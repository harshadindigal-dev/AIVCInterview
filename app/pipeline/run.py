"""Orchestrates intake -> enrich -> classify -> route -> notify and writes the audit log.

`run_pipeline` is a plain callable so a job queue can wrap it later without changes.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlmodel import Session

from app.config import Domain
from app.llm.client import LLMClient
from app.llm.prompts import PROMPT_VERSION
from app.models import Enquiry, PipelineRun, utcnow
from app.pipeline.classify import ClassificationOutcome, classify
from app.pipeline.enrich import CompanyData, CRMClient, enrich
from app.pipeline.intake import find_duplicate, normalise, text_hash
from app.pipeline.notify import build_notifications
from app.pipeline.route import ClassificationFacts, RoutingDecision, route
from app.schemas import EnquiryIn

log = logging.getLogger("pipeline")
STEPS = ["intake", "enrich", "classify", "route", "notify"]
StepCallback = Callable[[str, str, dict[str, Any]], None]  # (step, status, info)


@dataclass
class PipelineDeps:
    client: LLMClient
    domain: Domain
    model: str
    crm: CRMClient
    company_data: CompanyData
    fallback_models: list[str] = field(default_factory=list)
    backoff_seconds: float = 2.0
    base_url: str = "http://localhost:8000"
    sleep: Callable[[float], None] = time.sleep


def _log(step: str, enquiry_id: str, **fields: Any) -> None:
    log.info(json.dumps({"event": "pipeline_step", "step": step, "enquiry_id": enquiry_id, **fields}, default=str))


def new_enquiry_id() -> str:
    return "ENQ-" + uuid.uuid4().hex[:6].upper()


def create_enquiry(session: Session, data: EnquiryIn, deps: PipelineDeps, *, enquiry_id: str | None = None,
                   source: str = "form") -> Enquiry:
    """Intake step: normalise, hash, persist (status=processing). Fast; called in the request."""
    enq = Enquiry(
        id=enquiry_id or new_enquiry_id(),
        submitted_at=(data.submitted_at.replace(tzinfo=None) if data.submitted_at else utcnow()),
        name=data.name.strip(), email=data.email.strip(), company_name=data.company_name.strip(),
        industry=data.industry, company_size=data.company_size, stated_urgency=data.stated_urgency,
        description=data.description,
        description_normalised=normalise(data.description),
        text_hash=text_hash(data.email, data.description),
        source=source,
    )
    session.add(enq)
    session.commit()
    return enq


def process_enquiry(session: Session, enq: Enquiry, deps: PipelineDeps,
                    on_step: StepCallback | None = None) -> Enquiry:
    """Runs dedupe + enrich + classify + route + notify for an already-created enquiry."""
    domain = deps.domain
    emit = on_step or (lambda *a: None)
    latency: dict[str, int] = {}
    audit = PipelineRun(enquiry_id=enq.id, prompt_version=PROMPT_VERSION, model=deps.model)

    # -- intake: dedupe ------------------------------------------------------
    t = time.perf_counter()
    dup = find_duplicate(session, text_hash_=enq.text_hash, submitted_at=enq.submitted_at,
                         window_days=domain.routing["dedupe_window_days"], exclude_id=enq.id)
    latency["intake"] = int((time.perf_counter() - t) * 1000)
    if dup:
        enq.duplicate_of, enq.status = dup.id, "duplicate"
        enq.destination_type, enq.destination_key = "duplicate", dup.id
        enq.destination_name = f"Duplicate of {dup.id}"
        enq.service_line, enq.complexity, enq.summary = dup.service_line, dup.complexity, dup.summary
        audit.final_destination = f"duplicate:{dup.id}"
        audit.routing = {"destination_type": "duplicate", "destination_name": enq.destination_name,
                         "deciding_rule": "dedupe", "review_reasons": [], "cc": [], "notes": [],
                         "trace": [{"rule": "dedupe", "description": "Same email and near-identical text within "
                                    f"{domain.routing['dedupe_window_days']} days -> link and skip triage",
                                    "fired": True, "deciding": True,
                                    "detail": f"Matches {dup.id} submitted {dup.submitted_at:%d %b %H:%M}"}]}
        audit.latency_ms = latency
        session.add(audit)
        session.commit()
        enq.latest_run_id = audit.id
        session.add(enq)
        session.commit()
        _log("intake", enq.id, duplicate_of=dup.id, latency_ms=latency["intake"])
        emit("intake", "done", {"duplicate_of": dup.id})
        for s in STEPS[1:]:
            emit(s, "skipped", {})
        return enq
    _log("intake", enq.id, duplicate_of=None, latency_ms=latency["intake"])
    emit("intake", "done", {})

    # -- enrich --------------------------------------------------------------
    emit("enrich", "running", {})
    t = time.perf_counter()
    try:
        enrichment = enrich(email=enq.email, company_name=enq.company_name, company_size=enq.company_size,
                            industry=enq.industry, crm=deps.crm, company_data=deps.company_data)
    except Exception as e:  # enrichment is best-effort; never drop the enquiry
        enrichment = {"error": f"{type(e).__name__}: {e}", "existing_client": False, "conflict_flag": False}
    latency["enrich"] = int((time.perf_counter() - t) * 1000)
    audit.enrichment = enrichment
    _log("enrich", enq.id, existing_client=enrichment.get("existing_client"),
         conflict_flag=enrichment.get("conflict_flag"), latency_ms=latency["enrich"])
    emit("enrich", "done", {})

    # -- classify ------------------------------------------------------------
    emit("classify", "running", {})
    t = time.perf_counter()
    form = dict(name=enq.name, email=enq.email, company_name=enq.company_name, industry=enq.industry,
                company_size=enq.company_size, stated_urgency=enq.stated_urgency, description=enq.description)
    try:
        outcome = classify(client=deps.client, domain=domain, model=deps.model, enquiry=form,
                           fallback_models=deps.fallback_models, backoff_seconds=deps.backoff_seconds,
                           sleep=deps.sleep)
    except Exception as e:
        outcome = ClassificationOutcome(ok=False, model=deps.model, prompt_version=PROMPT_VERSION, runs=[],
                                        error=f"classification_failed: {type(e).__name__}: {e}")
    latency["classify"] = int((time.perf_counter() - t) * 1000)
    audit.classification = outcome.to_audit()
    audit.parsed = outcome.result.model_dump() if outcome.result else None
    audit.classification_failed = not outcome.ok
    audit.model_used, audit.provider = outcome.model_used, outcome.provider
    audit.input_tokens, audit.output_tokens, audit.cost_usd = outcome.input_tokens, outcome.output_tokens, outcome.cost_usd
    if outcome.result:
        r = outcome.result
        enq.service_line, enq.secondary_service_lines = r.primary_service_line, r.secondary_service_lines
        enq.complexity, enq.inferred_urgency, enq.summary, enq.flags = r.complexity, r.inferred_urgency, r.summary, r.flags
    _log("classify", enq.id, ok=outcome.ok, model=deps.model, model_used=outcome.model_used,
         provider=outcome.provider, agreement_service_line=outcome.agreement_service_line,
         agreement_complexity=outcome.agreement_complexity, input_tokens=outcome.input_tokens,
         output_tokens=outcome.output_tokens, cost_usd=outcome.cost_usd, error=outcome.error,
         latency_ms=latency["classify"])
    emit("classify", "done" if outcome.ok else "failed", {"error": outcome.error})

    # -- route ---------------------------------------------------------------
    emit("route", "running", {})
    t = time.perf_counter()
    decision: RoutingDecision = route(classification=ClassificationFacts.from_outcome(outcome),
                                      enrichment=enrichment, stated_urgency=enq.stated_urgency, domain=domain)
    latency["route"] = int((time.perf_counter() - t) * 1000)
    enq.destination_type, enq.destination_key = decision.destination_type, decision.destination_key
    enq.destination_name = decision.destination_name
    enq.review_reasons = decision.review_reasons
    enq.status = {"review": "in_review", "archive": "archived"}.get(decision.destination_type, "auto_routed")
    audit.routing = decision.to_dict()
    audit.final_destination = f"{decision.destination_type}:{decision.destination_key}"
    _log("route", enq.id, destination=audit.final_destination, deciding_rule=decision.deciding_rule,
         review_reasons=decision.review_reasons, latency_ms=latency["route"])
    emit("route", "done", {"destination": decision.destination_name})

    # -- notify --------------------------------------------------------------
    emit("notify", "running", {})
    t = time.perf_counter()
    notes = build_notifications(enq, decision, domain, deps.base_url)
    for n in notes:
        session.add(n)
    latency["notify"] = int((time.perf_counter() - t) * 1000)
    _log("notify", enq.id, notifications=len(notes), latency_ms=latency["notify"])

    audit.latency_ms = latency
    session.add(audit)
    session.commit()
    enq.latest_run_id = audit.id
    session.add(enq)
    session.commit()
    emit("notify", "done", {"count": len(notes)})
    return enq


def run_pipeline(session: Session, data: EnquiryIn, deps: PipelineDeps, *, enquiry_id: str | None = None,
                 source: str = "form", on_step: StepCallback | None = None) -> Enquiry:
    enq = create_enquiry(session, data, deps, enquiry_id=enquiry_id, source=source)
    return process_enquiry(session, enq, deps, on_step=on_step)
