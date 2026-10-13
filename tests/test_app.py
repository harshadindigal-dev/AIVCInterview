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
    home = client.get("/").text
    assert "routed to a team lead automatically" in home and eid in home


def test_reassign_changes_metrics(client):
    eid = submit_and_wait(client)
    before = client.get("/metrics.json").json()
    assert before["reassignment_rate"] == 0
    assert client.post(f"/enquiries/{eid}/reassign", data={"lead_key": "deals", "context": "detail"},
                       headers={"HX-Request": "true"}).status_code == 422  # reason is required
    r = client.post(f"/enquiries/{eid}/reassign", data={"lead_key": "deals", "reason": "valuation",
                                                       "reason_category": "wrong_service_line",
                                                       "context": "detail"}, headers={"HX-Request": "true"})
    assert r.headers["HX-Redirect"] == f"/enquiries/{eid}?done=reassigned"
    after = client.get("/metrics.json").json()
    assert after["reassignment_rate"] == 1.0 and after["model_error_rate"] == 1.0
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
    assert "No enquiries yet" in client.get("/").text
    assert client.get("/submit").status_code == 200
    assert "Nothing waiting for review" in client.get("/review").text
    assert "No notifications yet" in client.get("/notifications").text
    assert client.get("/enquiries/NOPE").status_code == 404
    assert client.get("/healthz").json()["ok"] is True


def test_inbox_sorting_filters_and_search(client):
    a = submit_and_wait(client, {**FORM, "company_name": "Zeta Ltd", "industry": "Technology"})
    client.app.state.deps.client = FakeLLMClient(default=triage_payload(complexity="complex", flags=["vague"]))
    b = submit_and_wait(client, {**FORM, "email": "b@beta.example", "company_name": "Alpha Ltd",
                                 "description": "A different request about something else."})
    page = lambda url: client.get(url).text
    assert page("/enquiries?sort=company&dir=asc").index(b) < page("/enquiries?sort=company&dir=asc").index(a)
    assert page("/enquiries?sort=complexity&dir=desc").index(b) < page("/enquiries?sort=complexity&dir=desc").index(a)
    assert page("/enquiries?sort=status&dir=asc").index(b) < page("/enquiries?sort=status&dir=asc").index(a)  # in review first
    only_tech = page("/enquiries?industry=Technology")
    assert a in only_tech and b not in only_tech
    assert b in page("/enquiries?q=alpha") and a not in page("/enquiries?q=alpha")
    assert client.get("/enquiries?sort=bogus&dir=sideways").status_code == 200  # falls back to defaults
    partial = client.get("/enquiries?sort=status&dir=asc", headers={"HX-Request": "true", "HX-Target": "inbox-table"}).text
    assert 'value="status" hx-swap-oob="true"' in partial


def test_capacity_reassignment_is_not_a_model_error(client):
    eid = submit_and_wait(client)
    client.post(f"/enquiries/{eid}/reassign", data={"lead_key": "deals", "reason_category": "capacity",
                                                   "reason": "Daniel on leave"}, headers={"HX-Request": "true"})
    m = client.get("/metrics.json").json()
    assert m["reassignment_rate"] == 1.0 and m["model_error_rate"] == 0.0
    assert m["reassign_reasons"] == [["Lead capacity or leave", 1]]
    assert "Lead capacity or leave" in client.get(f"/enquiries/{eid}").text
