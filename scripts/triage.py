"""Run the core classify -> route logic on one enquiry, no UI or database.

    python -m scripts.triage E043                      # a dataset record by id
    python -m scripts.triage "We need an R&D tax claim" --size 11-50 --urgency normal
    python -m scripts.triage E026 --model anthropic/claude-haiku-5.5 --json

Prints the classification, the agreement across runs, the enrichment, and the
routing trace. Makes live OpenRouter calls (about $0.04 with the default model).
"""
from __future__ import annotations

import argparse
import json
import sys

from app.config import get_domain, get_settings
from app.evaluation import load_dataset
from app.llm.client import OpenRouterClient
from app.pipeline.classify import classify
from app.pipeline.enrich import MockCompanyData, MockCRMClient, enrich
from app.pipeline.route import ClassificationFacts, route
from app.schemas import EnquiryIn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("enquiry", help="dataset id (e.g. E043) or free text")
    ap.add_argument("--company", default="Example Ltd")
    ap.add_argument("--email", default="client@example.co.uk")
    ap.add_argument("--industry", default="Other")
    ap.add_argument("--size", default="51-200")
    ap.add_argument("--urgency", default="normal")
    ap.add_argument("--model", help="OpenRouter slug (default: TRIAGE_MODEL)")
    ap.add_argument("--json", action="store_true", help="print machine-readable output")
    args = ap.parse_args()

    settings, domain = get_settings(), get_domain()
    settings.validate_for_startup()
    records = {r["id"]: r for r in load_dataset()}
    if args.enquiry in records:
        r = records[args.enquiry]
        form = {k: r[k] for k in ("name", "email", "company_name", "industry", "company_size", "stated_urgency",
                                  "description")}
        gold = r["gold"]
    else:
        form = dict(name="CLI", email=args.email, company_name=args.company, industry=args.industry,
                    company_size=args.size, stated_urgency=args.urgency, description=args.enquiry)
        gold = None
    EnquiryIn(**form)  # same validation as the web form

    model = args.model or settings.triage_model
    client = OpenRouterClient(settings.openrouter_api_key, app_url=settings.openrouter_app_url,
                              app_name=settings.openrouter_app_name)
    enrichment = enrich(email=form["email"], company_name=form["company_name"], company_size=form["company_size"],
                        industry=form["industry"], crm=MockCRMClient(domain.crm),
                        company_data=MockCompanyData(domain.crm))
    outcome = classify(client=client, domain=domain, model=model, enquiry=form)
    decision = route(classification=ClassificationFacts.from_outcome(outcome), enrichment=enrichment,
                     stated_urgency=form["stated_urgency"], domain=domain)

    if args.json:
        print(json.dumps({"classification": outcome.result.model_dump() if outcome.result else None,
                          "agreement": outcome.disagreement_text() if outcome.ok else None,
                          "enrichment": enrichment, "routing": decision.to_dict(), "gold": gold,
                          "cost_usd": outcome.cost_usd, "provider": outcome.provider}, indent=2, default=str))
        return 0

    r = outcome.result
    print(f"\n{form['company_name']} · {form['industry']} · {form['company_size']} · stated urgency {form['stated_urgency']}")
    print(f"model {model} via {outcome.provider} · {outcome.latency_ms / 1000:.1f}s · ${outcome.cost_usd or 0:.4f}\n")
    if r:
        sec = f" (+ {', '.join(r.secondary_service_lines)})" if r.secondary_service_lines else ""
        print(f"  Summary      {r.summary}")
        print(f"  Service line {r.primary_service_line}{sec}   [{outcome.disagreement_text()}]")
        print(f"  Complexity   {r.complexity} — {r.complexity_rationale}")
        print(f"  Urgency      stated {form['stated_urgency']}, inferred {r.inferred_urgency}")
        print(f"  Flags        {', '.join(r.flags) or 'none'}")
        for q in r.missing_information:
            print(f"  Ask client   {q}")
    else:
        print(f"  Classification failed: {outcome.error}")
    print(f"\n  CRM          existing client: {enrichment['existing_client']}, conflict: {enrichment['conflict_flag']}")
    print("\n  Routing trace")
    for step in decision.trace:
        mark = ">>" if step.deciding else ("x " if step.fired else "  ")
        print(f"   {mark} {step.rule:<24} {step.detail}")
    cc = f" (cc {', '.join(c['name'] for c in decision.cc)})" if decision.cc else ""
    print(f"\n  => {decision.destination_name}{cc}")
    if gold:
        print(f"\n  Gold: {gold['primary_service_line']} / {gold['complexity']} / should_review={gold['should_review']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
