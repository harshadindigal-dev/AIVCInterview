"""Enrichment behind interfaces. The mocks read config/crm_clients.json.

Production would add a second implementation of each interface (e.g. a
Dynamics/Salesforce CRM client and a Companies House / D&B company-data
client); nothing else changes.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any

FREE_EMAIL_DOMAINS = {"gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com",
                      "live.com", "aol.com", "proton.me", "protonmail.com"}
_SUFFIXES = r"\b(ltd|limited|plc|inc|llc|llp|gmbh|as|sa|bv)\b"


def normalise_company(name: str) -> str:
    n = name.lower().replace("&", " and ")
    n = re.sub(r"[^\w\s]", " ", n)
    n = re.sub(_SUFFIXES, " ", n)
    return " ".join(n.split())


def size_band(headcount: int) -> str:
    for upper, band in ((10, "1-10"), (50, "11-50"), (200, "51-200"), (1000, "201-1000")):
        if headcount <= upper:
            return band
    return "1000+"


@dataclass
class CRMResult:
    existing_client: bool
    relationship_partner: str | None     # key into routing.yaml relationship_partners
    conflict_flag: bool
    conflict_reason: str | None
    matched_on: str | None               # "email_domain" | "company_name" | None
    crm_company_name: str | None
    services_in_use: list[str] = field(default_factory=list)
    client_since: int | None = None
    last_engagement: dict[str, Any] | None = None
    open_opportunities: list[str] = field(default_factory=list)
    parent_group: str | None = None      # set when the company belongs to a group in the CRM
    via_group: bool = False              # client status / conflict inherited from the parent
    notes: str | None = None


@dataclass
class CompanyRecord:
    recorded_headcount: int | None
    recorded_industry: str | None
    hq_country: str | None = None
    annual_revenue_gbp_m: float | None = None
    ownership: str | None = None


class CRMClient(ABC):
    @abstractmethod
    def lookup(self, email_domain: str, company_name: str) -> CRMResult: ...


class CompanyData(ABC):
    @abstractmethod
    def lookup(self, company_name: str) -> CompanyRecord | None: ...


class _JsonCompanies:
    def __init__(self, crm: dict[str, Any]):
        self._companies = crm["companies"]

    def _match(self, email_domain: str | None, company_name: str | None) -> tuple[dict | None, str | None]:
        if email_domain and email_domain.lower() not in FREE_EMAIL_DOMAINS:
            for c in self._companies:
                if email_domain.lower() in [d.lower() for d in c["email_domains"]]:
                    return c, "email_domain"
        if company_name:
            target = normalise_company(company_name)
            for c in self._companies:
                if target and normalise_company(c["company_name"]) == target:
                    return c, "company_name"
        return None, None


class MockCRMClient(_JsonCompanies, CRMClient):
    def _by_name(self, name: str) -> dict | None:
        target = normalise_company(name)
        return next((c for c in self._companies if normalise_company(c["company_name"]) == target), None)

    def lookup(self, email_domain: str, company_name: str) -> CRMResult:
        c, how = self._match(email_domain, company_name)
        if c is None:
            return CRMResult(False, None, False, None, None, None)
        res = CRMResult(
            existing_client=bool(c["existing_client"]),
            relationship_partner=c.get("relationship_partner"),
            conflict_flag=bool(c["conflict_flag"]),
            conflict_reason=c.get("conflict_reason"),
            matched_on=how,
            crm_company_name=c["company_name"],
            services_in_use=list(c.get("services_in_use") or []),
            client_since=c.get("client_since"),
            last_engagement=c.get("last_engagement"),
            open_opportunities=list(c.get("open_opportunities") or []),
            parent_group=c.get("parent_group"),
            notes=c.get("notes"),
        )
        # A subsidiary of an existing client is part of that client: same partner,
        # same services (for independence checks), and the parent's conflicts apply.
        parent = self._by_name(c["parent_group"]) if c.get("parent_group") else None
        if parent:
            if parent.get("conflict_flag") and not res.conflict_flag:
                res.conflict_flag, res.via_group = True, True
                res.conflict_reason = f"Via parent {parent['company_name']}: {parent.get('conflict_reason')}"
            if parent.get("existing_client") and not res.existing_client:
                res.existing_client, res.via_group = True, True
                res.relationship_partner = parent.get("relationship_partner")
                res.services_in_use = res.services_in_use or list(parent.get("services_in_use") or [])
                res.client_since = res.client_since or parent.get("client_since")
                res.last_engagement = res.last_engagement or parent.get("last_engagement")
        return res


class MockCompanyData(_JsonCompanies, CompanyData):
    def lookup(self, company_name: str) -> CompanyRecord | None:
        c, _ = self._match(None, company_name)
        if c is None:
            return None
        return CompanyRecord(c.get("recorded_headcount"), c.get("recorded_industry"), c.get("hq_country"),
                             c.get("annual_revenue_gbp_m"), c.get("ownership"))


def enrich(*, email: str, company_name: str, company_size: str, industry: str,
           crm: CRMClient, company_data: CompanyData) -> dict[str, Any]:
    domain = email.rsplit("@", 1)[-1].lower()
    crm_res = crm.lookup(domain, company_name)
    rec = company_data.lookup(crm_res.crm_company_name or company_name)
    out: dict[str, Any] = {"email_domain": domain, **asdict(crm_res),
                           "recorded_headcount": None, "recorded_industry": None, "hq_country": None,
                           "annual_revenue_gbp_m": None, "ownership": None,
                           "recorded_size_band": None, "size_check": "unknown", "industry_check": "unknown"}
    out["audit_client"] = "audit" in out["services_in_use"]  # non-audit work needs an independence check
    if rec:
        out.update(recorded_headcount=rec.recorded_headcount, recorded_industry=rec.recorded_industry,
                   hq_country=rec.hq_country, annual_revenue_gbp_m=rec.annual_revenue_gbp_m, ownership=rec.ownership)
        if rec.recorded_headcount is not None:
            out["recorded_size_band"] = size_band(rec.recorded_headcount)
            out["size_check"] = "match" if out["recorded_size_band"] == company_size else "mismatch"
        if rec.recorded_industry:
            out["industry_check"] = "match" if rec.recorded_industry == industry else "mismatch"
    return out
