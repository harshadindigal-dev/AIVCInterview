"""First-start seeding: run the labelled dataset through the live pipeline.

Dates are shifted so the newest record is one day old (keeps the 7/30-day
metrics populated whenever the app is started). A few human actions are also
seeded, clearly marked source="seed", so the metrics page has reassignment data:
- enquiries the model got wrong (vs gold) are marked misclassified with the gold labels;
- two close calls are reassigned the way a reviewer plausibly would;
- the oldest correctly routed enquiries are accepted.
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, func, select

from app.evaluation import FORM_FIELDS, load_dataset
from app.feedback import accept, mark_misclassified, reassign
from app.models import Enquiry, utcnow
from app.pipeline.intake import text_hash
from app.pipeline.run import PipelineDeps, create_enquiry, process_enquiry
from app.schemas import EnquiryIn

log = logging.getLogger("seed")

status: dict[str, Any] = {"state": "idle", "done": 0, "total": 0, "error": None}
_lock = threading.Lock()

DEMO_REASSIGNMENTS = {
    "E027": ("people", "Mostly an org redesign and consultation; ERP is the smaller piece."),
    "E046": ("risk_reg", "They need readiness work first; assurance comes later."),
}


def needs_seed(engine) -> bool:
    with Session(engine) as s:
        return (s.exec(select(func.count()).select_from(Enquiry)).one() or 0) == 0


def _shifted(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    times = [datetime.fromisoformat(r["submitted_at"].replace("Z", "+00:00")).replace(tzinfo=None) for r in records]
    delta = (utcnow() - timedelta(days=1)) - max(times)
    out = []
    for r, t in zip(records, times):
        out.append({**r, "submitted_at": (t + delta).isoformat()})
    return out


def seed(engine, deps: PipelineDeps, workers: int = 6) -> None:
    records = sorted(_shifted(load_dataset()), key=lambda r: r["submitted_at"])
    with _lock:
        status.update(state="running", done=0, total=len(records), error=None)
    seen, first, repeats = set(), [], []
    for r in records:
        h = text_hash(r["email"], r["description"])
        (repeats if h in seen else first).append(r)
        seen.add(h)

    def one(rec: dict[str, Any]) -> None:
        with Session(engine, expire_on_commit=False) as s:
            data = EnquiryIn(**{k: rec[k] for k in FORM_FIELDS}, submitted_at=rec["submitted_at"])
            enq = create_enquiry(s, data, deps, enquiry_id=rec["id"], source="seed")
            process_enquiry(s, enq, deps)
        with _lock:
            status["done"] += 1

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, first))
        for r in repeats:
            one(r)
        _seed_feedback(engine, deps, records)
        with _lock:
            status["state"] = "done"
        log.info(json.dumps({"event": "seed_complete", "records": len(records)}))
    except Exception as e:  # keep the app up; the banner shows the error
        log.exception("seeding failed")
        with _lock:
            status.update(state="error", error=f"{type(e).__name__}: {e}")


def _seed_feedback(engine, deps: PipelineDeps, records: list[dict[str, Any]]) -> None:
    domain = deps.domain
    gold = {r["id"]: r["gold"] for r in records}
    with Session(engine, expire_on_commit=False) as s:
        enqs = {e.id: e for e in s.exec(select(Enquiry).where(Enquiry.source == "seed")).all()}
        handled = set()
        for eid, e in enqs.items():
            g = gold.get(eid)
            if not g or e.duplicate_of or e.destination_type in ("archive", "review"):
                continue
            if g["primary_service_line"] in domain.service_line_ids and e.service_line != g["primary_service_line"]:
                mark_misclassified(s, domain, e, g["primary_service_line"], g["complexity"] or e.complexity,
                                   "Seeded correction from gold label", deps.base_url, source="seed")
                handled.add(eid)
        for eid, (lead, reason) in DEMO_REASSIGNMENTS.items():
            e = enqs.get(eid)
            if e and eid not in handled and e.destination_type == "lead" and e.destination_key != lead:
                reassign(s, domain, e, lead, reason, deps.base_url, source="seed")
                handled.add(eid)
        routed = sorted((e for e in enqs.values() if e.status == "auto_routed" and e.id not in handled),
                        key=lambda e: e.submitted_at)
        for e in routed[:12]:
            accept(s, e, source="seed")


def start_background_seed(engine, deps: PipelineDeps) -> threading.Thread:
    t = threading.Thread(target=seed, args=(engine, deps), name="seed", daemon=True)
    t.start()
    return t
