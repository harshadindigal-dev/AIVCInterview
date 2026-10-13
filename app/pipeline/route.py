"""Deterministic routing. Pure function: same inputs, same decision.

Rules come from routing.yaml and run in the order listed there. Every rule
that is evaluated is written to the trace with its outcome, so the UI can show
exactly why an enquiry went where it did. The first rule that fires decides.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.config import Domain


@dataclass
class ClassificationFacts:
    """What routing needs from classification (built from ClassificationOutcome,
    or by hand in tests)."""

    ok: bool
    primary_service_line: str | None = None
    complexity: str | None = None
    inferred_urgency: str | None = None
    flags: list[str] = field(default_factory=list)
    injection_flagged_any_run: bool = False
    agreement_service_line: int = 3
    n_runs: int = 3
    agreement_text: str = "3/3 runs agreed"
    error: str | None = None

    @classmethod
    def from_outcome(cls, o) -> "ClassificationFacts":
        r = o.result
        return cls(
            ok=o.ok,
            primary_service_line=r.primary_service_line if r else None,
            complexity=r.complexity if r else None,
            inferred_urgency=r.inferred_urgency if r else None,
            flags=list(r.flags) if r else [],
            injection_flagged_any_run=o.injection_flagged_any_run,
            agreement_service_line=o.agreement_service_line,
            n_runs=len(o.runs),
            agreement_text=o.disagreement_text() if o.ok else "classification failed",
            error=o.error,
        )


@dataclass
class TraceStep:
    rule: str
    description: str
    fired: bool
    detail: str
    deciding: bool = False


@dataclass
class RoutingDecision:
    destination_type: str                 # lead | partner | review | archive
    destination_key: str | None
    recipient: dict[str, Any] | None
    cc: list[dict[str, Any]]
    review_reasons: list[str]
    trace: list[TraceStep]
    notes: list[str]

    @property
    def destination_name(self) -> str:
        if self.destination_type == "review":
            return "Review queue"
        if self.destination_type == "archive":
            return "Archived"
        return self.recipient["name"] if self.recipient else "Unknown"

    @property
    def deciding_rule(self) -> str:
        return next(s.rule for s in self.trace if s.deciding)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["destination_name"] = self.destination_name
        d["deciding_rule"] = self.deciding_rule
        return d


def _person(section: dict[str, Any], key: str) -> dict[str, Any]:
    return {"key": key, **section[key]}


def review_triggers(c: ClassificationFacts, domain: Domain) -> list[str]:
    """Non-hard-stop review triggers (section 5.4), as human-readable reasons."""
    cfg = domain.routing["review"]
    reasons: list[str] = []
    if c.agreement_service_line < min(cfg["min_service_line_agreement"], c.n_runs):
        reasons.append(f"Runs disagreed on service line ({c.agreement_text})")
    if cfg.get("review_on_out_of_scope", True) and c.primary_service_line == "out_of_scope":
        reasons.append("Out of scope for the firm")
    exempt = set(cfg["exempt_flags"]) | set(domain.routing["hard_stops"]["flags"])
    for f in c.flags:
        if f not in exempt and not (f == "out_of_scope" and c.primary_service_line == "out_of_scope"):
            reasons.append(f"Flag: {f.replace('_', ' ')}")
    return reasons


def route(*, classification: ClassificationFacts, enrichment: dict[str, Any], stated_urgency: str,
          domain: Domain) -> RoutingDecision:
    cfg = domain.routing
    rules = {r["id"]: r["description"] for r in cfg["rules"]}
    c = classification
    trace: list[TraceStep] = []
    notes: list[str] = []
    line = c.primary_service_line

    for f in c.flags:
        if f in cfg["review"]["exempt_flags"]:
            notes.append(f"Flag '{f.replace('_', ' ')}' noted; it does not send an enquiry to review on its own.")

    def lead_cc() -> list[dict[str, Any]]:
        lead = domain.lead_for(line) if line else None
        return [lead] if lead else []

    def decide(rule_id: str, detail: str, **kw) -> RoutingDecision:
        trace.append(TraceStep(rule_id, rules[rule_id], True, detail, deciding=True))
        return RoutingDecision(trace=trace, notes=notes, **kw)

    def skip(rule_id: str, detail: str) -> None:
        trace.append(TraceStep(rule_id, rules[rule_id], False, detail))

    for rule_id in rules:
        if rule_id == "hard_stop_review":
            hs = cfg["hard_stops"]
            reasons = []
            if hs.get("on_classification_failure", True) and not c.ok:
                reasons.append(f"Classification failed ({c.error or 'unknown error'})")
            if "possible_injection" in hs["flags"] and (c.injection_flagged_any_run or "possible_injection" in c.flags):
                reasons.append("Possible prompt injection in the enquiry text")
            for f in hs["flags"]:
                if f != "possible_injection" and f in c.flags:
                    reasons.append(f"Flag: {f.replace('_', ' ')}")
            if hs.get("on_conflict_flag", True) and enrichment.get("conflict_flag"):
                reasons.append(f"Conflict flag: {enrichment.get('conflict_reason') or 'see CRM'}")
            if reasons:
                q = cfg["review_queue"]
                return decide(rule_id, "; ".join(reasons), destination_type="review", destination_key="review",
                              recipient={"key": "review", **q}, cc=[], review_reasons=reasons)
            skip(rule_id, "No injection flag, no conflict, classification succeeded")

        elif rule_id == "archive_not_an_enquiry":
            if line == "not_an_enquiry":
                return decide(rule_id, "Classified as not an enquiry (spam, job application or vendor pitch)",
                              destination_type="archive", destination_key="archive", recipient=None, cc=[],
                              review_reasons=[])
            skip(rule_id, f"Classified as {domain.line_name(line)}")

        elif rule_id == "existing_client":
            if enrichment.get("existing_client") and enrichment.get("relationship_partner"):
                partner = _person(cfg["relationship_partners"], enrichment["relationship_partner"])
                pending = review_triggers(c, domain)
                if pending:
                    notes.append("Review triggers present but the relationship partner owns existing-client "
                                 "enquiries: " + "; ".join(pending))
                return decide(rule_id, f"Existing client {enrichment.get('crm_company_name')} "
                                       f"(matched on {enrichment.get('matched_on', '').replace('_', ' ')}); "
                                       f"relationship partner {partner['name']}",
                              destination_type="partner", destination_key=partner["key"], recipient=partner,
                              cc=lead_cc(), review_reasons=[])
            skip(rule_id, "Not an existing client in CRM")

        elif rule_id == "review_flags":
            reasons = review_triggers(c, domain)
            if reasons:
                q = cfg["review_queue"]
                return decide(rule_id, "; ".join(reasons), destination_type="review", destination_key="review",
                              recipient={"key": "review", **q}, cc=[], review_reasons=reasons)
            skip(rule_id, f"{c.agreement_text}; no review flags")

        elif rule_id == "complex_high_urgency":
            high = "high" in (stated_urgency, c.inferred_urgency)
            if c.complexity == "complex" and high:
                lead = domain.lead_for(line)
                ph = {"key": "practice_head", **cfg["practice_head"]}
                which = "stated and inferred" if stated_urgency == c.inferred_urgency == "high" else (
                    "stated" if stated_urgency == "high" else "inferred")
                return decide(rule_id, f"Complex with high urgency ({which}); practice head copied",
                              destination_type="lead", destination_key=lead["key"], recipient=lead, cc=[ph],
                              review_reasons=[])
            skip(rule_id, f"Complexity {c.complexity}, urgency stated {stated_urgency} / inferred {c.inferred_urgency}")

        elif rule_id == "default":
            lead = domain.lead_for(line)
            if lead is None:  # unreachable with a consistent taxonomy; never drop the enquiry
                q = cfg["review_queue"]
                reason = f"No team lead configured for '{line}'"
                return decide(rule_id, reason, destination_type="review", destination_key="review",
                              recipient={"key": "review", **q}, cc=[], review_reasons=[reason])
            return decide(rule_id, f"{domain.line_name(line)} lead", destination_type="lead",
                          destination_key=lead["key"], recipient=lead, cc=[], review_reasons=[])

    raise RuntimeError("routing.yaml must end with the 'default' rule")
