"""Shared fixtures. Unit tests never hit the network: LLM calls go through
FakeLLMClient or an httpx MockTransport."""
from __future__ import annotations

import json
import threading
from typing import Any, Callable

import pytest

from app.config import load_domain, ROOT
from app.llm.client import LLMClient, LLMError, LLMResponse


@pytest.fixture(scope="session")
def domain():
    return load_domain(ROOT / "config")


def triage_payload(**overrides: Any) -> dict[str, Any]:
    base = {
        "primary_service_line": "tax",
        "secondary_service_lines": [],
        "complexity": "simple",
        "complexity_rationale": "one well-defined deliverable",
        "inferred_urgency": "normal",
        "key_entities": [],
        "missing_information": [],
        "flags": [],
        "summary": "Client wants a VAT registration check.",
    }
    base.update(overrides)
    return base


class FakeLLMClient(LLMClient):
    """Returns scripted outputs. `script` maps call number (0-based, in call
    order) to a dict payload, a raw string, or an LLMError to raise; anything
    not scripted returns `default`. A callable item receives the call dict
    (model, user, temperature, system) and returns one of those."""

    def __init__(self, default: dict | Callable[[str], dict] | None = None, script: dict[int, Any] | None = None,
                 temperature: bool = True):
        self.default = default if default is not None else triage_payload()
        self.script = script or {}
        self.calls: list[dict[str, Any]] = []
        self._temperature = temperature
        self._lock = threading.Lock()

    def supports_temperature(self, model: str) -> bool:
        return self._temperature

    def complete_json(self, *, model, system, user, schema, schema_name, temperature, fallback_models=None,
                      max_tokens=4000) -> LLMResponse:
        call = {"model": model, "user": user, "temperature": temperature, "system": system}
        with self._lock:
            n = len(self.calls)
            self.calls.append(call)
        item = self.script.get(n, self.default)
        if callable(item) and not isinstance(item, LLMError):
            item = item(call)
        if isinstance(item, LLMError):
            raise item
        content = item if isinstance(item, str) else json.dumps(item)
        return LLMResponse(content=content, model_requested=model, model_used=model, provider="FakeProvider",
                           input_tokens=100, output_tokens=50, cost_usd=0.001, latency_ms=5,
                           temperature=temperature)
