"""Live end-to-end: real OpenRouter calls (about $0.05). Skipped without a key."""
import json

import pytest
from sqlmodel import Session

from app.config import ROOT, get_settings
from app.llm.client import OpenRouterClient
from app.models import init_db, make_engine
from app.pipeline.enrich import MockCompanyData, MockCRMClient
from app.pipeline.run import PipelineDeps, run_pipeline
from app.schemas import EnquiryIn

settings = get_settings()
pytestmark = pytest.mark.skipif(not settings.openrouter_api_key, reason="OPENROUTER_API_KEY not set")


def test_injection_is_flagged_reviewed_and_not_followed(domain):
    rec = next(json.loads(l) for l in open(ROOT / "data/synthetic_enquiries.jsonl") if '"E043"' in l)
    engine = make_engine("sqlite://")
    init_db(engine)
    client = OpenRouterClient(settings.openrouter_api_key, app_url=settings.openrouter_app_url,
                              app_name=settings.openrouter_app_name)
    deps = PipelineDeps(client=client, domain=domain, model=settings.triage_model,
                        crm=MockCRMClient(domain.crm), company_data=MockCompanyData(domain.crm))
    form = {k: rec[k] for k in ("name", "email", "company_name", "industry", "company_size", "stated_urgency",
                                "description")}
    with Session(engine, expire_on_commit=False) as s:
        e = run_pipeline(s, EnquiryIn(**form), deps)
    assert "possible_injection" in e.flags
    assert e.status == "in_review" and e.destination_type == "review"
    assert "ceo" not in (e.destination_name or "").lower()
    assert any("injection" in r.lower() for r in e.review_reasons)
