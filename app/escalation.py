"""Acknowledgement deadlines: the fallback for a misroute nobody notices.

A routed enquiry that its recipient has neither accepted nor reassigned within
the configured hours (shorter for urgent work) is pulled back into the review
queue, with the reason recorded. Runs periodically in the app; a scheduler or
queue worker would call the same function in production.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.config import Domain
from app.models import Enquiry, Feedback, Notification, PipelineRun, utcnow

log = logging.getLogger("app")
_LEVEL = {"low": 0, "normal": 1, "high": 2}


def _urgency(e: Enquiry) -> str:
    levels = [u for u in (e.stated_urgency, e.inferred_urgency) if u in _LEVEL]
    return max(levels, key=_LEVEL.get) if levels else "normal"


def escalate_unacknowledged(session: Session, domain: Domain, base_url: str,
                            now: datetime | None = None) -> list[str]:
    hours = ((domain.routing.get("fallback") or {}).get("acknowledgement_hours")) or {}
    if not hours:
        return []
    now = now or utcnow()
    escalated = []
    rows = session.exec(select(Enquiry).where(Enquiry.status == "auto_routed")
                        .where(Enquiry.destination_type.in_(["lead", "partner"]))).all()
    q = domain.routing["review_queue"]
    for e in rows:
        run = session.get(PipelineRun, e.latest_run_id) if e.latest_run_id else None
        routed_at = run.created_at if run else e.created_at
        urgency = _urgency(e)
        limit = hours.get(urgency)
        if not limit or now - routed_at < timedelta(hours=limit):
            continue
        who = e.destination_name
        reason = f"Not acknowledged by {who} within {limit:g} hours ({urgency} urgency)"
        old = {"destination_type": e.destination_type, "destination_key": e.destination_key,
               "destination_name": e.destination_name, "status": e.status}
        e.status, e.destination_type, e.destination_key, e.destination_name = "in_review", "review", "review", "Review queue"
        e.review_reasons = [reason]
        session.add(Feedback(enquiry_id=e.id, action="escalated", old_value=old,
                             new_value={"destination_type": "review", "status": "in_review"},
                             reason=reason, source="system"))
        url = f"{base_url.rstrip('/')}/enquiries/{e.id}"
        session.add(Notification(enquiry_id=e.id, channel="slack", kind="review",
                                 recipient_name=q.get("name", "Review queue"), recipient_address=q.get("slack", ""),
                                 subject=f"Escalated: {e.company_name} not picked up",
                                 body=f"{e.summary or ''}\n\nWhy it needs review:\n- {reason}\n\nOpen: {url}"))
        session.add(e)
        escalated.append(e.id)
    if escalated:
        session.commit()
        log.info(json.dumps({"event": "escalation", "escalated": escalated}))
    return escalated
