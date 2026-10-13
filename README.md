# Intake Triage

A working prototype that reads inbound client enquiries for a professional services firm. It classifies each one by service line, complexity and urgency, enriches it from the CRM, and routes it to a team lead. Anything uncertain, conflicted or suspicious goes to a human review queue instead. Every routing decision shows the rules that produced it, and every human correction is recorded as feedback that can become evaluation data.

## Quick start

```bash
cp .env.example .env              # add OPENROUTER_API_KEY
docker compose up --build         # or: make up
open http://localhost:8000
```

- **First start** seeds the database by running the 50 labelled enquiries through the live pipeline. That takes about 45s and costs about $2 with the default model. Pages fill in while it runs.
- **Later starts** reuse the database volume. `make reset-db` deletes it, and the next start re-seeds.
- **No key:** without `OPENROUTER_API_KEY` the container stops at startup with a message saying so. This build is live-only (DECISIONS.md D1).

**Try the hard cases:** on the Submit page, open **Load sample → Hard cases**. Good ones to start with:
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

**Silent misroutes.** A confident, wrong classification goes straight to the wrong lead, and nobody notices until the client chases.
- *Monitor:* reassignment rate overall and per predicted service line (`/metrics`). Also the age of un-accepted auto-routed enquiries.
- *Fallback:* run in shadow and then assisted mode first (ARCHITECTURE.md). Every notification carries one-click Accept/Reassign links. Raise the review threshold for any line whose reassignment rate climbs.

**Taxonomy drift.** The firm launches an ESG practice, or "AI governance" requests start arriving. The model forces them into the nearest existing line, and the agreement check won't catch it because the runs will agree.
- *Monitor:* service-line mix over 7 and 30 days against the baseline. Reassignments into a line from many different predicted lines. A rising `out_of_scope` share.
- *Fallback:* the taxonomy is YAML and the prompt is regenerated from it. Add the line, re-run `make eval` on the labelled set plus exported feedback, and bump `PROMPT_VERSION`.

**Multi-service enquiries.** Routing picks one owner. A deal with a big tax component can sit with the deals lead while tax only finds out later.
- *Monitor:* the share of `multi_service` flags, and reassignments between the primary and secondary lines.
- *Fallback:* copy the secondary line's lead on multi-service enquiries. That is a one-rule change in `route.py`, deliberately not on by default, to avoid inbox noise.

**Form input quality.** One-liners, the wrong size band, urgency set to "high" by default. Stated fields are unreliable: two dataset companies misstate their size, and both urgency-mismatch cases contradict the form.
- *Monitor:* `vague` flag rate, the stated-vs-recorded size mismatches on the enrichment panel, and the urgency mismatch rate.
- *Fallback:* vague enquiries already go to review with "Questions to ask the client". If the rate is high, fix the form (required fields, examples) rather than the model.

**Dependence on the provider.** OpenRouter or the upstream provider is down or slow, or a model update changes behaviour silently.
- *Monitor:* classification failure rate, p95 latency, which model and provider answered (stored per call), and a weekly `make eval` against a pinned prompt version.
- *Fallback:* retry with backoff, then the OpenRouter fallback model, then `classification_failed` → review queue. Enquiries are never dropped, and at about 10 a day a human can triage by hand for a day. Pin model versions in production.

## What I'd do with more time

- **A held-out test set**, written and labelled by someone other than the taxonomy author, plus a second labeller on complexity. Then fix the "board-level" rubric wording and measure the change on that set, not this one.
- **Test whether the agreement check works:** craft genuinely ambiguous enquiries, or lower the threshold to 2/3 and measure what it catches in shadow mode. If it catches nothing, drop it and save two-thirds of the cost.
- **Prompt caching** for the 4k-token system prompt. That is most of Sonnet's cost.
- **Embedding nearest-neighbour or fine-tuned classifier** once there are a few hundred feedback-labelled enquiries (`scripts/export_feedback.py`). Use it as a cheap first opinion, with the LLM as a second.
- **Real integrations:** CRM lookup, Slack and email notifications with signed Accept/Reassign links, and SSO.
