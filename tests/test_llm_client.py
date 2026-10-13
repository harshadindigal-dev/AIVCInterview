"""OpenRouterClient with the HTTP layer mocked (httpx.MockTransport)."""
import json

import httpx
import pytest

from app.llm.client import LLMError, OpenRouterClient
from app.pipeline.classify import classify
from tests.conftest import triage_payload

ENQ = dict(name="A", email="a@b.co", company_name="C", industry="Other", company_size="1-10",
           stated_urgency="normal", description="We need a VAT registration check please.")
MODELS = {"data": [{"id": "vendor/temp-model", "supported_parameters": ["temperature", "response_format"]},
                   {"id": "vendor/no-temp", "supported_parameters": ["response_format"]}]}


def completion(content, model="vendor/temp-model"):
    return {"id": "gen-1", "object": "chat.completion", "created": 1, "model": model, "provider": "SomeCloud",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1200, "completion_tokens": 300, "total_tokens": 1500, "cost": 0.0042}}


def make_client(responses):
    """responses: list of (status, body) for successive chat calls."""
    sent = []
    queue = list(responses)

    def handler(request: httpx.Request):
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json=MODELS)
        sent.append(request)
        status, body = queue.pop(0) if queue else (200, completion(json.dumps(triage_payload())))
        return httpx.Response(status, json=body)

    c = OpenRouterClient("sk-test", app_url="http://localhost:8000", app_name="Intake Triage Demo",
                         http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    return c, sent


def test_request_shape():
    client, sent = make_client([(200, completion(json.dumps(triage_payload())))])
    resp = client.complete_json(model="vendor/temp-model", system="s", user="u", schema={"type": "object"},
                                schema_name="record_triage", temperature=0.0, fallback_models=["other/model"])
    body = json.loads(sent[0].content)
    assert body["provider"] == {"require_parameters": True}
    assert body["usage"] == {"include": True}
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["name"] == "record_triage"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["temperature"] == 0.0
    assert body["models"] == ["vendor/temp-model", "other/model"]
    assert sent[0].headers["X-Title"] == "Intake Triage Demo"
    assert sent[0].headers["HTTP-Referer"] == "http://localhost:8000"
    assert sent[0].headers["Authorization"] == "Bearer sk-test"
    assert resp.provider == "SomeCloud" and resp.cost_usd == 0.0042
    assert resp.input_tokens == 1200 and resp.output_tokens == 300


def test_temperature_omitted_when_none():
    client, sent = make_client([])
    client.complete_json(model="vendor/no-temp", system="s", user="u", schema={}, schema_name="x", temperature=None)
    assert "temperature" not in json.loads(sent[0].content)


def test_capabilities_from_models_endpoint():
    client, _ = make_client([])
    assert client.supports_temperature("vendor/temp-model")
    assert not client.supports_temperature("vendor/no-temp")
    assert not client.supports_temperature("unknown/model")


@pytest.mark.parametrize("status,retryable", [(429, True), (502, True), (503, True), (400, False), (402, False)])
def test_status_classification(status, retryable):
    client, _ = make_client([(status, {"error": {"message": "nope", "code": status}})])
    with pytest.raises(LLMError) as ei:
        client.complete_json(model="vendor/temp-model", system="s", user="u", schema={}, schema_name="x",
                             temperature=None)
    assert ei.value.status == status and ei.value.retryable is retryable


def test_429_is_retried_end_to_end(domain):
    client, sent = make_client([(429, {"error": {"message": "rate limited", "code": 429}})])
    slept = []
    out = classify(client=client, domain=domain, model="vendor/temp-model", enquiry=ENQ, backoff_seconds=0.5,
                   sleep=slept.append, n_runs=1)
    assert out.ok and len(sent) == 2 and slept == [0.5]
    assert out.provider == "SomeCloud"


def test_malformed_output_triggers_validation_retry(domain):
    bad = completion('{"primary_service_line": "tax"}')  # missing required fields
    client, sent = make_client([(200, bad)])
    out = classify(client=client, domain=domain, model="vendor/temp-model", enquiry=ENQ, backoff_seconds=0,
                   sleep=lambda s: None, n_runs=1)
    assert out.ok and len(sent) == 2
    assert out.runs[0].attempts[0]["kind"] == "validation_error"


def test_malformed_twice_is_classification_failed(domain):
    bad = completion("{not json")
    client, sent = make_client([(200, bad), (200, bad)])
    out = classify(client=client, domain=domain, model="vendor/temp-model", enquiry=ENQ, backoff_seconds=0,
                   sleep=lambda s: None, n_runs=1)
    assert not out.ok and out.error.startswith("classification_failed")
