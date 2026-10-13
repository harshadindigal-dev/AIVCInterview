"""Classification: three LLM runs, schema validation with one retry, agreement check.

We never ask the model for a confidence score. Agreement across runs is the
uncertainty signal: run 0 is the primary result, runs 1-2 are sampled at a
higher temperature (or at the provider default when the model doesn't accept
temperature) and only vote.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from pydantic import ValidationError

from app.config import Domain
from app.llm.client import LLMClient, LLMError
from app.llm.prompts import PROMPT_VERSION, build_system_prompt, build_user_message
from app.schemas import TriageResult, triage_json_schema

N_RUNS = 3
PRIMARY_TEMPERATURE = 0.0
SAMPLE_TEMPERATURE = 0.7
MAX_ATTEMPTS = 2  # one call + one retry


def agreement_text(votes: list[str | None], agreement: int) -> str:
    """'3/3 runs agreed' or '2/3 agreed: tax vs deals'."""
    n = len(votes)
    if agreement == n:
        return f"{n}/{n} runs agreed"
    others = [v or "failed" for v in votes[1:] if v != votes[0]]
    return f"{agreement}/{n} agreed: {votes[0] or 'failed'} vs {', '.join(dict.fromkeys(others))}"


@dataclass
class RunRecord:
    index: int
    temperature: float | None
    ok: bool
    attempts: list[dict[str, Any]] = field(default_factory=list)  # every attempt, including failed ones
    parsed: dict[str, Any] | None = None
    error: str | None = None

    @property
    def last_response(self) -> dict[str, Any] | None:
        for a in reversed(self.attempts):
            if a.get("response"):
                return a["response"]
        return None


@dataclass
class ClassificationOutcome:
    ok: bool
    model: str
    prompt_version: str
    runs: list[RunRecord]
    result: TriageResult | None = None
    error: str | None = None
    agreement_service_line: int = 0
    agreement_complexity: int = 0
    service_line_votes: list[str | None] = field(default_factory=list)
    complexity_votes: list[str | None] = field(default_factory=list)
    injection_flagged_any_run: bool = False
    model_used: str | None = None
    provider: str | None = None
    latency_ms: int = 0

    @property
    def input_tokens(self) -> int:
        return sum(a["response"]["input_tokens"] for r in self.runs for a in r.attempts if a.get("response"))

    @property
    def output_tokens(self) -> int:
        return sum(a["response"]["output_tokens"] for r in self.runs for a in r.attempts if a.get("response"))

    @property
    def cost_usd(self) -> float | None:
        costs = [a["response"]["cost_usd"] for r in self.runs for a in r.attempts if a.get("response")]
        known = [c for c in costs if c is not None]
        return sum(known) if known else None

    def disagreement_text(self) -> str:
        return agreement_text(self.service_line_votes, self.agreement_service_line)

    def to_audit(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "model": self.model,
            "model_used": self.model_used,
            "provider": self.provider,
            "prompt_version": self.prompt_version,
            "error": self.error,
            "agreement_service_line": self.agreement_service_line,
            "agreement_complexity": self.agreement_complexity,
            "service_line_votes": self.service_line_votes,
            "complexity_votes": self.complexity_votes,
            "injection_flagged_any_run": self.injection_flagged_any_run,
            "runs": [asdict(r) for r in self.runs],
        }


def _validate(content: str, domain: Domain) -> TriageResult:
    data = json.loads(content)
    return TriageResult.model_validate(
        data, context={"outcome_ids": domain.outcome_ids, "service_line_ids": domain.service_line_ids}
    )


def _one_run(
    *,
    index: int,
    temperature: float | None,
    client: LLMClient,
    model: str,
    system: str,
    user: str,
    schema: dict[str, Any],
    domain: Domain,
    fallback_models: list[str] | None,
    backoff_seconds: float,
    sleep: Callable[[float], None],
) -> tuple[RunRecord, TriageResult | None]:
    rec = RunRecord(index=index, temperature=temperature, ok=False)
    for attempt in range(1, MAX_ATTEMPTS + 1):
        entry: dict[str, Any] = {"attempt": attempt}
        try:
            resp = client.complete_json(
                model=model, system=system, user=user, schema=schema, schema_name="record_triage",
                temperature=temperature, fallback_models=fallback_models,
            )
        except LLMError as e:
            entry.update(error=str(e), kind="api_error", status=e.status, retryable=e.retryable)
            rec.attempts.append(entry)
            rec.error = str(e)
            if attempt < MAX_ATTEMPTS and e.retryable:
                sleep(backoff_seconds)
            continue
        entry["response"] = resp.to_dict()
        try:
            result = _validate(resp.content, domain)
        except (json.JSONDecodeError, ValidationError) as e:
            entry.update(error=f"invalid output: {str(e)[:500]}", kind="validation_error")
            rec.attempts.append(entry)
            rec.error = entry["error"]
            continue
        rec.attempts.append(entry)
        rec.ok, rec.error, rec.parsed = True, None, result.model_dump()
        return rec, result
    return rec, None


def classify(
    *,
    client: LLMClient,
    domain: Domain,
    model: str,
    enquiry: dict[str, str],
    fallback_models: list[str] | None = None,
    backoff_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    n_runs: int = N_RUNS,
) -> ClassificationOutcome:
    """`enquiry` holds the form fields (name, email, company_name, industry,
    company_size, stated_urgency, description)."""
    started = time.perf_counter()
    system = build_system_prompt(domain)
    user = build_user_message(**enquiry)
    schema = triage_json_schema(domain.service_line_ids, domain.outcome_ids)

    if client.supports_temperature(model):
        temps: list[float | None] = [PRIMARY_TEMPERATURE] + [SAMPLE_TEMPERATURE] * (n_runs - 1)
    else:
        temps = [None] * n_runs

    common = dict(client=client, model=model, system=system, user=user, schema=schema, domain=domain,
                  fallback_models=fallback_models, backoff_seconds=backoff_seconds, sleep=sleep)
    with ThreadPoolExecutor(max_workers=n_runs) as pool:
        futures = [pool.submit(_one_run, index=i, temperature=t, **common) for i, t in enumerate(temps)]
        pairs = [f.result() for f in futures]

    runs = [p[0] for p in pairs]
    results = [p[1] for p in pairs]
    primary = results[0]
    out = ClassificationOutcome(
        ok=primary is not None,
        model=model,
        prompt_version=PROMPT_VERSION,
        runs=runs,
        result=primary,
        service_line_votes=[r.primary_service_line if r else None for r in results],
        complexity_votes=[r.complexity if r else None for r in results],
        injection_flagged_any_run=any(r is not None and "possible_injection" in r.flags for r in results),
        latency_ms=int((time.perf_counter() - started) * 1000),
    )
    resp0 = runs[0].last_response
    if resp0:
        out.model_used, out.provider = resp0["model_used"], resp0["provider"]
    if primary is None:
        out.error = f"classification_failed: {runs[0].error}"
        return out
    out.agreement_service_line = sum(v == primary.primary_service_line for v in out.service_line_votes)
    out.agreement_complexity = sum(v == primary.complexity for v in out.complexity_votes)
    return out
