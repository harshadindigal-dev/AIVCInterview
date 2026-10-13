"""Run the evaluation for every configured model (TRIAGE_MODEL + EVAL_MODELS).

    python -m scripts.run_eval                     # all configured models
    python -m scripts.run_eval --models a/b,c/d    # specific models
    python -m scripts.run_eval --limit 5           # smoke test on the first 5 records

Writes eval_results/{model}_{prompt_version}.json. Costs real money: about
$2 for anthropic/claude-sonnet-5.5 over the 50-record dataset.
"""
from __future__ import annotations

import argparse
import json
import sys

from app.config import get_domain, get_settings
from app.evaluation import RESULTS_DIR, evaluate, load_dataset, results_path
from app.llm.client import OpenRouterClient


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", help="comma-separated OpenRouter slugs (default: TRIAGE_MODEL + EVAL_MODELS)")
    ap.add_argument("--limit", type=int, help="only the first N records (no results file written)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    settings = get_settings()
    settings.validate_for_startup()
    domain = get_domain()
    client = OpenRouterClient(settings.openrouter_api_key, app_url=settings.openrouter_app_url,
                              app_name=settings.openrouter_app_name, timeout=settings.llm_timeout_seconds)
    models = [m.strip() for m in args.models.split(",")] if args.models else settings.all_models
    records = load_dataset()
    if args.limit:
        records = records[: args.limit]

    RESULTS_DIR.mkdir(exist_ok=True)
    for model in models:
        print(f"== {model} ({len(records)} records)", flush=True)
        result = evaluate(client=client, domain=domain, model=model, records=records, workers=args.workers,
                          progress=lambda s: print(s, flush=True))
        m = result["metrics"]
        print(json.dumps({k: m[k] for k in ("service_line_accuracy", "complexity_accuracy", "routing_accuracy",
                                            "review_recall", "auto_route_precision", "review_rate",
                                            "cost_per_1000_usd", "latency_ms_p50")}, indent=1))
        print(f"   failures: {len(result['failures'])}  injection: all_flagged={m['injection']['all_flagged']} "
              f"none_followed={m['injection']['none_followed']}")
        if not args.limit:
            path = results_path(model)
            path.write_text(json.dumps(result, indent=1, default=str))
            print(f"   wrote {path.relative_to(RESULTS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
