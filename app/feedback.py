"""Human actions on an enquiry. Each writes a Feedback row (the metrics page and
scripts/export_feedback.py read them)."""
from __future__ import annotations

from sqlmodel import Session

from app.config import Domain
from app.models import Enquiry, Feedback
from app.pipeline.notify import reassignment_notification


# Why a lead changed. Only a wrong service line is a model error; the others are
# workload or relationship decisions and must not count against the classifier.
REASSIGN_REASONS = {
    "wrong_service_line": "Wrong service line (model error)",
    "capacity": "Lead capacity or leave",
    "client_relationship": "Client relationship",
    "conflict": "Conflict or independence",
    "other": "Other",
}
MODEL_ERROR_REASONS = {"wrong_service_line"}


def is_model_error(f: Feedback) -> bool:
    return f.action == "misclassified" or (f.action == "reassign" and f.reason_category in MODEL_ERROR_REASONS)


def _snapshot(e: Enquiry) -> dict:
    return {"destination_type": e.destination_type, "destination_key": e.destination_key,
            "destination_name": e.destination_name, "service_line": e.service_line,
            "complexity": e.complexity, "status": e.status}


def accept(session: Session, enq: Enquiry, *, source: str = "user") -> Enquiry:
    """Accept the suggested routing. For a review item, the suggestion is the
    classified service line's lead."""
    old = _snapshot(enq)
    session.add(Feedback(enquiry_id=enq.id, action="accept", old_value=old, new_value=old, source=source))
    enq.status = "accepted"
    session.add(enq)
    session.commit()
    return enq


def suggested_lead(domain: Domain, enq: Enquiry) -> dict | None:
    return domain.lead_for(enq.service_line) if enq.service_line else None


def accept_suggestion_from_review(session: Session, domain: Domain, enq: Enquiry, base_url: str,
                                  *, source: str = "user") -> Enquiry:
    """In the review queue, 'Accept' means: route to the suggested lead."""
    lead = suggested_lead(domain, enq)
    if lead is None:
        return accept(session, enq, source=source)
    old = _snapshot(enq)
    enq.destination_type, enq.destination_key, enq.destination_name = "lead", lead["key"], lead["name"]
    enq.status = "accepted"
    session.add(Feedback(enquiry_id=enq.id, action="accept", old_value=old, new_value=_snapshot(enq),
                         reason="Accepted suggestion from review queue", source=source))
    session.add(reassignment_notification(enq, lead, "accepted from review queue", base_url))
    session.add(enq)
    session.commit()
    return enq


def reassign(session: Session, domain: Domain, enq: Enquiry, lead_key: str, reason: str | None, base_url: str,
             *, reason_category: str = "other", source: str = "user") -> Enquiry:
    if reason_category not in REASSIGN_REASONS:
        raise ValueError(f"unknown reassignment reason {reason_category!r}")
    lead = {"key": lead_key, **domain.routing["team_leads"][lead_key]}
    old = _snapshot(enq)
    enq.destination_type, enq.destination_key, enq.destination_name = "lead", lead_key, lead["name"]
    enq.status = "reassigned"
    session.add(Feedback(enquiry_id=enq.id, action="reassign", old_value=old, new_value=_snapshot(enq),
                         reason=reason or None, reason_category=reason_category, source=source))
    label = REASSIGN_REASONS[reason_category] + (f": {reason}" if reason else "")
    session.add(reassignment_notification(enq, lead, label, base_url))
    session.add(enq)
    session.commit()
    return enq


def mark_misclassified(session: Session, domain: Domain, enq: Enquiry, service_line: str, complexity: str,
                       reason: str | None, base_url: str, *, source: str = "user") -> Enquiry:
    """Record the correct labels. If the service line changed, also reroute to its lead."""
    old = _snapshot(enq)
    enq.service_line, enq.complexity = service_line, complexity
    lead = domain.lead_for(service_line)
    if lead and (enq.destination_type != "lead" or enq.destination_key != lead["key"]) and old["service_line"] != service_line:
        enq.destination_type, enq.destination_key, enq.destination_name = "lead", lead["key"], lead["name"]
        session.add(reassignment_notification(enq, lead, f"reclassified as {domain.line_name(service_line)}", base_url))
    enq.status = "reassigned"
    session.add(Feedback(enquiry_id=enq.id, action="misclassified", old_value=old, new_value=_snapshot(enq),
                         reason=reason or None, source=source))
    session.add(enq)
    session.commit()
    return enq
