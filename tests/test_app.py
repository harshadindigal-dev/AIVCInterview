"""HTTP-level tests with a fake LLM: submit flow, actions, metrics react to feedback."""
import time

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from tests.conftest import FakeLLMClient, triage_payload

FORM = dict(name="Ann", email="ann@acme.example", company_name="Acme", industry="Other", company_size="11-50",
            stated_urgency="normal", description="We need help with a VAT registration question.")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        app.state.deps.client = FakeLLMClient()
        app.state.deps.backoff_seconds = 0
        yield c
    get_settings.cache_clear()


def submit_and_wait(c, form=FORM):
    r = c.post("/submit", data=form, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "Processing" in r.text
    eid = r.text.split("Processing ")[1].split("<")[0].strip()
    for _ in range(100):
        p = c.get(f"/enquiries/{eid}/progress", headers={"HX-Request": "true"})
        if p.headers.get("HX-Redirect"):
            assert p.headers["HX-Redirect"] == f"/enquiries/{eid}"
            return eid
        time.sleep(0.05)
    raise AssertionError("pipeline did not finish")


def test_missing_key_fails_at_startup(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_settings().validate_for_startup()
    get_settings.cache_clear()


def test_validation_errors_rerender_form(client):
    r = client.post("/submit", data={**FORM, "description": "short", "industry": "Nope"})
    assert r.status_code == 200
    assert "at least 10 characters" in r.text and "choose an option" in r.text


def test_submit_flow_and_detail_explains_routing(client):
    eid = submit_and_wait(client)
    d = client.get(f"/enquiries/{eid}")
    assert d.status_code == 200
    assert "Daniel Whitfield" in d.text and "Why it went here" in d.text and "Decided" in d.text
    assert "Technical details" in d.text


def test_reassign_changes_metrics(client):
    eid = submit_and_wait(client)
    before = client.get("/metrics.json").json()
    assert before["reassignment_rate"] == 0
    r = client.post(f"/enquiries/{eid}/reassign", data={"lead_key": "deals", "reason": "valuation",
                                                       "context": "detail"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Redirect"] == f"/enquiries/{eid}?done=reassigned"
    after = client.get("/metrics.json").json()
    assert after["reassignment_rate"] == 1.0
    assert after["feedback_counts"] == {"reassign": 1}
    assert "Sofia Marchetti" in client.get("/notifications").text


def test_review_queue_accept_removes_card(client):
    client.app.state.deps.client = FakeLLMClient(default=triage_payload(flags=["vague"]))
    eid = submit_and_wait(client)
    page = client.get("/review").text
    assert f"card-{eid}" in page and "Flag: vague" in page
    r = client.post(f"/enquiries/{eid}/accept", data={"context": "review"}, headers={"HX-Request": "true"})
    assert 'id="review-count"' in r.text and ">0<" in r.text and "Nothing waiting for review" in r.text
    assert f"card-{eid}" not in client.get("/review").text


def test_empty_states(client):
    assert "No enquiries yet" in client.get("/enquiries").text
    assert "Nothing waiting for review" in client.get("/review").text
    assert "No notifications yet" in client.get("/notifications").text
    assert client.get("/enquiries/NOPE").status_code == 404
    assert client.get("/healthz").json()["ok"] is True
