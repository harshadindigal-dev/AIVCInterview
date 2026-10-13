"""Export human feedback as labelled records in the evaluation JSONL format.

    python -m scripts.export_feedback [--out data/feedback_export.jsonl] [--include-seed]

Each reassignment or misclassification becomes a record whose gold label is
what the human chose. Accepts become records confirming the model's labels.
This is how production feedback turns into new evaluation data. Review the
output before adding it to the eval set: a reassignment can be about workload,
not classification (the reason field usually says which).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sqlmodel import Session, select

from app.config import ROOT, get_domain, get_settings
from app.models import Enquiry, Feedback, make_engine


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "data" / "feedback_export.jsonl"))
    ap.add_argument("--include-seed", action="store_true", help="include demo feedback created at first start")
    args = ap.parse_args()
    domain = get_domain()
    lead_to_line = {v: k for k, v in domain.routing["routing_table"].items()}

    engine = make_engine(get_settings().database_url)
    out = []
    with Session(engine) as s:
        latest: dict[str, Feedback] = {}
        for f in s.exec(select(Feedback).order_by(Feedback.created_at)).all():
            if f.source == "seed" and not args.include_seed:
                continue
            latest[f.enquiry_id] = f  # the final human decision wins
        for eid, f in latest.items():
            e = s.get(Enquiry, eid)
            if e is None:
                continue
            if f.action == "misclassified":
                line, cx = f.new_value.get("service_line"), f.new_value.get("complexity")
            elif f.action == "reassign":
                line, cx = lead_to_line.get(f.new_value.get("destination_key")), f.old_value.get("complexity")
            else:
                line, cx = f.old_value.get("service_line"), f.old_value.get("complexity")
            out.append({
                "id": f"FB-{e.id}", "submitted_at": e.submitted_at.isoformat() + "Z", "name": e.name,
                "email": e.email, "company_name": e.company_name, "industry": e.industry,
                "company_size": e.company_size, "stated_urgency": e.stated_urgency, "description": e.description,
                "gold": {"case_type": "production_feedback", "primary_service_line": line,
                         "secondary_service_lines": [], "complexity": cx, "urgency": e.inferred_urgency,
                         "should_review": f.old_value.get("destination_type") == "review",
                         "duplicate_of": None,
                         "notes": f"From feedback action '{f.action}' at {f.created_at:%Y-%m-%d %H:%M}"
                                  + (f": {f.reason}" if f.reason else "") + ". Needs human review before use."},
            })
    Path(args.out).write_text("".join(json.dumps(r) + "\n" for r in out))
    print(f"wrote {len(out)} records to {args.out}")


if __name__ == "__main__":
    main()
