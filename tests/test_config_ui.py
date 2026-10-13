"""Configuration page: validate -> preview (diff, prompt diff, routing impact) -> apply -> live reload."""
import html
import shutil
import time

import pytest
from fastapi.testclient import TestClient

from app import configstore
from app.config import ROOT, get_domain, get_settings, load_domain
from app.models import ConfigChange, PipelineRun
from tests.conftest import FakeLLMClient
from tests.test_app import FORM, submit_and_wait

HX = {"HX-Request": "true"}


@pytest.fixture
def cfg_client(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    shutil.copytree(ROOT / "config", cfg)
    monkeypatch.setenv("CONFIG_DIR", str(cfg))
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setenv("SEED_ON_STARTUP", "false")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    get_settings.cache_clear()
    get_domain.cache_clear()
    from app.main import app
    with TestClient(app) as c:
        app.state.deps.client = FakeLLMClient()
        app.state.deps.backoff_seconds = 0
        yield c, cfg
    get_settings.cache_clear()
    get_domain.cache_clear()


def routing_form(domain, **overrides):
    rt = domain.routing
    f = {"name": "routing", "mode": "form", "min_service_line_agreement": "3",
         "hard_stop_injection": "on", "hard_stop_conflict": "on", "hard_stop_failure": "on",
         "review_on_out_of_scope": "on", "dedupe_window_days": "7",
         **{f"exempt__{x}": "on" for x in rt["review"]["exempt_flags"]}}
    for key, p in rt["team_leads"].items():
        for k in ("name", "email", "slack"):
            f[f"lead__{key}__{k}"] = p[k]
    for line, lk in rt["routing_table"].items():
        f[f"table__{line}"] = lk
    for k in ("name", "email", "slack"):
        f[f"practice_head__{k}"] = rt["practice_head"][k]
    f.update(overrides)
    return {k: v for k, v in f.items() if v is not None}


def test_live_config_is_valid():
    assert configstore.validate(load_domain(ROOT / "config")) == []


@pytest.mark.parametrize("name,mutate,expect", [
    ("routing", lambda t: t.replace("  tax: tax\n", "  tax: nobody\n"), "unknown team lead 'nobody'"),
    ("routing", lambda t: t.replace("  - id: default\n", "  - id: defaultx\n"), "last rule must be 'default'"),
    ("taxonomy", lambda t: t.replace("id: out_of_scope", "id: outside"), "must include ['out_of_scope']"),
    ("rubric", lambda t: t.replace("level: moderate", "level: medium"), "complexity levels must be exactly"),
    ("routing", lambda t: t + "\n  bad: [unclosed\n", "not valid YAML"),
])
def test_validation_blocks_bad_edits(cfg_client, name, mutate, expect):
    c, _ = cfg_client
    r = c.post("/config/preview", data={"name": name, "text": mutate(configstore.read_text(name))}, headers=HX)
    assert expect in html.unescape(r.text) and "Save and apply" not in r.text
    a = c.post("/config/apply", data={"name": name, "text": mutate(configstore.read_text(name))}, headers=HX)
    assert a.status_code == 422


def test_turning_off_conflict_hard_stop_shows_impact_then_applies_live(cfg_client):
    c, cfg = cfg_client
    eid = submit_and_wait(c, {**FORM, "email": "j@brightwatercap.com", "company_name": "Brightwater Capital Partners"})
    assert "Review queue" in c.get(f"/enquiries/{eid}").text

    form = routing_form(get_domain(), hard_stop_conflict=None)
    pv = c.post("/config/preview", data=form, headers=HX).text
    assert "1 of 1 existing enquiries would route differently" in pv
    assert eid in pv and "Daniel Whitfield" in pv
    assert "on_conflict_flag: false" in pv  # diff shows the YAML change
    assert "# Routing configuration." in configstore.read_text("routing")

    text = configstore.routing_from_form(form)
    r = c.post("/config/apply", data={"name": "routing", "text": text, "note": "test"}, headers=HX)
    assert r.headers["HX-Redirect"].startswith("/config?tab=routing&saved=")
    assert "on_conflict_flag: false" in (cfg / "routing.yaml").read_text()
    assert "# Routing configuration." in (cfg / "routing.yaml").read_text()  # comments preserved

    # Applied without a restart: the next conflicted enquiry routes automatically.
    eid2 = submit_and_wait(c, {**FORM, "email": "k@brightwatercap.com", "company_name": "Brightwater Capital Partners",
                               "description": "Another request from the same fund."})
    assert "Daniel Whitfield" in c.get(f"/enquiries/{eid2}").text
    hist = c.get("/config?tab=history").text
    assert "routing.yaml" in hist and "test" in hist and "1 existing enquiry would route differently" in hist


def test_rename_lead_and_revert(cfg_client):
    c, cfg = cfg_client
    text = configstore.routing_from_form(routing_form(get_domain(), **{"lead__tax__name": "Tamsin Okoro"}))
    c.post("/config/apply", data={"name": "routing", "text": text}, headers=HX)
    eid = submit_and_wait(c)
    assert "Tamsin Okoro" in c.get(f"/enquiries/{eid}").text
    from sqlmodel import Session, select
    with Session(c.app.state.engine) as s:
        change = s.exec(select(ConfigChange)).one()
    pv = c.post(f"/config/revert/{change.id}", headers=HX).text
    assert "Save revert" in pv and "Daniel Whitfield" in pv
    c.post("/config/apply", data={"name": "routing", "text": change.before}, headers=HX)
    assert "Daniel Whitfield" in (cfg / "routing.yaml").read_text()


def test_taxonomy_edit_changes_prompt_and_runs_record_hash(cfg_client):
    c, _ = cfg_client
    d = get_domain()
    before_hash = configstore.prompt_hash(d)
    form = {"name": "taxonomy", "mode": "form"}
    for sl in d.taxonomy["service_lines"]:
        form.update({f"{sl['id']}__name": sl["name"], f"{sl['id']}__description": sl["description"],
                     f"{sl['id']}__typical_requests": "\n".join(sl["typical_requests"]),
                     f"{sl['id']}__not_this": "\n".join(sl["not_this"])})
    form["tax__not_this"] += "\nCarbon tax reporting is risk_reg."
    pv = c.post("/config/preview", data=form, headers=HX).text
    assert "Changes to the system prompt" in pv and "Carbon tax reporting is risk_reg." in pv
    assert "re-run" in pv  # eval staleness warning
    c.post("/config/apply", data={"name": "taxonomy", "text": configstore.taxonomy_from_form(form)}, headers=HX)
    assert configstore.prompt_hash(get_domain()) != before_hash
    eid = submit_and_wait(c)
    assert "Carbon tax reporting is risk_reg." in c.app.state.deps.client.calls[-1]["system"]
    from sqlmodel import Session, select
    with Session(c.app.state.engine) as s:
        run = s.exec(select(PipelineRun).where(PipelineRun.enquiry_id == eid)).one()
    assert run.prompt_hash == configstore.prompt_hash(get_domain())
    assert "These results were produced with prompt" in c.get("/eval").text


def test_prompt_tab_shows_exact_prompt(cfg_client):
    c, _ = cfg_client
    page = c.get("/config?tab=prompt").text
    assert "untrusted data" in page and "response_format: json_schema" in page
    for sl in get_domain().taxonomy["service_lines"]:
        assert sl["id"] in page
    assert "Routing rules, in the order they run" in c.get("/config").text
