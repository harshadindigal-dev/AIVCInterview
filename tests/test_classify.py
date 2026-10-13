from app.llm.client import LLMError
from app.pipeline.classify import classify
from tests.conftest import FakeLLMClient, triage_payload

ENQ = dict(name="A", email="a@b.co", company_name="C", industry="Other", company_size="1-10",
           stated_urgency="normal", description="We need a VAT registration check please.")


def run(client, domain):
    return classify(client=client, domain=domain, model="m/x", enquiry=ENQ, backoff_seconds=0, sleep=lambda s: None)


def test_three_runs_with_temperature_schedule(domain):
    client = FakeLLMClient()
    out = run(client, domain)
    assert out.ok and len(client.calls) == 3
    assert sorted(c["temperature"] for c in client.calls) == [0.0, 0.7, 0.7]
    assert out.agreement_service_line == 3 and out.agreement_complexity == 3
    assert out.disagreement_text() == "3/3 runs agreed"


def test_no_temperature_when_model_rejects_it(domain):
    client = FakeLLMClient(temperature=False)
    run(client, domain)
    assert [c["temperature"] for c in client.calls] == [None, None, None]


def test_agreement_counts_disagreement(domain):
    # Runs execute concurrently, so script by temperature: the primary is the only 0.0 call.
    sampled = iter([triage_payload(primary_service_line="deals", complexity="complex"),
                    triage_payload(primary_service_line="tax", complexity="complex")])
    client = FakeLLMClient(default=lambda call: triage_payload() if call["temperature"] == 0.0 else next(sampled))
    out = run(client, domain)
    assert out.result.primary_service_line == "tax"
    assert out.agreement_service_line == 2
    assert out.agreement_complexity == 1
    assert out.disagreement_text() == "2/3 agreed: tax vs deals"


def test_invalid_output_retried_then_succeeds(domain):
    client = FakeLLMClient(temperature=False, script={0: "not json"})
    out = run(client, domain)
    assert out.ok
    assert len(client.calls) == 4  # one run needed a retry
    failed = [r for r in out.runs if len(r.attempts) == 2]
    assert len(failed) == 1 and failed[0].attempts[0]["kind"] == "validation_error"


def test_unknown_service_line_is_a_validation_error(domain):
    bad = triage_payload(primary_service_line="ceo_office")
    client = FakeLLMClient(default=bad)
    out = run(client, domain)
    assert not out.ok
    assert out.error.startswith("classification_failed")
    assert all(len(r.attempts) == 2 for r in out.runs)


def test_api_error_twice_gives_classification_failed(domain):
    client = FakeLLMClient(default=LLMError("boom", status=503, retryable=True))
    slept = []
    out = classify(client=client, domain=domain, model="m/x", enquiry=ENQ, backoff_seconds=1.5, sleep=slept.append)
    assert not out.ok and "boom" in out.error
    assert len(client.calls) == 6 and slept == [1.5, 1.5, 1.5]


def test_injection_flag_in_any_run_is_recorded(domain):
    client = FakeLLMClient(temperature=False, script={2: triage_payload(flags=["possible_injection"])})
    out = run(client, domain)
    assert out.injection_flagged_any_run


def test_tokens_and_cost_are_summed(domain):
    out = run(FakeLLMClient(), domain)
    assert out.input_tokens == 300 and out.output_tokens == 150
    assert abs(out.cost_usd - 0.003) < 1e-9
