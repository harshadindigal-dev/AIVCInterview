"""Intake: normalise and dedupe. Validation is done by schemas.EnquiryIn."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from sqlmodel import Session, select

from app.models import Enquiry


def normalise(text: str) -> str:
    """Collapse all whitespace runs to one space. The original is stored separately."""
    return " ".join(text.split())


def text_hash(email: str, description: str) -> str:
    key = email.strip().lower() + "\n" + normalise(description).lower()
    return hashlib.sha256(key.encode()).hexdigest()


def find_duplicate(session: Session, *, text_hash_: str, submitted_at: datetime, window_days: int,
                   exclude_id: str | None = None) -> Enquiry | None:
    """Earliest original enquiry with the same email + normalised text within the window."""
    stmt = (
        select(Enquiry)
        .where(Enquiry.text_hash == text_hash_)
        .where(Enquiry.duplicate_of.is_(None))
        .where(Enquiry.submitted_at >= submitted_at - timedelta(days=window_days))
        .where(Enquiry.submitted_at <= submitted_at)
        .order_by(Enquiry.submitted_at)
    )
    if exclude_id:
        stmt = stmt.where(Enquiry.id != exclude_id)
    return session.exec(stmt).first()
