"""Pydantic I/O schemas, including the LLM output schema."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

# Fixed form lists (the real web form's dropdowns).
INDUSTRIES = [
    "Financial services",
    "Healthcare & life sciences",
    "Technology",
    "Manufacturing",
    "Retail & consumer",
    "Energy & utilities",
    "Real estate & construction",
    "Transport & logistics",
    "Public sector",
    "Professional services",
    "Media & telecoms",
    "Not-for-profit",
    "Other",
]
COMPANY_SIZES = ["1-10", "11-50", "51-200", "201-1000", "1000+"]
URGENCIES = ["low", "normal", "high"]
COMPLEXITIES = ["simple", "moderate", "complex"]
FLAGS = ["multi_service", "vague", "out_of_scope", "spam", "urgency_mismatch", "possible_injection"]

Urgency = Literal["low", "normal", "high"]
Complexity = Literal["simple", "moderate", "complex"]
Flag = Literal["multi_service", "vague", "out_of_scope", "spam", "urgency_mismatch", "possible_injection"]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EnquiryIn(BaseModel):
    """What the web form submits."""

    name: str = Field(min_length=1, max_length=200)
    email: str = Field(max_length=320)
    company_name: str = Field(min_length=1, max_length=200)
    industry: Literal[tuple(INDUSTRIES)]  # type: ignore[valid-type]
    company_size: Literal[tuple(COMPANY_SIZES)]  # type: ignore[valid-type]
    stated_urgency: Urgency
    description: str
    submitted_at: datetime | None = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        v = v.strip()
        if not _EMAIL_RE.match(v):
            raise ValueError("enter a valid email address")
        return v

    @field_validator("description")
    @classmethod
    def _description(cls, v: str) -> str:
        n = len(v.strip())
        if n < 10:
            raise ValueError("description must be at least 10 characters")
        if n > 5000:
            raise ValueError("description must be at most 5000 characters")
        return v  # original text kept exactly as submitted


class TriageResult(BaseModel):
    """The classifier's structured output.

    Service-line values are validated against the taxonomy passed in the
    validation context, so the allowed ids come from taxonomy.yaml, not code.
    """

    primary_service_line: str
    secondary_service_lines: list[str]
    complexity: Complexity
    complexity_rationale: str = Field(min_length=1)
    inferred_urgency: Urgency
    key_entities: list[str]
    missing_information: list[str]
    flags: list[Flag]
    summary: str = Field(min_length=1)

    @model_validator(mode="after")
    def _check_ids(self, info: ValidationInfo) -> "TriageResult":
        ctx = info.context or {}
        outcomes: list[str] | None = ctx.get("outcome_ids")
        lines: list[str] | None = ctx.get("service_line_ids")
        if outcomes is not None and self.primary_service_line not in outcomes:
            raise ValueError(f"unknown primary_service_line {self.primary_service_line!r}")
        if lines is not None:
            bad = [s for s in self.secondary_service_lines if s not in lines]
            if bad:
                raise ValueError(f"unknown secondary_service_lines {bad}")
        # A secondary line equal to the primary adds nothing; drop it.
        self.secondary_service_lines = [
            s for s in dict.fromkeys(self.secondary_service_lines) if s != self.primary_service_line
        ]
        self.flags = list(dict.fromkeys(self.flags))
        return self


def triage_json_schema(service_line_ids: list[str], outcome_ids: list[str]) -> dict[str, Any]:
    """JSON schema sent as response_format (strict mode: every field required,
    no additional properties). Enums come from the taxonomy."""
    str_list = {"type": "array", "items": {"type": "string"}}
    return {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "primary_service_line",
            "secondary_service_lines",
            "complexity",
            "complexity_rationale",
            "inferred_urgency",
            "key_entities",
            "missing_information",
            "flags",
            "summary",
        ],
        "properties": {
            "primary_service_line": {"type": "string", "enum": outcome_ids},
            "secondary_service_lines": {"type": "array", "items": {"type": "string", "enum": service_line_ids}},
            "complexity": {"type": "string", "enum": COMPLEXITIES},
            "complexity_rationale": {"type": "string"},
            "inferred_urgency": {"type": "string", "enum": URGENCIES},
            "key_entities": str_list,
            "missing_information": str_list,
            "flags": {"type": "array", "items": {"type": "string", "enum": FLAGS}},
            "summary": {"type": "string"},
        },
    }
