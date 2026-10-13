"""Orchestrator + audit log, with a fake LLM."""
import json

from sqlmodel import Session, select

from app.feedback import reassign
from app.llm.client import LLMError
from app.models import Feedback, Notification, PipelineRun, init_db, make_engine
from app.pipeline.enrich import MockCompanyData, MockCRMClient
from app.pipeline.run import PipelineDeps, run_pipeline
from app.schemas import EnquiryIn
from tests.conftest import FakeLLMClient, triage_payload

FORM = dict(name="A", email="a@acme.example", company_name="Acme", industry="Other", company_size="11-50",
            stated_urgency="normal", description="We need help with a VAT registration question.")


def setup(domain, client):
    engine = make_engine("sqlite://")
    init_db(engine)
    deps = PipelineDeps(client=client, domain=domain, model="vendor/model", crm=MockCRMClient(domain.crm),
                        company_data=MockCompanyData(domain.crm), backoff_seconds=0, sleep=lambda s: None)
    return Session(engine, expire_on_commit=False), deps


def test_happy_path_writes_audit_and_notification(domain):
    s, deps = setup(domain, FakeLLMClient())
    steps = []
    e = run_pipeline(s, EnquiryIn(**FORM), deps, on_step=lambda st, status, info: steps.append((st, status)))
    assert e.status == "auto_routed" and e.destination_name == "Daniel Whitfield"
    run = s.exec(select(PipelineRun)).one()
    assert run.prompt_version == "v1" and run.model == "vendor/model" and run.provider == "FakeProvider"
    assert len(run.classification["runs"]) == 3
    assert all(r["attempts"][0]["response"]["content"] for r in run.classification["runs"])  # raw outputs kept
    assert run.parsed["primary_service_line"] == "tax"
    assert run.routing["deciding_rule"] == "default" and run.final_destination == "lead:tax"
    assert set(run.latency_ms) == {"intake", "enrich", "classify", "route", "notify"}
    assert run.input_tokens == 300 and run.cost_usd is not None
    n = s.exec(select(Notification)).one()
    assert n.recipient_name == "Daniel Whitfield" and "?action=accept" in n.body
    assert ("notify", "done") in steps


def test_original_text_kept_exactly(domain):
    s, deps = setup(domain, FakeLLMClient())
    raw = "  We need help\n\nwith a VAT   registration question.  "
    e = run_pipeline(s, EnquiryIn(**{**FORM, "description": raw}), deps)
    assert e.description == raw
    assert e.description_normalised == "We need help with a VAT registration question."


def test_duplicate_is_linked_and_skips_triage(domain):
    client = FakeLLMClient()
    s, deps = setup(domain, client)
    first = run_pipeline(s, EnquiryIn(**FORM), deps)
    calls = len(client.calls)
    dup = run_pipeline(s, EnquiryIn(**{**FORM, "description": FORM["description"].upper() + "  "}), deps)
    assert dup.duplicate_of == first.id and dup.status == "duplicate"
    assert len(client.calls) == calls  # no LLM calls
    assert len(s.exec(select(Notification)).all()) == 1  # no second notification


def test_classification_failure_goes_to_review_never_dropped(domain):
    s, deps = setup(domain, FakeLLMClient(default=LLMError("down", status=503, retryable=True)))
    e = run_pipeline(s, EnquiryIn(**FORM), deps)
    assert e.status == "in_review" and e.destination_type == "review"
    assert e.review_reasons[0].startswith("Classification failed")
    run = s.exec(select(PipelineRun)).one()
    assert run.classification_failed
    assert s.exec(select(Notification)).one().kind == "review"


def test_archive_sends_no_notification(domain):
    s, deps = setup(domain, FakeLLMClient(default=triage_payload(primary_service_line="not_an_enquiry",
                                                                 flags=["spam"])))
    e = run_pipeline(s, EnquiryIn(**FORM), deps)
    assert e.status == "archived" and s.exec(select(Notification)).all() == []


def test_reassign_writes_feedback(domain):
    s, deps = setup(domain, FakeLLMClient())
    e = run_pipeline(s, EnquiryIn(**FORM), deps)
    reassign(s, domain, e, "deals", "really a valuation", "http://x", reason_category="wrong_service_line")
    fb = s.exec(select(Feedback)).one()
    assert fb.action == "reassign" and fb.reason_category == "wrong_service_line" and fb.old_value["destination_key"] == "tax"
    assert fb.new_value["destination_key"] == "deals" and e.status == "reassigned"
