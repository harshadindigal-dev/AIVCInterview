"""Enrichment behind interfaces. The mocks read config/crm_clients.json.

Production would add a second implementation of each interface (e.g. a
Dynamics/Salesforce CRM client and a Companies House / D&B company-data
client); nothing else changes.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
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


@dataclass
class CompanyRecord:
    recorded_headcount: int | None
    recorded_industry: str | None


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
    def lookup(self, email_domain: str, company_name: str) -> CRMResult:
        c, how = self._match(email_domain, company_name)
        if c is None:
            return CRMResult(False, None, False, None, None, None)
        return CRMResult(
            existing_client=bool(c["existing_client"]),
            relationship_partner=c.get("relationship_partner"),
            conflict_flag=bool(c["conflict_flag"]),
            conflict_reason=c.get("conflict_reason"),
            matched_on=how,
            crm_company_name=c["company_name"],
        )


class MockCompanyData(_JsonCompanies, CompanyData):
    def lookup(self, company_name: str) -> CompanyRecord | None:
        c, _ = self._match(None, company_name)
        if c is None:
            return None
        return CompanyRecord(c.get("recorded_headcount"), c.get("recorded_industry"))


def enrich(*, email: str, company_name: str, company_size: str, industry: str,
           crm: CRMClient, company_data: CompanyData) -> dict[str, Any]:
    domain = email.rsplit("@", 1)[-1].lower()
    crm_res = crm.lookup(domain, company_name)
    rec = company_data.lookup(crm_res.crm_company_name or company_name)
    out: dict[str, Any] = {"email_domain": domain, **asdict(crm_res),
                           "recorded_headcount": None, "recorded_industry": None,
                           "recorded_size_band": None, "size_check": "unknown", "industry_check": "unknown"}
    if rec:
        out["recorded_headcount"] = rec.recorded_headcount
        out["recorded_industry"] = rec.recorded_industry
        if rec.recorded_headcount is not None:
            out["recorded_size_band"] = size_band(rec.recorded_headcount)
            out["size_check"] = "match" if out["recorded_size_band"] == company_size else "mismatch"
        if rec.recorded_industry:
            out["industry_check"] = "match" if rec.recorded_industry == industry else "mismatch"
    return out
