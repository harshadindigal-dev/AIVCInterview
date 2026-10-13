"""Database tables (SQLModel on SQLite) and engine helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, Session, SQLModel, create_engine


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Enquiry(SQLModel, table=True):
    id: str = Field(primary_key=True)
    submitted_at: datetime = Field(index=True)
    name: str
    email: str = Field(index=True)
    company_name: str
    industry: str
    company_size: str
    stated_urgency: str
    description: str                      # exactly as submitted
    description_normalised: str
    text_hash: str = Field(index=True)
    source: str = "form"                  # form | seed
    duplicate_of: str | None = None

    # processing | auto_routed | in_review | accepted | reassigned | archived | duplicate
    status: str = "processing"
    destination_type: str | None = None   # lead | partner | review | archive | duplicate
    destination_key: str | None = None
    destination_name: str | None = None

    # Denormalised from the latest run for the inbox; corrected by feedback.
    service_line: str | None = None
    secondary_service_lines: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    complexity: str | None = None
    inferred_urgency: str | None = None
    summary: str | None = None
    flags: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    review_reasons: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    latest_run_id: int | None = None
    created_at: datetime = Field(default_factory=utcnow)


class PipelineRun(SQLModel, table=True):
    """Audit log: one row per pipeline run."""

    id: int | None = Field(default=None, primary_key=True)
    enquiry_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    prompt_version: str | None = None
    model: str | None = None              # requested
    model_used: str | None = None         # answered (differs if a fallback model was used)
    provider: str | None = None           # upstream provider OpenRouter chose
    enrichment: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    classification: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))  # all runs, raw outputs
    parsed: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    routing: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))         # trace, cc, reasons
    final_destination: str | None = None
    latency_ms: dict[str, int] = Field(default_factory=dict, sa_column=Column(JSON))
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    classification_failed: bool = False


class Notification(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    enquiry_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    channel: str                          # email | slack
    recipient_name: str
    recipient_address: str
    cc: list[dict[str, str]] = Field(default_factory=list, sa_column=Column(JSON))
    subject: str
    body: str
    kind: str = "routed"                  # routed | review | reassigned


class Feedback(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    enquiry_id: str = Field(index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    action: str                           # accept | reassign | misclassified
    old_value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    new_value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    reason: str | None = None
    source: str = "user"                  # user | seed (demo feedback created at startup)


def make_engine(url: str):
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def init_db(engine) -> None:
    SQLModel.metadata.create_all(engine)


def new_session(engine) -> Session:
    return Session(engine, expire_on_commit=False)
