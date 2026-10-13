"""Day-one production metrics, computed from the database."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import timedelta
from typing import Any

from sqlmodel import Session, select

from app.config import Domain
from app.feedback import REASSIGN_REASONS, is_model_error
from app.models import Enquiry, Feedback, PipelineRun, utcnow


def _pct(n: int, d: int) -> float | None:
    return round(n / d, 4) if d else None


def _percentile(xs: list[int], p: float) -> int | None:
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo))


def compute(session: Session, domain: Domain) -> dict[str, Any]:
    enqs = session.exec(select(Enquiry)).all()
    runs = session.exec(select(PipelineRun)).all()
    fb = session.exec(select(Feedback)).all()
    now = utcnow()

    first_run: dict[str, PipelineRun] = {}
    for r in sorted(runs, key=lambda r: r.id or 0):
        first_run.setdefault(r.enquiry_id, r)

    triaged = [e for e in enqs if e.duplicate_of is None and e.status != "processing"]
    routable = [e for e in triaged if (first_run.get(e.id) and
                                       not (first_run[e.id].final_destination or "").startswith("archive"))]

    # Reassignment, attributed to the originally predicted line. "Model error" counts only
    # reassignments for a wrong service line plus Mark misclassified; capacity, relationship
    # and conflict moves are tracked separately so they don't look like classifier drift.
    changed: set[str] = set()
    model_errors: set[str] = set()
    for f in fb:
        if f.action in ("reassign", "misclassified"):
            changed.add(f.enquiry_id)
            if is_model_error(f):
                model_errors.add(f.enquiry_id)
    by_line_total: Counter = Counter()
    by_line_changed: Counter = Counter()
    by_line_errors: Counter = Counter()
    for e in routable:
        run = first_run[e.id]
        line = (run.parsed or {}).get("primary_service_line") or "unclassified"
        by_line_total[line] += 1
        by_line_changed[line] += e.id in changed
        by_line_errors[line] += e.id in model_errors
    reassign_by_line = [
        {"line": line, "name": domain.line_name(line), "total": by_line_total[line], "changed": by_line_changed[line],
         "errors": by_line_errors[line], "rate": _pct(by_line_changed[line], by_line_total[line]),
         "error_rate": _pct(by_line_errors[line], by_line_total[line])}
        for line in domain.outcome_ids + ["unclassified"] if by_line_total[line]
    ]
    reassign_reasons = Counter(REASSIGN_REASONS.get(f.reason_category or "", "Not recorded")
                               for f in fb if f.action == "reassign")

    went_to_review = [e for e in triaged if first_run.get(e.id) and
                      (first_run[e.id].final_destination or "").startswith("review")]
    review_reasons = Counter()
    for e in went_to_review:
        for r in e.review_reasons or []:
            key = r.split(" (")[0] if r.startswith("Flag:") else r.split(":")[0].split(" (")[0]
            review_reasons[key] += 1

    def distribution(days: int) -> list[dict[str, Any]]:
        cutoff = now - timedelta(days=days)
        c = Counter(e.service_line or "unclassified" for e in triaged if e.submitted_at >= cutoff)
        total = sum(c.values())
        return [{"line": k, "name": domain.line_name(k), "count": v, "share": _pct(v, total)}
                for k, v in c.most_common()]

    classify_lat = [r.latency_ms.get("classify") for r in runs if r.latency_ms.get("classify") is not None]
    total_lat = [sum(r.latency_ms.values()) for r in runs if r.latency_ms]
    costs = [r.cost_usd for r in runs if r.cost_usd is not None]
    fb_counts = Counter(f.action for f in fb)
    agreement = Counter((r.classification or {}).get("agreement_service_line") for r in runs
                        if r.classification and r.classification.get("ok"))

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "enquiries": len(enqs),
        "triaged": len(triaged),
        "duplicates": sum(1 for e in enqs if e.duplicate_of),
        "reassignment_rate": _pct(len([e for e in routable if e.id in changed]), len(routable)),
        "model_error_rate": _pct(len([e for e in routable if e.id in model_errors]), len(routable)),
        "reassign_reasons": reassign_reasons.most_common(),
        "reassignment_by_line": reassign_by_line,
        "review_queue_rate": _pct(len(went_to_review), len(triaged)),
        "review_reasons": review_reasons.most_common(),
        "open_in_review": sum(1 for e in enqs if e.status == "in_review"),
        "distribution_7d": distribution(7),
        "distribution_30d": distribution(30),
        "classification_failure_rate": _pct(sum(r.classification_failed for r in runs
                                                if r.classification), sum(1 for r in runs if r.classification)),
        "latency_classify_p50_ms": _percentile(classify_lat, 0.5),
        "latency_classify_p95_ms": _percentile(classify_lat, 0.95),
        "latency_total_p50_ms": _percentile(total_lat, 0.5),
        "latency_total_p95_ms": _percentile(total_lat, 0.95),
        "mean_cost_usd": round(sum(costs) / len(costs), 4) if costs else None,
        "feedback_counts": dict(fb_counts),
        "feedback_seeded": sum(1 for f in fb if f.source == "seed"),
        "agreement_distribution": {f"{k}/3": v for k, v in sorted(agreement.items(), key=lambda kv: -(kv[0] or 0))},
        "providers": dict(Counter(r.provider for r in runs if r.provider)),
        "models_used": dict(Counter(r.model_used for r in runs if r.model_used)),
    }
