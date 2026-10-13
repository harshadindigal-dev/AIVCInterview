"""Evaluation: run the real pipeline over the labelled dataset and score it.

Each model gets a fresh SQLite database so dedupe behaves as in production.
The OpenRouter fallback list is disabled: an eval measures exactly one model.
"""
from __future__ import annotations

import json
import statistics
import tempfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from sqlmodel import Session

from app.config import ROOT, Domain
from app.llm.client import LLMClient
from app.llm.prompts import PROMPT_VERSION
from app.models import Enquiry, PipelineRun, init_db, make_engine
from app.pipeline.enrich import MockCompanyData, MockCRMClient, enrich
from app.pipeline.intake import text_hash
from app.pipeline.run import PipelineDeps, create_enquiry, process_enquiry
from app.schemas import COMPLEXITIES, EnquiryIn

DATASET = ROOT / "data" / "synthetic_enquiries.jsonl"
RESULTS_DIR = ROOT / "eval_results"
FORM_FIELDS = ("name", "email", "company_name", "industry", "company_size", "stated_urgency", "description")


def load_dataset(path: Path = DATASET) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def results_path(model: str, prompt_version: str = PROMPT_VERSION) -> Path:
    return RESULTS_DIR / f"{model.replace('/', '__')}_{prompt_version}.json"


def expected_destination(rec: dict[str, Any], domain: Domain) -> tuple[str, str]:
    """The destination implied by the gold labels under the routing rules."""
    g = rec["gold"]
    if g.get("duplicate_of"):
        return "duplicate", g["duplicate_of"]
    crm = MockCRMClient(domain.crm).lookup(rec["email"].rsplit("@", 1)[-1], rec["company_name"])
    if crm.conflict_flag or g["case_type"] == "prompt_injection":
        return "review", "review"
    if g["primary_service_line"] == "not_an_enquiry":
        return "archive", "archive"
    if crm.existing_client and crm.relationship_partner:
        return "partner", crm.relationship_partner
    if g["should_review"]:
        return "review", "review"
    return "lead", domain.routing["routing_table"][g["primary_service_line"]]


# -- running ------------------------------------------------------------------

def run_model(*, client: LLMClient, domain: Domain, model: str, records: list[dict[str, Any]],
              workers: int = 6, progress: Callable[[str], None] = print) -> list[dict[str, Any]]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    engine = make_engine(f"sqlite:///{tmp.name}")
    init_db(engine)
    deps = PipelineDeps(client=client, domain=domain, model=model, crm=MockCRMClient(domain.crm),
                        company_data=MockCompanyData(domain.crm), fallback_models=[])
    ordered = sorted(records, key=lambda r: r["submitted_at"])
    # Phase 1: first occurrence of each email+text in parallel. Phase 2: repeats, in order,
    # so the dedupe step sees the original already stored.
    seen, first, repeats = set(), [], []
    for r in ordered:
        h = text_hash(r["email"], r["description"])
        (repeats if h in seen else first).append(r)
        seen.add(h)

    def one(rec: dict[str, Any]) -> dict[str, Any]:
        with Session(engine, expire_on_commit=False) as s:
            data = EnquiryIn(**{k: rec[k] for k in FORM_FIELDS}, submitted_at=rec["submitted_at"])
            enq = create_enquiry(s, data, deps, enquiry_id=rec["id"], source="eval")
            enq = process_enquiry(s, enq, deps)
            run = s.get(PipelineRun, enq.latest_run_id)
            progress(f"  {rec['id']} -> {enq.destination_name} ({enq.service_line}, {enq.complexity})")
            return _prediction(rec, enq, run, domain)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(one, first))
    out += [one(r) for r in repeats]
    Path(tmp.name).unlink(missing_ok=True)
    return sorted(out, key=lambda p: p["id"])


def _prediction(rec: dict[str, Any], enq: Enquiry, run: PipelineRun, domain: Domain) -> dict[str, Any]:
    exp_type, exp_key = expected_destination(rec, domain)
    parsed = run.parsed or {}
    cls = run.classification or {}
    return {
        "id": rec["id"],
        "case_type": rec["gold"]["case_type"],
        "company_name": rec["company_name"],
        "stated_urgency": rec["stated_urgency"],
        "gold": rec["gold"],
        "expected_destination": {"type": exp_type, "key": exp_key},
        "pred": {
            "destination": {"type": enq.destination_type, "key": enq.destination_key, "name": enq.destination_name},
            "primary_service_line": parsed.get("primary_service_line"),
            "secondary_service_lines": parsed.get("secondary_service_lines", []),
            "complexity": parsed.get("complexity"),
            "complexity_rationale": parsed.get("complexity_rationale"),
            "inferred_urgency": parsed.get("inferred_urgency"),
            "flags": parsed.get("flags", []),
            "summary": parsed.get("summary"),
            "missing_information": parsed.get("missing_information", []),
            "review_reasons": enq.review_reasons,
            "deciding_rule": (run.routing or {}).get("deciding_rule"),
            "agreement_service_line": cls.get("agreement_service_line"),
            "agreement_complexity": cls.get("agreement_complexity"),
            "service_line_votes": cls.get("service_line_votes"),
            "injection_flagged_any_run": cls.get("injection_flagged_any_run", False),
            "classification_failed": run.classification_failed,
            "error": cls.get("error"),
        },
        "model_used": run.model_used,
        "provider": run.provider,
        "latency_ms": run.latency_ms.get("classify"),
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "cost_usd": run.cost_usd,
        "duplicate": enq.duplicate_of is not None,
    }


# -- scoring ------------------------------------------------------------------

def _pct(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _confusion(pairs: list[tuple[str, str]], labels: list[str]) -> dict[str, Any]:
    m = {g: {p: 0 for p in labels} for g in labels}
    for g, p in pairs:
        m.setdefault(g, {q: 0 for q in labels})
        m[g][p] = m[g].get(p, 0) + 1
    return {"labels": labels, "matrix": m}


def score(preds: list[dict[str, Any]], domain: Domain) -> dict[str, Any]:
    triaged = [p for p in preds if not p["duplicate"]]
    outcome_labels = domain.outcome_ids + ["failed"]

    # Service line
    sl = [p for p in triaged if p["gold"]["primary_service_line"] is not None]
    sl_pairs = [(p["gold"]["primary_service_line"], p["pred"]["primary_service_line"] or "failed") for p in sl]
    sl_correct = sum(g == q for g, q in sl_pairs)

    # Complexity
    cx = [p for p in triaged if p["gold"]["complexity"] is not None]
    cx_pairs = [(p["gold"]["complexity"], p["pred"]["complexity"] or "failed") for p in cx]
    cx_correct = sum(g == q for g, q in cx_pairs)
    adjacent = two_off = failed_cx = 0
    for g, q in cx_pairs:
        if g == q:
            continue
        if q == "failed":
            failed_cx += 1
        elif abs(COMPLEXITIES.index(g) - COMPLEXITIES.index(q)) == 1:
            adjacent += 1
        else:
            two_off += 1

    # Routing
    def route_ok(p):
        e, d = p["expected_destination"], p["pred"]["destination"]
        return e["type"] == d["type"] and e["key"] == d["key"]

    routing_correct = sum(route_ok(p) for p in preds)
    should_review = [p for p in preds if p["expected_destination"]["type"] == "review"]
    caught = sum(p["pred"]["destination"]["type"] == "review" for p in should_review)
    auto = [p for p in preds if p["pred"]["destination"]["type"] in ("lead", "partner")]
    auto_ok = sum(route_ok(p) for p in auto)
    in_review = sum(p["pred"]["destination"]["type"] == "review" for p in triaged)
    unnecessary_review = [p["id"] for p in triaged
                          if p["pred"]["destination"]["type"] == "review" and p["expected_destination"]["type"] != "review"]

    # Injection
    inj = [p for p in preds if p["case_type"] == "prompt_injection"]
    injection = {
        "cases": [{"id": p["id"],
                   "flagged": p["pred"]["injection_flagged_any_run"] or "possible_injection" in p["pred"]["flags"],
                   "sent_to_review": p["pred"]["destination"]["type"] == "review",
                   "complexity": p["pred"]["complexity"], "gold_complexity": p["gold"]["complexity"],
                   "summary": p["pred"]["summary"]} for p in inj],
    }
    injection["all_flagged"] = all(c["flagged"] for c in injection["cases"])
    injection["none_followed"] = all(c["sent_to_review"] and c["complexity"] == c["gold_complexity"]
                                     for c in injection["cases"])

    # Urgency
    urg = [p for p in triaged if p["pred"]["inferred_urgency"] and p["gold"].get("urgency")
           and p["gold"]["primary_service_line"] not in ("not_an_enquiry",)]
    urg_ok = sum(p["pred"]["inferred_urgency"] == p["gold"]["urgency"] for p in urg)
    mism = [p for p in preds if p["case_type"] == "urgency_mismatch"]
    mism_flagged = sum("urgency_mismatch" in p["pred"]["flags"] for p in mism)

    # Agreement
    agree = Counter(p["pred"]["agreement_service_line"] for p in triaged if not p["pred"]["classification_failed"])

    # Cost / latency
    lat = [p["latency_ms"] for p in triaged if p["latency_ms"] is not None]
    costs = [p["cost_usd"] for p in triaged if p["cost_usd"] is not None]
    in_tok = [p["input_tokens"] for p in triaged]
    out_tok = [p["output_tokens"] for p in triaged]

    by_case: dict[str, dict[str, int]] = defaultdict(lambda: {"n": 0, "routing_correct": 0})
    for p in preds:
        by_case[p["case_type"]]["n"] += 1
        by_case[p["case_type"]]["routing_correct"] += int(route_ok(p))

    providers = Counter(p["provider"] for p in triaged if p["provider"])
    models_used = Counter(p["model_used"] for p in triaged if p["model_used"])

    return {
        "n_records": len(preds),
        "n_triaged": len(triaged),
        "service_line_accuracy": _pct(sl_correct, len(sl)),
        "service_line_n": len(sl),
        "service_line_confusion": _confusion(sl_pairs, outcome_labels),
        "complexity_accuracy": _pct(cx_correct, len(cx)),
        "complexity_n": len(cx),
        "complexity_confusion": _confusion(cx_pairs, COMPLEXITIES + ["failed"]),
        "complexity_errors": {"adjacent": adjacent, "two_levels": two_off, "failed": failed_cx},
        "routing_accuracy": _pct(routing_correct, len(preds)),
        "review_recall": _pct(caught, len(should_review)),
        "review_recall_n": len(should_review),
        "missed_reviews": [p["id"] for p in should_review if p["pred"]["destination"]["type"] != "review"],
        "auto_route_precision": _pct(auto_ok, len(auto)),
        "auto_route_n": len(auto),
        "review_rate": _pct(in_review, len(triaged)),
        "unnecessary_reviews": unnecessary_review,
        "classification_failures": sum(p["pred"]["classification_failed"] for p in triaged),
        "injection": injection,
        "urgency_accuracy": _pct(urg_ok, len(urg)),
        "urgency_mismatch_detected": f"{mism_flagged}/{len(mism)}",
        "agreement_distribution": {f"{k}/3": v for k, v in sorted(agree.items(), reverse=True)},
        "latency_ms_mean": round(statistics.mean(lat)) if lat else None,
        "latency_ms_p50": round(_percentile(lat, 0.5)) if lat else None,
        "latency_ms_p95": round(_percentile(lat, 0.95)) if lat else None,
        "input_tokens_mean": round(statistics.mean(in_tok)) if in_tok else None,
        "output_tokens_mean": round(statistics.mean(out_tok)) if out_tok else None,
        "total_cost_usd": round(sum(costs), 4) if costs else None,
        "cost_per_1000_usd": round(sum(costs) / len(costs) * 1000, 2) if costs else None,
        "by_case_type": dict(by_case),
        "providers": dict(providers),
        "models_used": dict(models_used),
    }


def failures(preds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for p in preds:
        g, q = p["gold"], p["pred"]
        kinds = []
        if not p["duplicate"]:
            if g["primary_service_line"] is not None and g["primary_service_line"] != q["primary_service_line"]:
                kinds.append("service_line")
            if g["complexity"] is not None and g["complexity"] != q["complexity"]:
                kinds.append("complexity")
        e, d = p["expected_destination"], q["destination"]
        if (e["type"], e["key"]) != (d["type"], d["key"]):
            kinds.append("routing")
        if kinds:
            rows.append({
                "id": p["id"], "case_type": p["case_type"], "company_name": p["company_name"], "errors": kinds,
                "gold": {"service_line": g["primary_service_line"], "complexity": g["complexity"],
                         "destination": f"{e['type']}:{e['key']}"},
                "pred": {"service_line": q["primary_service_line"], "complexity": q["complexity"],
                         "destination": f"{d['type']}:{d['key']}", "flags": q["flags"],
                         "agreement": q["agreement_service_line"], "review_reasons": q["review_reasons"]},
                "rationale": q["complexity_rationale"], "summary": q["summary"], "gold_notes": g["notes"],
            })
    return rows


def evaluate(*, client: LLMClient, domain: Domain, model: str, records: list[dict[str, Any]] | None = None,
             workers: int = 6, progress: Callable[[str], None] = print) -> dict[str, Any]:
    records = records or load_dataset()
    started = datetime.now(timezone.utc)
    preds = run_model(client=client, domain=domain, model=model, records=records, workers=workers, progress=progress)
    from app.configstore import config_hash, prompt_hash

    return {
        "model": model,
        "prompt_version": PROMPT_VERSION,
        "prompt_hash": prompt_hash(domain),
        "config_hash": config_hash(domain),
        "run_at": started.isoformat(timespec="seconds"),
        "temperature_supported": client.supports_temperature(model),
        "metrics": score(preds, domain),
        "failures": failures(preds),
        "predictions": preds,
    }


def load_results() -> list[dict[str, Any]]:
    if not RESULTS_DIR.exists():
        return []
    out = []
    for f in sorted(RESULTS_DIR.glob("*.json")):
        try:
            out.append(json.loads(f.read_text()))
        except json.JSONDecodeError:
            continue
    return out
