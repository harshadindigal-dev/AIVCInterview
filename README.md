# Intake Triage

A working prototype that reads inbound client enquiries for a professional services firm. It classifies each one by service line, complexity and urgency, enriches it from the CRM, and routes it to a team lead. Anything uncertain, conflicted or suspicious goes to a human review queue instead. Every routing decision shows the rules that produced it, and every human correction is recorded as feedback that can become evaluation data.

## Where to find what the brief asks for

| Brief asks for | Where it is |
|---|---|
| Prototype of the core classification and routing logic | `app/pipeline/` (`classify.py`, `route.py`, about 400 lines). Run it on one enquiry with `python -m scripts.triage E043`, or use the app (Inbox → any enquiry → *Why it went here*) |
| Classifies (service line, complexity) | `classify.py`. The prompt is generated from [`config/taxonomy.yaml`](config/taxonomy.yaml) and [`config/rubric.yaml`](config/rubric.yaml) |
| Enriches | `enrich.py` (CRM and company data, mocked behind interfaces), plus model-extracted entities, urgency and questions for the client |
| Routes to the right team lead | `route.py`, using the rules in [`config/routing.yaml`](config/routing.yaml) |
| Synthetic data | [`data/synthetic_enquiries.jsonl`](data/synthetic_enquiries.jsonl): 50 hand-labelled records, half of them deliberately hard |
| Evidence that it works, and where it doesn't | `/eval` page, the table below, and [`eval_results/failure_notes.md`](eval_results/failure_notes.md) |
| Architecture document | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Brief write-up | [WRITEUP.md](WRITEUP.md) |
| Trade-offs behind each choice | [DECISIONS.md](DECISIONS.md) |

## Quick start

```bash
cp .env.example .env              # add OPENROUTER_API_KEY
docker compose up --build         # or: make up
open http://localhost:8000
```

- **First start** seeds the database by running the 50 labelled enquiries through the live pipeline. That takes about 45s and costs about $2 with the default model. Pages fill in while it runs.
- **Later starts** reuse the database volume. `make reset-db` deletes it, and the next start re-seeds.
- **No key:** without `OPENROUTER_API_KEY` the container stops at startup with a message saying so. This build is live-only (DECISIONS.md D1).

The home page is the Inbox: every enquiry with its service line, complexity and destination, plus how many needed a human. **Try the hard cases:** go to **Submit enquiry** and open **Load sample → Hard cases**. Good ones to start with:
- *Prompt injection*: Fairbanks Motors and Lister Wholesale.
- *Conflict flag*: Brightwater Capital.
- *Vague*: Moorgate Payments.
- *Urgency mismatch*: Kestrel Wealth.

Submit any sample twice to see the duplicate check. Then open the enquiry: the **Why it went here** panel lists each routing rule in order and highlights the one that decided.

| Command | What it does |
|---|---|
| `make up` / `make down` | Start / stop (keeps the database) |
| `make test` | 69 tests in the image (one makes a live call, about $0.05) |
| `make eval` | Re-run the evaluation for all configured models (about $3) |
| `make reset-db` | Delete the database volume; the next `make up` re-seeds |
| `python -m scripts.triage E030` | Core logic only, no UI: classification, agreement, enrichment and routing trace for one enquiry (a dataset id or free text) |

## Live mode and models

All calls go through OpenRouter with the official `openai` SDK. Every request sends:
- `provider.require_parameters: true`, so the request only reaches providers that honour the JSON schema;
- `usage.include: true`, so each response reports tokens and cost.

The audit log stores the model slug and the provider OpenRouter actually used for every call.

| Variable | Default |
|---|---|
| `TRIAGE_MODEL` | `anthropic/claude-sonnet-5.5` |
| `EVAL_MODELS` | `anthropic/claude-haiku-5.5,google/gemini-3.8-flash` |
| `TRIAGE_FALLBACK_MODELS` | `google/gemini-3.8-flash` (app only, never in eval) |

Structured output uses a strict `response_format: json_schema` rather than a forced tool call. As of 2026-10-13, no OpenRouter provider for the current Claude models accepts a forced `tool_choice` under `require_parameters` (D6). Claude and GPT-5.6 also reject `temperature`, so the 3-run agreement check samples at the provider default for those models (D7).

## Evaluation results

Each model ran the full pipeline over 50 hand-labelled synthetic enquiries: 25 clear, 25 deliberately hard (see `data/`). Costs are as reported by OpenRouter, for 3 classification runs per enquiry.

| Model | Review recall | Auto-route precision | Service line | Complexity | Urgency | Review rate | Injection | Cost / 1,000 | p50 / p95 latency |
|---|---|---|---|---|---|---|---|---|---|
| **claude-sonnet-5.5** (default) | 100% (8/8) | 100% (37) | 100% | **90%** (4 off by one) | 98% | 17% | 2/2 flagged, not followed | $40.17 | 4.5s / 8.7s |
| claude-haiku-5.5 | 100% | 100% | 100% | 66% (14 off by one) | 69% | 17% | 2/2 | **$2.50** | 5.5s / 6.9s |
| gemini-3.8-flash | 100% | 100% | 100% | 73% (10 off by one, 1 off by two) | 89% | 17% | 2/2 | $19.21 | 9.9s / 35.9s |

What fails and why (full notes in `eval_results/failure_notes.md` and on the `/eval` page):
- **Routing looks perfect, but don't trust that yet.** The taxonomy's `not_this` notes and the confusable test cases have the same author. The eval checks that models follow written rules, not that they generalise. Treat 100% as an upper bound.
- **The 3-run agreement check never fired.** All runs agreed on all 48 triaged enquiries, for every model. Every review decision came from model flags or the CRM conflict flag. Whether disagreement catches anything is untested, and it is the first thing to measure in shadow mode.
- **Every complexity error goes the same way: up.** The model calls the job more complex than gold. The main cause is the rubric's "board-level stakeholders" criterion, which models apply whenever a board or committee is mentioned, together with "any single complex criterion is enough".
- **The cheaper model is good enough for routing but not for complexity.** Haiku matches Sonnet on every routing and safety metric at about 1/16th of the cost. Its complexity and urgency calls are much weaker, and those are what the team lead reads.
- **The complexity gold labels are single-labeller and debatable.** A few points of the complexity metric are labeller noise.

## Assumptions

All of these are editable in `config/`. Restart to apply; no rebuild is needed because `config/` is mounted read-only.
- **[Taxonomy](config/taxonomy.yaml):** seven service lines, plus `out_of_scope` and `not_an_enquiry`, each with "not this" notes to separate neighbours.
- **[Rubric](config/rubric.yaml):** complexity criteria from the brief, plus definitions of each urgency level and a rule for when to flag a mismatch.
- **[Routing](config/routing.yaml):** fictional team leads, practice head, relationship partners, rule order and review thresholds. One change from the brief: injection, conflict and classification failure are hard stops that run before the existing-client rule (D5).
- **[CRM](config/crm_clients.json):** seven fake companies, including two existing clients, two conflicts and two size mismatches.

## What would break first in production

See **[WRITEUP.md](WRITEUP.md)** (half a page): confident misroutes from taxonomy drift break first; reassignment rate per service line is the signal; hard stops, the review queue, one-click Reassign and a shadow → assisted rollout are the fallback.

## What I'd do with more time

- **A held-out test set**, written and labelled by someone other than the taxonomy author, plus a second labeller on complexity. Then fix the "board-level" rubric wording and measure the change on that set, not this one.
- **Test whether the agreement check works:** craft genuinely ambiguous enquiries, or lower the threshold to 2/3 and measure what it catches in shadow mode. If it catches nothing, drop it and save two-thirds of the cost.
- **Prompt caching** for the 4k-token system prompt. That is most of Sonnet's cost.
- **Embedding nearest-neighbour or fine-tuned classifier** once there are a few hundred feedback-labelled enquiries (`scripts/export_feedback.py`). Use it as a cheap first opinion, with the LLM as a second.
- **Real integrations:** CRM lookup, Slack and email notifications with signed Accept/Reassign links, and SSO.
