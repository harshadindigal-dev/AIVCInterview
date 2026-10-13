"""Read, validate, preview and apply edits to the business-logic config.

Every edit goes through the same path, whether it came from a structured form
or the raw editor:

    proposed text -> parse -> validate -> preview (diff, prompt diff, routing impact) -> apply

Applying writes the file atomically, records a ConfigChange row with the full
before/after text, and reloads the domain in the running app (no restart).
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML
from sqlmodel import Session, select

from app.config import Domain, get_settings
from app.llm.prompts import build_system_prompt
from app.models import ConfigChange, Enquiry, PipelineRun
from app.pipeline.enrich import MockCompanyData, MockCRMClient, enrich
from app.pipeline.route import KNOWN_RULES, facts_from_audit, route
from app.schemas import COMPLEXITIES, FLAGS, URGENCIES

FILES: dict[str, tuple[str, str, str]] = {
    # key: (filename, format, what it controls)
    "taxonomy": ("taxonomy.yaml", "yaml", "Service lines and non-service outcomes. Changes the system prompt."),
    "rubric": ("rubric.yaml", "yaml", "Complexity criteria and urgency levels. Changes the system prompt."),
    "routing": ("routing.yaml", "yaml", "Team leads, rule order, hard stops and review thresholds. Changes routing."),
    "crm": ("crm_clients.json", "json", "Mock CRM: existing clients, partners, conflicts. Changes enrichment and routing."),
}
REQUIRED_OUTCOMES = {"out_of_scope", "not_an_enquiry"}  # the router depends on these ids
_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def config_dir() -> Path:
    return Path(get_settings().config_dir)


def path_for(name: str) -> Path:
    return config_dir() / FILES[name][0]


def read_text(name: str) -> str:
    return path_for(name).read_text()


def is_writable() -> bool:
    d = config_dir()
    return os.access(d, os.W_OK) and all(os.access(d / f[0], os.W_OK) for f in FILES.values())


def parse(name: str, text: str) -> Any:
    fmt = FILES[name][1]
    try:
        data = json.loads(text) if fmt == "json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as e:
        raise ValueError(f"{FILES[name][0]} is not valid {fmt.upper()}: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{FILES[name][0]} must be a mapping at the top level")
    return data


def prompt_hash(domain: Domain) -> str:
    return hashlib.sha256(build_system_prompt(domain).encode()).hexdigest()[:12]


def config_hash(domain: Domain) -> str:
    blob = json.dumps([domain.taxonomy, domain.rubric, domain.routing, domain.crm], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:12]


# -- validation ------------------------------------------------------------------

def validate(d: Domain) -> list[str]:
    """Everything the code relies on. Returns human-readable errors (empty = valid)."""
    errors: list[str] = []
    tax, rub, rt, crm = d.taxonomy, d.rubric, d.routing, d.crm

    # taxonomy
    lines = tax.get("service_lines") or []
    outcomes = tax.get("non_service_outcomes") or []
    if not lines:
        errors.append("taxonomy: at least one service line is required")
    seen: set[str] = set()
    for i, s in enumerate(lines + outcomes):
        where = f"taxonomy entry {i + 1}"
        if not isinstance(s, dict):
            errors.append(f"{where}: must be a mapping")
            continue
        sid = s.get("id")
        if not sid or not _ID_RE.match(str(sid)):
            errors.append(f"{where}: id must be lower_snake_case (got {sid!r})")
        elif sid in seen:
            errors.append(f"taxonomy: duplicate id '{sid}'")
        seen.add(str(sid))
        for k in ("name", "description"):
            if not str(s.get(k) or "").strip():
                errors.append(f"taxonomy '{sid}': {k} is required")
        for k in ("typical_requests", "not_this"):
            if not isinstance(s.get(k), list) or not s.get(k):
                errors.append(f"taxonomy '{sid}': {k} must be a non-empty list")
    missing = REQUIRED_OUTCOMES - {o.get("id") for o in outcomes if isinstance(o, dict)}
    if missing:
        errors.append(f"taxonomy: non_service_outcomes must include {sorted(missing)}")
    line_ids = [s.get("id") for s in lines if isinstance(s, dict)]

    # rubric
    levels = [c.get("level") for c in rub.get("complexity") or [] if isinstance(c, dict)]
    if levels != COMPLEXITIES:
        errors.append(f"rubric: complexity levels must be exactly {COMPLEXITIES} in that order (the output schema "
                      f"is fixed); got {levels}")
    for c in rub.get("complexity") or []:
        if isinstance(c, dict) and not c.get("criteria"):
            errors.append(f"rubric: level '{c.get('level')}' needs at least one criterion")
    ulevels = [u.get("level") for u in (rub.get("urgency") or {}).get("levels") or [] if isinstance(u, dict)]
    if ulevels != URGENCIES:
        errors.append(f"rubric: urgency levels must be exactly {URGENCIES}; got {ulevels}")
    if not str((rub.get("urgency") or {}).get("mismatch_rule") or "").strip():
        errors.append("rubric: urgency.mismatch_rule is required")

    # routing
    leads = rt.get("team_leads") or {}
    for key, p in leads.items():
        for k in ("name", "email", "slack"):
            if not str((p or {}).get(k) or "").strip():
                errors.append(f"routing: team lead '{key}' needs a {k}")
    table = rt.get("routing_table") or {}
    for lid in line_ids:
        if lid not in table:
            errors.append(f"routing: service line '{lid}' has no entry in routing_table")
        elif table[lid] not in leads:
            errors.append(f"routing: routing_table['{lid}'] points to unknown team lead '{table[lid]}'")
    for lid in table:
        if lid not in line_ids:
            errors.append(f"routing: routing_table has '{lid}', which is not a service line in the taxonomy")
    for person in ("practice_head", "review_queue"):
        if not isinstance(rt.get(person), dict) or not rt[person].get("name"):
            errors.append(f"routing: {person} is required")
    rule_ids = [r.get("id") for r in rt.get("rules") or [] if isinstance(r, dict)]
    unknown = [r for r in rule_ids if r not in KNOWN_RULES]
    if unknown:
        errors.append(f"routing: unknown rule ids {unknown}; known rules are {sorted(KNOWN_RULES)}")
    if len(set(rule_ids)) != len(rule_ids):
        errors.append("routing: a rule appears more than once")
    if not rule_ids or rule_ids[-1] != "default":
        errors.append("routing: the last rule must be 'default' so every enquiry has a destination")
    rv = rt.get("review") or {}
    msa = rv.get("min_service_line_agreement")
    if not isinstance(msa, int) or not 1 <= msa <= 3:
        errors.append("routing: review.min_service_line_agreement must be 1, 2 or 3")
    for k in ("exempt_flags",):
        bad = [f for f in rv.get(k) or [] if f not in FLAGS]
        if bad:
            errors.append(f"routing: review.{k} has unknown flags {bad}")
    hs = rt.get("hard_stops") or {}
    bad = [f for f in hs.get("flags") or [] if f not in FLAGS]
    if bad:
        errors.append(f"routing: hard_stops.flags has unknown flags {bad}")
    dw = rt.get("dedupe_window_days")
    if not isinstance(dw, int) or dw < 0:
        errors.append("routing: dedupe_window_days must be a whole number of days")

    # crm
    partners = rt.get("relationship_partners") or {}
    for c in crm.get("companies") or []:
        rp = c.get("relationship_partner")
        if c.get("existing_client") and rp not in partners:
            errors.append(f"crm: {c.get('company_name')!r} is an existing client but relationship_partner "
                          f"{rp!r} is not in routing.yaml relationship_partners")
    names = {str(c.get("company_name", "")).lower() for c in crm.get("companies") or []}
    for c in crm.get("companies") or []:
        pg = c.get("parent_group")
        if pg and str(pg).lower() not in names:
            errors.append(f"crm: {c.get('company_name')!r} has parent_group {pg!r}, which is not a company in the CRM")
    if not errors:
        try:
            build_system_prompt(d)
        except Exception as e:  # anything the checks above missed
            errors.append(f"prompt could not be built: {type(e).__name__}: {e}")
    return errors


# -- preview ------------------------------------------------------------------------

@dataclass
class Preview:
    name: str
    text: str
    errors: list[str]
    diff: str
    unchanged: bool
    prompt_changed: bool = False
    prompt_diff: str = ""
    prompt_hash_before: str = ""
    prompt_hash_after: str = ""
    impact: list[dict[str, Any]] = field(default_factory=list)
    impact_checked: int = 0
    warnings: list[str] = field(default_factory=list)


def _diff(a: str, b: str, fa: str, fb: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fa, fb, n=2))


def draft_domain(current: Domain, name: str, text: str) -> Domain:
    return replace(current, **{name: parse(name, text)})


def preview(session: Session, current: Domain, name: str, text: str) -> Preview:
    before = read_text(name)
    fname = FILES[name][0]
    pv = Preview(name=name, text=text, errors=[], diff=_diff(before, text, f"{fname} (live)", f"{fname} (draft)"),
                 unchanged=before == text)
    try:
        draft = draft_domain(current, name, text)
    except ValueError as e:
        pv.errors = [str(e)]
        return pv
    pv.errors = validate(draft)
    if pv.errors:
        return pv
    old_prompt, new_prompt = build_system_prompt(current), build_system_prompt(draft)
    pv.prompt_hash_before, pv.prompt_hash_after = prompt_hash(current), prompt_hash(draft)
    pv.prompt_changed = old_prompt != new_prompt
    if pv.prompt_changed:
        pv.prompt_diff = _diff(old_prompt, new_prompt, "system prompt (live)", "system prompt (draft)")
        pv.warnings.append("The system prompt changes. Stored classifications are not re-run, and evaluation "
                           "results now describe the old prompt: re-run `make eval` to measure the new one.")
        removed = set(current.outcome_ids) - set(draft.outcome_ids)
        if removed:
            n = len(session.exec(select(Enquiry).where(Enquiry.service_line.in_(removed))).all())
            if n:
                pv.warnings.append(f"{n} existing enquiries are classified under removed ids {sorted(removed)}. "
                                   "They keep their labels; reassign them by hand.")
    pv.impact, pv.impact_checked = routing_impact(session, current, draft)
    return pv


def routing_impact(session: Session, current: Domain, draft: Domain) -> tuple[list[dict[str, Any]], int]:
    """Replay routing for every triaged enquiry under the live and the draft config,
    using the stored classifications (no LLM calls), and list those that differ."""
    runs = {r.enquiry_id: r for r in session.exec(select(PipelineRun).order_by(PipelineRun.id)).all()}
    crm_now, cd_now = MockCRMClient(current.crm), MockCompanyData(current.crm)
    crm_new, cd_new = MockCRMClient(draft.crm), MockCompanyData(draft.crm)
    changes, checked = [], 0
    for e in session.exec(select(Enquiry).where(Enquiry.duplicate_of.is_(None))).all():
        run = runs.get(e.id)
        if run is None or not run.classification:
            continue
        checked += 1
        facts = facts_from_audit(run.classification, run.parsed)
        kw = dict(email=e.email, company_name=e.company_name, company_size=e.company_size, industry=e.industry)
        a = route(classification=facts, enrichment=enrich(**kw, crm=crm_now, company_data=cd_now),
                  stated_urgency=e.stated_urgency, domain=current)
        b = route(classification=facts, enrichment=enrich(**kw, crm=crm_new, company_data=cd_new),
                  stated_urgency=e.stated_urgency, domain=draft)
        before = (a.destination_type, a.destination_key, a.destination_name, sorted(p["name"] for p in a.cc))
        after = (b.destination_type, b.destination_key, b.destination_name, sorted(p["name"] for p in b.cc))
        if before != after:
            changes.append({"id": e.id, "company": e.company_name,
                            "before": a.destination_name + (f" (cc {', '.join(before[3])})" if before[3] else ""),
                            "after": b.destination_name + (f" (cc {', '.join(after[3])})" if after[3] else ""),
                            "rule_before": a.deciding_rule, "rule_after": b.deciding_rule})
    return changes, checked


# -- apply ---------------------------------------------------------------------------

def apply(session: Session, current: Domain, name: str, text: str, note: str | None) -> ConfigChange:
    pv = preview(session, current, name, text)
    if pv.errors:
        raise ValueError("; ".join(pv.errors))
    path = path_for(name)
    before = path.read_text()
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(fd, "w") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    os.replace(tmp, path)
    draft = draft_domain(current, name, text)
    change = ConfigChange(file=name, note=(note or "").strip() or None, before=before, after=path.read_text(),
                          prompt_hash_before=pv.prompt_hash_before, prompt_hash_after=pv.prompt_hash_after,
                          config_hash_after=config_hash(draft), routing_changes=len(pv.impact))
    session.add(change)
    session.commit()
    return change


# -- structured edits (comment-preserving) ----------------------------------------------

def _rt() -> YAML:
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


def _edit_yaml(name: str, mutate) -> str:
    y = _rt()
    data = y.load(read_text(name))
    mutate(data)
    buf = io.StringIO()
    y.dump(data, buf)
    return buf.getvalue()


def _lines(text: str) -> list[str]:
    return [ln.strip() for ln in (text or "").splitlines() if ln.strip()]


def routing_from_form(form: dict[str, Any]) -> str:
    """Team leads, line -> lead mapping, review thresholds and hard stops."""
    def mutate(d):
        for key, lead in d["team_leads"].items():
            for k in ("name", "email", "slack"):
                v = form.get(f"lead__{key}__{k}")
                if v is not None:
                    lead[k] = v.strip()
        for line in list(d["routing_table"].keys()):
            v = form.get(f"table__{line}")
            if v:
                d["routing_table"][line] = v
        for k in ("name", "email", "slack"):
            v = form.get(f"practice_head__{k}")
            if v is not None:
                d["practice_head"][k] = v.strip()
        rv = d["review"]
        if form.get("min_service_line_agreement"):
            rv["min_service_line_agreement"] = int(form["min_service_line_agreement"])
        rv["exempt_flags"] = [f for f in FLAGS if form.get(f"exempt__{f}")]
        rv["review_on_out_of_scope"] = bool(form.get("review_on_out_of_scope"))
        hs = d["hard_stops"]
        hs["flags"] = ["possible_injection"] if form.get("hard_stop_injection") else []
        hs["on_conflict_flag"] = bool(form.get("hard_stop_conflict"))
        hs["on_classification_failure"] = bool(form.get("hard_stop_failure"))
        if form.get("dedupe_window_days") not in (None, ""):
            d["dedupe_window_days"] = int(form["dedupe_window_days"])
    return _edit_yaml("routing", mutate)


def taxonomy_from_form(form: dict[str, Any]) -> str:
    """Name, description, typical requests and 'not this' notes for each existing entry."""
    def mutate(d):
        for entry in list(d["service_lines"]) + list(d["non_service_outcomes"]):
            sid = entry["id"]
            if f"{sid}__name" not in form:
                continue
            entry["name"] = form[f"{sid}__name"].strip()
            entry["description"] = " ".join(form[f"{sid}__description"].split())
            entry["typical_requests"] = _lines(form[f"{sid}__typical_requests"])
            entry["not_this"] = _lines(form[f"{sid}__not_this"])
    return _edit_yaml("taxonomy", mutate)


def rubric_from_form(form: dict[str, Any]) -> str:
    def mutate(d):
        for c in d["complexity"]:
            key = f"cx__{c['level']}"
            if key in form:
                c["criteria"] = _lines(form[key])
        if "complexity_notes" in form:
            d["complexity_notes"] = _lines(form["complexity_notes"])
        for u in d["urgency"]["levels"]:
            key = f"urg__{u['level']}"
            if key in form:
                u["meaning"] = " ".join(form[key].split())
        if "mismatch_rule" in form:
            d["urgency"]["mismatch_rule"] = " ".join(form["mismatch_rule"].split())
    return _edit_yaml("rubric", mutate)


def history(session: Session, limit: int = 50) -> list[ConfigChange]:
    return session.exec(select(ConfigChange).order_by(ConfigChange.created_at.desc()).limit(limit)).all()
