from datetime import datetime, timedelta

from sqlmodel import Session

from app.models import Enquiry, init_db, make_engine
from app.pipeline.enrich import MockCompanyData, MockCRMClient, enrich, normalise_company
from app.pipeline.intake import find_duplicate, normalise, text_hash


def test_normalise_keeps_words_collapses_space():
    assert normalise("  a\n\n b\t c ") == "a b c"


def test_hash_ignores_whitespace_and_case_but_not_email():
    h = text_hash("A@x.com", "Need  a VAT\ncheck")
    assert h == text_hash("a@x.com ", "need a vat check")
    assert h != text_hash("b@x.com", "need a vat check")


def _enq(id_, when, h="h1"):
    return Enquiry(id=id_, submitted_at=when, name="n", email="e@x.com", company_name="c", industry="Other",
                   company_size="1-10", stated_urgency="low", description="d", description_normalised="d",
                   text_hash=h)


def test_dedupe_window():
    engine = make_engine("sqlite://")
    init_db(engine)
    t0 = datetime(2026, 9, 1, 9)
    with Session(engine) as s:
        s.add(_enq("A", t0))
        s.add(_enq("B", t0, h="other"))
        s.commit()
        assert find_duplicate(s, text_hash_="h1", submitted_at=t0 + timedelta(days=6), window_days=7).id == "A"
        assert find_duplicate(s, text_hash_="h1", submitted_at=t0 + timedelta(days=8), window_days=7) is None
        assert find_duplicate(s, text_hash_="h2", submitted_at=t0 + timedelta(days=1), window_days=7) is None
        # excluding itself
        assert find_duplicate(s, text_hash_="h1", submitted_at=t0, window_days=7, exclude_id="A") is None


def test_company_name_normalisation():
    assert normalise_company("Halden Foods Ltd") == normalise_company("halden foods")
    assert normalise_company("Pell & Garrow Retail") == "pell and garrow retail"


def test_crm_matches_domain_and_name(domain):
    crm, cd = MockCRMClient(domain.crm), MockCompanyData(domain.crm)
    by_domain = enrich(email="x@haldenfoods.co.uk", company_name="Something else", company_size="201-1000",
                       industry="Retail & consumer", crm=crm, company_data=cd)
    assert by_domain["existing_client"] and by_domain["matched_on"] == "email_domain"
    assert by_domain["relationship_partner"] == "priya_raman" and by_domain["size_check"] == "match"
    by_name = enrich(email="someone@gmail.com", company_name="Northwind Logistics", company_size="201-1000",
                     industry="Transport & logistics", crm=crm, company_data=cd)
    assert by_name["existing_client"] and by_name["matched_on"] == "company_name"
    assert by_name["recorded_headcount"] == 1450 and by_name["size_check"] == "mismatch"


def test_free_email_domain_never_matches(domain):
    crm, cd = MockCRMClient(domain.crm), MockCompanyData(domain.crm)
    r = enrich(email="a@gmail.com", company_name="Unknown Co", company_size="1-10", industry="Other",
               crm=crm, company_data=cd)
    assert not r["existing_client"] and r["matched_on"] is None and r["size_check"] == "unknown"


def test_conflict_flag(domain):
    r = enrich(email="j@brightwatercap.com", company_name="Brightwater Capital Partners", company_size="51-200",
               industry="Financial services", crm=MockCRMClient(domain.crm), company_data=MockCompanyData(domain.crm))
    assert r["conflict_flag"] and not r["existing_client"]


def test_subsidiary_of_client_is_treated_as_client(domain):
    crm, cd = MockCRMClient(domain.crm), MockCompanyData(domain.crm)
    r = enrich(email="ops@brannigansbakery.co.uk", company_name="Brannigan's Bakery", company_size="51-200",
               industry="Retail & consumer", crm=crm, company_data=cd)
    assert r["existing_client"] and r["via_group"] and r["parent_group"] == "Halden Foods Ltd"
    assert r["relationship_partner"] == "priya_raman" and r["audit_client"]
    assert r["hq_country"] == "United Kingdom" and r["recorded_headcount"] == 58


def test_conflict_inherited_from_parent_and_richer_facts(domain):
    import copy
    from dataclasses import replace
    crm_data = copy.deepcopy(domain.crm)
    crm_data["companies"].append({"company_name": "Kestrel Spares Ltd", "email_domains": ["kestrelspares.co.uk"],
                                  "existing_client": False, "conflict_flag": False,
                                  "parent_group": "Kestrel Components Ltd"})
    d = replace(domain, crm=crm_data)
    r = enrich(email="a@kestrelspares.co.uk", company_name="Kestrel Spares", company_size="11-50", industry="Other",
               crm=MockCRMClient(d.crm), company_data=MockCompanyData(d.crm))
    assert r["conflict_flag"] and r["conflict_reason"].startswith("Via parent Kestrel Components Ltd")


def test_existing_client_carries_history(domain):
    r = enrich(email="x@haldenfoods.co.uk", company_name="Halden Foods", company_size="201-1000",
               industry="Retail & consumer", crm=MockCRMClient(domain.crm), company_data=MockCompanyData(domain.crm))
    assert r["client_since"] == 2017 and r["last_engagement"]["service_line"] == "audit"
    assert r["open_opportunities"] and r["annual_revenue_gbp_m"] == 182
