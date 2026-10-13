"""One test per override rule, plus precedence and each review trigger."""
import pytest

from app.pipeline.route import ClassificationFacts, route

NO_CRM = {"existing_client": False, "relationship_partner": None, "conflict_flag": False}
CLIENT = {"existing_client": True, "relationship_partner": "priya_raman", "conflict_flag": False,
          "crm_company_name": "Halden Foods Ltd", "matched_on": "email_domain"}
CONFLICT = {**NO_CRM, "conflict_flag": True, "conflict_reason": "acts for the counterparty"}


def facts(**kw):
    base = dict(ok=True, primary_service_line="tax", complexity="moderate", inferred_urgency="normal", flags=[])
    base.update(kw)
    return ClassificationFacts(**base)


def go(domain, c=None, enrichment=NO_CRM, stated="normal"):
    return route(classification=c or facts(), enrichment=enrichment, stated_urgency=stated, domain=domain)


# --- each rule --------------------------------------------------------------

def test_default_routes_to_service_line_lead(domain):
    d = go(domain)
    assert (d.destination_type, d.destination_key) == ("lead", "tax")
    assert d.recipient["name"] == "Daniel Whitfield" and d.cc == []
    assert d.deciding_rule == "default"
    assert [s.rule for s in d.trace] == ["hard_stop_review", "archive_not_an_enquiry", "existing_client",
                                         "review_flags", "complex_high_urgency", "default"]
    assert [s.deciding for s in d.trace].count(True) == 1


def test_archive_not_an_enquiry(domain):
    d = go(domain, facts(primary_service_line="not_an_enquiry", flags=["spam"]))
    assert d.destination_type == "archive" and d.recipient is None
    assert d.deciding_rule == "archive_not_an_enquiry"


def test_existing_client_goes_to_partner_with_lead_cc(domain):
    d = go(domain, facts(primary_service_line="audit"), enrichment=CLIENT)
    assert (d.destination_type, d.destination_key) == ("partner", "priya_raman")
    assert [p["name"] for p in d.cc] == ["Margaret Osei"]


def test_review_flags_send_to_review(domain):
    d = go(domain, facts(flags=["vague"]))
    assert d.destination_type == "review" and d.review_reasons == ["Flag: vague"]


def test_complex_high_urgency_copies_practice_head(domain):
    d = go(domain, facts(complexity="complex", inferred_urgency="high"))
    assert (d.destination_type, d.destination_key) == ("lead", "tax")
    assert [p["key"] for p in d.cc] == ["practice_head"]
    d2 = go(domain, facts(complexity="complex", inferred_urgency="normal"), stated="high")
    assert d2.deciding_rule == "complex_high_urgency"
    assert go(domain, facts(complexity="moderate", inferred_urgency="high")).deciding_rule == "default"


# --- hard stops (D5) ----------------------------------------------------------

@pytest.mark.parametrize("c,enrichment,reason", [
    (facts(flags=["possible_injection"]), NO_CRM, "Possible prompt injection"),
    (facts(injection_flagged_any_run=True), NO_CRM, "Possible prompt injection"),
    (facts(), CONFLICT, "Conflict flag"),
    (ClassificationFacts(ok=False, error="classification_failed: boom"), NO_CRM, "Classification failed"),
])
def test_hard_stops(domain, c, enrichment, reason):
    d = go(domain, c, enrichment)
    assert d.destination_type == "review" and d.deciding_rule == "hard_stop_review"
    assert any(r.startswith(reason) for r in d.review_reasons)


# --- precedence ---------------------------------------------------------------

def test_hard_stop_beats_existing_client(domain):
    d = go(domain, facts(flags=["possible_injection"]), enrichment=CLIENT)
    assert d.destination_type == "review"
    d = go(domain, facts(), enrichment={**CLIENT, "conflict_flag": True})
    assert d.destination_type == "review"


def test_hard_stop_beats_archive(domain):
    d = go(domain, facts(primary_service_line="not_an_enquiry", flags=["spam", "possible_injection"]))
    assert d.destination_type == "review"


def test_archive_beats_existing_client(domain):
    d = go(domain, facts(primary_service_line="not_an_enquiry"), enrichment=CLIENT)
    assert d.destination_type == "archive"


def test_existing_client_beats_soft_review_flags(domain):
    d = go(domain, facts(flags=["vague"], agreement_service_line=2, agreement_text="2/3 agreed: tax vs deals"),
           enrichment=CLIENT)
    assert d.destination_type == "partner"
    assert any("Review triggers present" in n for n in d.notes)


def test_review_beats_complex_high(domain):
    d = go(domain, facts(complexity="complex", inferred_urgency="high", flags=["vague"]))
    assert d.destination_type == "review"


def test_never_routes_to_an_unknown_person(domain):
    # The model can only pick taxonomy ids (validated upstream); a 'CEO' has nowhere to go.
    names = {p["name"] for p in domain.routing["team_leads"].values()}
    for line in domain.service_line_ids:
        d = go(domain, facts(primary_service_line=line))
        assert d.recipient["name"] in names


# --- review triggers ------------------------------------------------------------

def test_trigger_service_line_disagreement(domain):
    d = go(domain, facts(agreement_service_line=2, agreement_text="2/3 agreed: tax vs deals"))
    assert d.destination_type == "review"
    assert d.review_reasons == ["Runs disagreed on service line (2/3 agreed: tax vs deals)"]


def test_complexity_disagreement_alone_does_not_trigger(domain):
    assert go(domain, facts()).destination_type == "lead"


@pytest.mark.parametrize("flag", ["vague", "out_of_scope", "spam"])
def test_trigger_each_non_exempt_flag(domain, flag):
    assert go(domain, facts(flags=[flag])).destination_type == "review"


def test_trigger_out_of_scope_primary(domain):
    d = go(domain, facts(primary_service_line="out_of_scope", flags=["out_of_scope"]))
    assert d.destination_type == "review" and d.review_reasons == ["Out of scope for the firm"]


@pytest.mark.parametrize("flag", ["urgency_mismatch", "multi_service"])
def test_exempt_flags_do_not_trigger(domain, flag):
    d = go(domain, facts(flags=[flag]))
    assert d.destination_type == "lead"
    assert any(flag.replace("_", " ") in n for n in d.notes)


def test_routing_reads_yaml_not_code(domain):
    import copy
    from dataclasses import replace
    routing = copy.deepcopy(domain.routing)
    routing["team_leads"]["tax"]["name"] = "Someone New"
    d = go(replace(domain, routing=routing))
    assert d.recipient["name"] == "Someone New"
