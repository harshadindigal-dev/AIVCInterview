"""Notifications: written to the notification log instead of email/Slack."""
from __future__ import annotations

from app.config import Domain
from app.models import Enquiry, Notification
from app.pipeline.route import RoutingDecision


def _links(base_url: str, enquiry_id: str) -> str:
    url = f"{base_url.rstrip('/')}/enquiries/{enquiry_id}"
    return f"Accept: {url}?action=accept\nReassign: {url}?action=reassign"


def build_notifications(enquiry: Enquiry, decision: RoutingDecision, domain: Domain,
                        base_url: str) -> list[Notification]:
    if decision.destination_type in ("archive", "duplicate"):
        return []
    line = domain.line_name(enquiry.service_line) if enquiry.service_line else "Unclassified"
    summary = enquiry.summary or "(no summary: classification failed)"
    header = (
        f"{summary}\n\n"
        f"Company: {enquiry.company_name} ({enquiry.company_size}, {enquiry.industry})\n"
        f"Service line: {line}\n"
        f"Complexity: {enquiry.complexity or 'unknown'}\n"
        f"Urgency: stated {enquiry.stated_urgency}, inferred {enquiry.inferred_urgency or 'unknown'}\n"
    )
    if decision.destination_type == "review":
        reasons = "\n".join(f"- {r}" for r in decision.review_reasons)
        q = decision.recipient or {}
        return [Notification(
            enquiry_id=enquiry.id, channel="slack", kind="review",
            recipient_name=q.get("name", "Review queue"), recipient_address=q.get("slack", ""),
            subject=f"Needs review: {enquiry.company_name}",
            body=f"{header}\nWhy it needs review:\n{reasons}\n\n{_links(base_url, enquiry.id)}",
        )]
    r = decision.recipient
    why = next(s.detail for s in decision.trace if s.deciding)
    return [Notification(
        enquiry_id=enquiry.id, channel="email", kind="routed",
        recipient_name=r["name"], recipient_address=r["email"],
        cc=[{"name": p["name"], "email": p["email"]} for p in decision.cc],
        subject=f"New enquiry: {enquiry.company_name} ({line}, {enquiry.complexity})",
        body=f"{header}\nWhy you: {why}\n\n{_links(base_url, enquiry.id)}",
    )]


def reassignment_notification(enquiry: Enquiry, lead: dict, reason: str | None, base_url: str) -> Notification:
    return Notification(
        enquiry_id=enquiry.id, channel="email", kind="reassigned",
        recipient_name=lead["name"], recipient_address=lead["email"],
        subject=f"Reassigned to you: {enquiry.company_name}",
        body=(f"{enquiry.summary or ''}\n\nReassigned by a reviewer."
              + (f" Reason: {reason}" if reason else "") + f"\n\n{_links(base_url, enquiry.id)}"),
    )
