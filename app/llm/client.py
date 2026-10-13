"""LLM client interface and the OpenRouter implementation.

All OpenRouter-specific code lives in this file. The rest of the app depends
only on LLMClient, so calling a provider directly later means writing one new
class that implements `complete_json`.
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
import openai
from openai import OpenAI

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
RETRYABLE_STATUS = {429, 502, 503}


class LLMError(Exception):
    """A call failed. `retryable` marks rate limits and upstream outages."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass
class LLMResponse:
    content: str                  # raw text the model returned (JSON string)
    model_requested: str
    model_used: str               # may differ when a fallback model answered
    provider: str | None          # the upstream provider OpenRouter routed to
    input_tokens: int
    output_tokens: int
    cost_usd: float | None        # as reported by the API, not estimated
    latency_ms: int
    temperature: float | None     # None = not sent (model doesn't support it)
    request_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class LLMClient(ABC):
    @abstractmethod
    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        temperature: float | None,
        fallback_models: list[str] | None = None,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Return the model's JSON output for `schema`. Raise LLMError on failure."""

    @abstractmethod
    def supports_temperature(self, model: str) -> bool:
        """Whether `temperature` can be sent to this model."""


class OpenRouterClient(LLMClient):
    def __init__(
        self,
        api_key: str,
        *,
        app_url: str,
        app_name: str,
        timeout: float = 60.0,
        http_client: httpx.Client | None = None,
    ):
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key,
            max_retries=0,  # retries are explicit in classify.py so they are logged and tested
            timeout=timeout,
            default_headers={"HTTP-Referer": app_url, "X-Title": app_name},
            http_client=http_client,
        )
        self._capabilities: dict[str, set[str]] | None = None

    # -- capabilities ---------------------------------------------------------

    def _load_capabilities(self) -> dict[str, set[str]]:
        if self._capabilities is None:
            try:
                resp = self._client.get("/models", cast_to=httpx.Response)
                data = resp.json()["data"]
                self._capabilities = {m["id"]: set(m.get("supported_parameters") or []) for m in data}
            except Exception:
                # Unknown: be conservative and send no optional parameters.
                self._capabilities = {}
        return self._capabilities

    def supports_temperature(self, model: str) -> bool:
        return "temperature" in self._load_capabilities().get(model, set())

    # -- completion -----------------------------------------------------------

    def complete_json(
        self,
        *,
        model: str,
        system: str,
        user: str,
        schema: dict[str, Any],
        schema_name: str,
        temperature: float | None,
        fallback_models: list[str] | None = None,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        extra_body: dict[str, Any] = {
            # Only route to providers that honour every parameter we send
            # (response_format, temperature). Otherwise a provider could
            # silently ignore the schema.
            "provider": {"require_parameters": True},
            "usage": {"include": True},
        }
        if fallback_models:
            extra_body["models"] = [model, *[m for m in fallback_models if m != model]]
        kwargs: dict[str, Any] = {}
        if temperature is not None:
            kwargs["temperature"] = temperature

        started = time.perf_counter()
        try:
            completion = self._client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": schema_name, "strict": True, "schema": schema},
                },
                max_tokens=max_tokens,
                extra_body=extra_body,
                **kwargs,
            )
        except openai.APIStatusError as e:
            raise LLMError(
                f"OpenRouter HTTP {e.status_code}: {_error_message(e)}",
                status=e.status_code,
                retryable=e.status_code in RETRYABLE_STATUS,
            ) from e
        except (openai.APIConnectionError, openai.APITimeoutError) as e:
            raise LLMError(f"OpenRouter connection error: {e}", retryable=True) from e
        latency_ms = int((time.perf_counter() - started) * 1000)

        body = completion.model_dump()
        # OpenRouter can return HTTP 200 with an error object (e.g. upstream failure mid-stream).
        if body.get("error"):
            err = body["error"]
            code = err.get("code") if isinstance(err, dict) else None
            raise LLMError(f"OpenRouter error: {err}", status=code, retryable=code in RETRYABLE_STATUS)
        if not completion.choices:
            raise LLMError("OpenRouter returned no choices", retryable=True)

        content = completion.choices[0].message.content or ""
        usage = body.get("usage") or {}
        return LLMResponse(
            content=content,
            model_requested=model,
            model_used=body.get("model") or model,
            provider=body.get("provider"),
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cost_usd=usage.get("cost"),
            latency_ms=latency_ms,
            temperature=temperature,
            request_id=body.get("id"),
            extra={"finish_reason": completion.choices[0].finish_reason},
        )


def _error_message(e: openai.APIStatusError) -> str:
    try:
        return json.dumps(e.response.json().get("error", {}).get("message", ""))[:300]
    except Exception:
        return str(e)[:300]
