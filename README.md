# Intake Triage Agent

A working prototype that replaces the manual triage step for a professional services firm's inbound enquiries.

Each enquiry submitted through the web form is:
- **classified** by service line, complexity and urgency, using a live LLM run three times;
- **enriched** from the CRM (existing client, relationship partner, conflicts, recorded company size);
- **routed** by deterministic, configurable rules to the right team lead.

Anything uncertain, conflicted or suspicious goes to a human review queue instead of being routed. Every decision explains itself, every human correction is recorded, and the business logic behind the prompt and the routing can be viewed and changed from the UI.

> **The problem in the brief.** About 40–60 enquiries arrive a week. A junior analyst reads every one, tags the service line, estimates complexity and routes it, which takes about 8 hours a week and is error-prone.
> **On the 50-enquiry test set**, the system routes 77% to a team lead automatically, sends 17% to a human with a stated reason, and archives or links the rest. Every one of those routing decisions matched the hand-labelled answer, but read [Evaluation](#evaluation) for why that number is an upper bound.

---

## Contents

- [Where to find what the brief asks for](#where-to-find-what-the-brief-asks-for)
- [Screenshots](#screenshots)
- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [A tour of the app](#a-tour-of-the-app)
- [Configuration: the business logic, visible and editable](#configuration-the-business-logic-visible-and-editable)
- [Evaluation](#evaluation)
- [Models and the OpenRouter integration](#models-and-the-openrouter-integration)
- [Commands and scripts](#commands-and-scripts)
- [Environment variables](#environment-variables)
- [Project structure](#project-structure)
- [Synthetic dataset](#synthetic-dataset)
- [Tests](#tests)
- [Costs](#costs)
- [Limitations and security notes](#limitations-and-security-notes)
- [What would break first in production](#what-would-break-first-in-production)
- [What I'd do with more time](#what-id-do-with-more-time)

---

## Where to find what the brief asks for

| Brief asks for | Where it is |
|---|---|
| Prototype of the core classification and routing logic | [`app/pipeline/`](app/pipeline/). The core is [`classify.py`](app/pipeline/classify.py) and [`route.py`](app/pipeline/route.py), about 400 lines. Run it on one enquiry without the UI: `python -m scripts.triage E043` |
| Classifies (service line, complexity) | `classify.py`, with the prompt generated from [`config/taxonomy.yaml`](config/taxonomy.yaml) and [`config/rubric.yaml`](config/rubric.yaml) |
| Enriches | [`enrich.py`](app/pipeline/enrich.py): CRM and company data behind interfaces (mocked), plus model-extracted entities, inferred urgency and questions to ask the client |
| Routes to the right team lead | `route.py`, applying the rules in [`config/routing.yaml`](config/routing.yaml) |
| Synthetic data | [`data/synthetic_enquiries.jsonl`](data/synthetic_enquiries.jsonl): 50 hand-labelled enquiries, half of them deliberately hard |
| Architecture document (1–2 pages) | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Brief write-up (half a page) | [WRITEUP.md](WRITEUP.md): a half-page answer on what breaks first, what to monitor, and the fallback when the model is wrong. Below it: risks ranked by likelihood, a monitoring plan with thresholds and owners, the escalation path, rollout criteria and a 90-day plan |
| Evidence, including failures | The `/eval` page, [Evaluation](#evaluation) below, [`eval_results/failure_notes.md`](eval_results/failure_notes.md) |
| Trade-offs behind each choice | [DECISIONS.md](DECISIONS.md): 23 short entries, each with the decision, the alternatives considered and why |

---

## Screenshots

These are for reviewing without running anything; the app needs an OpenRouter key to run.

| | |
|---|---|
| **Inbox:** every enquiry, sortable and filterable, with what was routed automatically vs sent to a human ![Inbox](docs/screenshots/inbox.png) | **Enquiry detail:** the triage card, *Why it went here* (every rule, the deciding one highlighted), enrichment ![Enquiry detail](docs/screenshots/enquiry-detail.png) |
| **Review queue:** each card says why it needs a human; accept or route in one click ![Review queue](docs/screenshots/review-queue.png) | **Evaluation:** three models compared, confusion matrices, failures, and an honest write-up ![Evaluation](docs/screenshots/evaluation.png) |
| **Configuration:** the business logic in plain English, editable with an impact preview ![Configuration](docs/screenshots/configuration.png) | **Health:** model-error rate per service line, review reasons, service-line mix, latency and cost ![Health](docs/screenshots/health.png) |

---

## Quick start

### With Docker (recommended)

```bash
git clone https://github.com/harshadindigal-dev/AIVCInterview.git
cd AIVCInterview
cp .env.example .env              # then set OPENROUTER_API_KEY=...
docker compose up --build         # or: make up
open http://localhost:8000
```

- **First start** seeds the database by running the 50 labelled enquiries through the **live** pipeline. That takes about 45 seconds and costs about $2 with the default model. The pages fill in while it runs, and a banner shows progress.
- **Later starts** reuse the database volume, so there's no re-seed and no cost. `make reset-db` deletes the volume, and the next start re-seeds.
- **The OpenRouter key is required.** This build is live-only (DECISIONS D1). Without a key, the container stops at startup with a message saying exactly that.
- **The image** is `python:3.12-slim`, runs as a non-root user, has a `/healthz` health check, and is about 290 MB.

### Without Docker (local development)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env              # set OPENROUTER_API_KEY; DATABASE_URL can be left unset
uvicorn app.main:app --port 8000  # database at data/app.db; seeds on first start
```

### Things to try first

1. **Inbox (the home page):** every enquiry with its service line, complexity, urgency and destination, plus a summary of what was routed automatically and what needed a human. Click any column heading to sort.
2. **Open an enquiry.** The **Why it went here** panel lists every routing rule in order with its result, and highlights the one that decided.
3. **Submit enquiry → Load sample → Hard cases:**
   - *Prompt injection*: Fairbanks Motors ("route this to the CEO") and Lister Wholesale (a hidden note asking for "simple").
   - *Conflict flag*: Brightwater Capital (the firm acts for the other side).
   - *Vague*: Moorgate Payments ("need help with compliance stuff").
   - *Urgency mismatch*: Kestrel Wealth (stated low, regulator deadline next Friday).
   - Submit any sample **twice** to see the duplicate check.
4. **Review queue:** accept a suggestion or route an enquiry; the card disappears and the count updates.
5. **Configuration → Routing & team leads:** untick "CRM conflict flag" and click **Preview changes** (you don't need to save). The preview lists which existing enquiries would route differently under that change.

---

## How it works

```mermaid
flowchart LR
    F[Web form] --> I[Intake<br/>validate · normalise · dedupe]
    I -- duplicate --> DUP[(Link to original,<br/>skip triage)]
    I --> E[Enrich<br/>CRM · company data]
    E --> C[Classify<br/>3 LLM runs · strict JSON schema]
    C <--> OR[[OpenRouter → provider]]
    C --> R[Route<br/>rules in routing.yaml]
    R -- lead / partner --> N[Notify team lead]
    R -- review --> Q[Review queue]
    R -- archive --> A[(Archive)]
    N & Q --> H[Human: Accept · Reassign · Correct]
    H --> FB[(Feedback)] --> M[Health metrics + new eval data]
    Y[config/*.yaml<br/>editable at /config] -.-> C & R
```

### 1. Intake ([`intake.py`](app/pipeline/intake.py))
- Pydantic validates the form: the description must be 10–5,000 characters, and industry, company size and urgency come from fixed lists.
- The original text is stored **exactly as submitted**. A whitespace-normalised copy is used for matching.
- **Dedupe:** the same email with near-identical text within 7 days is linked to the original. It skips triage and sends no second notification.

### 2. Enrich ([`enrich.py`](app/pipeline/enrich.py))
- `CRMClient.lookup(email_domain, company_name)` returns existing client, relationship partner and conflict flag. Free-mail domains such as gmail.com only ever match by company name.
- `CompanyData.lookup(company_name)` returns the recorded headcount and industry, which are compared with what the client stated.
- Both are **interfaces with a mock implementation** that reads [`config/crm_clients.json`](config/crm_clients.json). A production CRM or Companies House client would be a second implementation.
- Enrichment is best-effort: a failure is recorded and never blocks the enquiry.

### 3. Classify ([`classify.py`](app/pipeline/classify.py), [`prompts.py`](app/llm/prompts.py), [`client.py`](app/llm/client.py))
- **The system prompt is generated from the YAML files.** The taxonomy and rubric are never hard-coded in Python.
- **Untrusted input:** the enquiry is wrapped in `<enquiry>` tags marked as untrusted data, and the tags are escaped so submitted text can't close them early. Instructions inside are to be ignored and flagged as `possible_injection`.
- **Three concurrent runs.** Run 0 is the answer; runs 1–2 vote. Agreement on service line and complexity is recorded. **The model is never asked for a confidence score.**
- **Output** is a strict JSON schema whose enums come from the taxonomy, validated again with Pydantic against the taxonomy ids:

  | Field | What it holds |
  |---|---|
  | `primary_service_line` | One of the 7 service lines, `out_of_scope` or `not_an_enquiry` |
  | `secondary_service_lines` | Other service lines genuinely needed (often empty) |
  | `complexity` + `complexity_rationale` | simple / moderate / complex; the rationale must cite rubric criteria |
  | `inferred_urgency` | low / normal / high, compared with what the client stated |
  | `key_entities` | Jurisdictions, regulators, deadlines, amounts (highlighted in the UI) |
  | `missing_information` | Shown in the UI as "Questions to ask the client" |
  | `flags` | `multi_service`, `vague`, `out_of_scope`, `spam`, `urgency_mismatch`, `possible_injection` |
  | `summary` | One sentence a team lead can read in three seconds |

- **Failure handling:** each run retries once on an API error or invalid output, with backoff on 429/502/503. If the app's primary model is down, OpenRouter falls through to the fallback model, and the model that actually answered is recorded. If the primary run still fails, the result is `classification_failed` and the enquiry goes to review. **Enquiries are never dropped.**

### 4. Route ([`route.py`](app/pipeline/route.py))
A pure function: the same inputs always give the same decision. Rules run in the order listed in `routing.yaml`, and **the first one that applies decides**:

| # | Rule | Destination |
|---|---|---|
| 1 | **Hard stop:** possible injection (from *any* of the 3 runs), CRM conflict, or classification failure | Review queue |
| 2 | **Not an enquiry:** spam, job applications, vendor pitches | Archive, no notification |
| 3 | **Existing client** in the CRM | Relationship partner, with the service-line lead copied |
| 4 | **Review triggers:** fewer than 3/3 runs agree on the service line, any non-exempt flag, out of scope | Review queue |
| 5 | **Complex and high urgency** (stated *or* inferred) | Service-line lead, with the practice head copied |
| 6 | **Otherwise** | Service-line lead |

Every rule that was checked is written to a **routing trace** with its result. The model can only output taxonomy ids, so it can't name a person or a destination: "route this to the CEO" has no path.

### 5. Notify and audit ([`notify.py`](app/pipeline/notify.py), [`run.py`](app/pipeline/run.py))
- **Notify:** a rendered email or Slack message, with the summary, service line, complexity and **Accept / Reassign** links, is written to the notifications log. Nothing real is sent.
- **Audit:** one row per pipeline run, holding:
  - the enrichment output;
  - the prompt version, **prompt hash** and **config hash**;
  - the model requested and the model that answered, plus the upstream provider;
  - **all raw LLM outputs**, including failed attempts;
  - the parsed result, the routing trace and the final destination;
  - latency per step, tokens, and **cost as reported by OpenRouter**.
- **Logs** go to stdout as one JSON line per pipeline step.

`run_pipeline()` is a plain function. The web app runs it in a background thread and streams progress over HTMX; a job queue could call it unchanged.

---

## A tour of the app

Server-rendered Jinja2 templates with HTMX and one hand-written CSS file. There is no JavaScript build step.

| Page | What it's for |
|---|---|
| **Inbox** (`/`) | Every enquiry with an outcome summary: routed automatically / sent to review / archived / duplicates. Columns: received, company (with size), industry, summary, service line, complexity, urgency (stated → inferred, with a warning when they differ), destination and status. **Every column heading except Summary sorts**, by meaning rather than alphabet: status puts "in review" first, complexity puts complex first, urgency puts high first. There are filters for service line, industry, status and destination, plus **search** over company, contact, summary and the enquiry text. Sort and filters are kept in the URL, so a view can be bookmarked. |
| **Enquiry detail** (`/enquiries/{id}`) | The centre of the demo. Shows: the original text with key entities highlighted; the triage card (summary, service lines, complexity and rationale, stated vs inferred urgency, flags, agreement such as "3/3 runs agreed" or "2/3 agreed: tax vs deals", questions for the client); **Why it went here** (every rule with its result, the deciding one highlighted); enrichment (existing client, partner, conflict, stated vs recorded size); actions (**Accept**, **Reassign** with a required reason, **Mark misclassified**); history; and a **Technical details** panel with the prompt and config hash, model, provider, latency per step, tokens, cost and the raw JSON from all 3 runs. |
| **Review queue** (`/review`) | Only enquiries that need a human, as cards headed by *why* ("Possible prompt injection", "Conflict flag: …", "Runs disagreed on service line"). Accept the suggestion, route to any lead, or close out-of-scope ones, without leaving the page. |
| **Submit enquiry** (`/submit`) | The real form fields, a **Load sample** menu grouped into clear and hard cases, and live step-by-step progress (Intake → Enrich → Classify → Route → Notify) before landing on the detail page. |
| **Evaluation** (`/eval`) | Headline tiles, a model comparison table, confusion matrices for service line and complexity (shaded HTML tables), the failure table with a link to each enquiry, routing accuracy by case type, and a "What this tells us" write-up. Warns when results were produced with a different prompt from the live one. |
| **Notifications log** (`/notifications`) | What would have been sent to team leads and the review channel, rendered as it would arrive. |
| **Health** (`/metrics`, `/metrics.json`) | Day-one production metrics computed from the database: **model-error rate** (overall and per predicted service line; see below), any-reassignment rate, review-queue rate and reasons, service-line mix over 7 and 30 days, classification failure rate, p50/p95 latency, mean cost, run agreement, and which providers and models answered. |
| **Configuration** (`/config`) | See [the next section](#configuration-the-business-logic-visible-and-editable). |

**Feedback loop:** Accept, Reassign and Mark misclassified each write a feedback row (old value, new value, reason).
- **Every reassignment needs a reason:** *wrong service line*, *lead capacity or leave*, *client relationship*, *conflict or independence*, or *other*.
- **Only a wrong service line (or Mark misclassified) counts as a model error,** both on the Health page and in the exported eval data. Without this, a lead on holiday would look like classifier drift. The Health page reads these rows, and [`scripts/export_feedback.py`](scripts/export_feedback.py) turns them into new labelled records in the dataset's format. That is how production corrections become evaluation data.

---

## Configuration: the business logic, visible and editable

The rules and definitions are the product, so the people who own them, such as practice leads and operations, should be able to see and change them without a developer. The **Configuration** page has seven tabs:

| Tab | Shows | Can change |
|---|---|---|
| **How it decides** | What the model decides vs what code decides; the 6 routing rules in plain English, using the live settings; service line → lead, with enquiry counts | (read-only overview) |
| **Routing & team leads** | Every lead, the line → lead mapping, the practice head, hard stops and review triggers | Lead names and emails; which lead receives each line; the practice head; each hard stop on or off; how many runs must agree; which flags are exempt from review; out-of-scope handling; the duplicate window |
| **Service lines** | Each line's name, description, typical requests and "not this" notes. These are written word for word into the prompt | All of those fields |
| **Complexity & urgency** | The rubric criteria, guidance notes, urgency definitions and mismatch rule | All of those |
| **System prompt** | The **exact system prompt**, the user-message template, the output schema and the request settings, with the prompt version and hash | (generated from the two tabs above) |
| **CRM data** | Mock clients, partners, conflicts and headcounts | The raw JSON |
| **Change history** | Every saved edit, with its note, diff, prompt hash before → after, and routing impact | **Revert** any change |

Every tab also has a **raw YAML/JSON editor** for structural changes, such as adding a service line or reordering rules.

**Every edit goes Draft → Preview → Save:**
1. **Validation** checks everything the code relies on: every service line has a lead, the last rule is `default`, the complexity levels match the output schema, existing clients have a known partner, and so on. An invalid edit can't be saved.
2. **The preview** shows a diff of the file and, if the prompt changes, a **diff of the system prompt**.
3. **A routing impact replay:** every stored enquiry is re-routed under both the live and the draft config, using its stored classification, so no model calls are made. The preview lists any that would land somewhere different, for example "2 of 48 existing enquiries would route differently: Brightwater Capital, Review queue → Sofia Marchetti".
4. **On save:** the file is written atomically and **the running app reloads it immediately**, with no restart. The change is recorded with a note, and form edits **preserve the YAML comments**.
5. **Prompt changes mark the evaluation as stale.** The Evaluation and System prompt tabs warn until `make eval` is re-run.

There is no authentication: anyone with the URL can change routing. In production this page would sit behind SSO with an admin role (DECISIONS D22). To lock it in a deployment, mount `config/` read-only (`./config:/app/config:ro`); the page then says it's read-only.

---

## Evaluation

[`scripts/run_eval.py`](scripts/run_eval.py) runs the **real pipeline** over the labelled dataset for every configured model, each with a fresh database so dedupe behaves as in production. It writes `eval_results/{model}_{prompt_version}.json`. The fallback model is disabled during evaluation, so each run measures exactly one model.

### Results (prompt v1, 50 enquiries, 3 runs each)

| Model | Review recall | Auto-route precision | Service line | Complexity | Urgency | Review rate | Injection | Cost / 1,000 | p50 / p95 latency |
|---|---|---|---|---|---|---|---|---|---|
| **anthropic/claude-sonnet-5.5** (default) | 100% (8/8) | 100% (37) | 100% | **90%** (4 off by one) | 98% | 17% | 2/2 flagged, not followed | $40.17 | 4.5s / 8.7s |
| anthropic/claude-haiku-5.5 | 100% | 100% | 100% | 66% (14 off by one) | 69% | 17% | 2/2 | **$2.50** | 5.5s / 6.9s |
| google/gemini-3.8-flash | 100% | 100% | 100% | 73% (10 off by one, 1 off by two) | 89% | 17% | 2/2 | $19.21 | 9.9s / 35.9s |

How to read the metrics:
- **Review recall** is the metric that matters most: of the enquiries that should have gone to a human, the share that did. A miss is a silent misroute.
- **Auto-route precision:** of the enquiries routed without review, the share routed correctly.
- **Routing accuracy** compares the final destination with the destination implied by the gold labels.
- **Complexity errors** are counted separately as one level off vs two levels off.

### What it shows, honestly
- **Treat the 100% routing scores as an upper bound.** I wrote both the taxonomy's "not this" notes and the confusable test cases. The eval shows that models follow written rules, not that they generalise to messier real enquiries.
- **The 3-run agreement check never fired.** All runs agreed on all 48 triaged enquiries, for every model. Every review decision came from model flags or the CRM. Whether disagreement catches anything is **untested**; it is the first thing to measure in shadow mode.
- **Complexity is over-called, never under-called.** 28 of the 29 complexity errors across all models are one level too high (the other is two levels too high). The main cause is the "board-level stakeholders" criterion, which models apply whenever a board or committee is mentioned. I deliberately **did not tune the prompt against these same 50 records** (DECISIONS D17). The fix belongs with a held-out set.
- **The cheaper model is good enough for routing, not for complexity.** Haiku matches Sonnet on every routing and safety metric at about 1/16th of the cost, but its complexity and urgency calls, which are what the team lead reads, are much weaker.
- **The complexity gold labels come from a single labeller.** A few points of that metric are labeller noise.

Full analysis: [`eval_results/failure_notes.md`](eval_results/failure_notes.md), also shown on the `/eval` page.

---

## Models and the OpenRouter integration

All LLM calls go through **OpenRouter** with the official `openai` Python SDK. Everything OpenRouter-specific lives in [`app/llm/client.py`](app/llm/client.py), behind an `LLMClient` interface with two methods. Calling a provider directly would mean writing one new class.

Every request sends:
- `response_format: {type: json_schema, strict: true}`, with the schema generated from the taxonomy;
- `provider: {require_parameters: true}`, so the request only reaches providers that honour every parameter sent and can't silently ignore the schema;
- `usage: {include: true}`, so each response reports tokens and **actual cost**;
- `HTTP-Referer` and `X-Title` headers, so usage is attributed in the OpenRouter dashboard.

Two findings, both verified against OpenRouter on 2026-10-13:
- **Strict `json_schema` instead of a forced tool call** (D6). No provider for the current Claude models accepts a forced `tool_choice` under `require_parameters`. `json_schema` works for all three models.
- **Temperature is sent only to models that accept it** (D7), read from OpenRouter's `/models`. Claude and GPT-5.6 reject it, so their three runs are samples at the provider default. Gemini runs the intended 0 / 0.7 / 0.7.

Why OpenRouter, and the trade-offs (data retention, the extra hop, calling a provider directly in production) are covered in [ARCHITECTURE.md](ARCHITECTURE.md#model-and-api-choices).

---

## Commands and scripts

| Command | What it does |
|---|---|
| `make up` | Build and start on http://localhost:8000; seeds on first start |
| `make down` | Stop, keeping the database |
| `make test` | Run the 81 tests inside the image. One makes a live call (about $0.05) and is skipped without a key |
| `make eval` | Evaluate `TRIAGE_MODEL` + `EVAL_MODELS` and write `eval_results/` (about $3) |
| `make reset-db` | Delete the database volume; the next `make up` re-seeds (about $2) |
| `make logs` | Follow the structured JSON logs |
| `python -m scripts.triage E030` | **Core logic only, no UI or database:** classification, agreement, enrichment and routing trace for one enquiry. Takes a dataset id or free text (`--size`, `--urgency`, `--industry`, `--company`, `--model`, `--json`) |
| `python -m scripts.run_eval --models a/b,c/d --limit 5` | Evaluate specific models; `--limit` is a cheap smoke test that writes no results file |
| `python -m scripts.export_feedback [--include-seed]` | Export human feedback as labelled JSONL in the dataset's format |

Example output of `python -m scripts.triage E030`:

```
Kestrel Wealth Management · Financial services · 51-200 · stated urgency low
model anthropic/claude-sonnet-5.5 via Azure · 5.2s · $0.0401

  Service line risk_reg   [3/3 runs agreed]
  Complexity   complex — significant regulatory exposure (FCA s166 skilled person review); ...
  Urgency      stated low, inferred high
  Flags        urgency_mismatch

  Routing trace
      hard_stop_review         No injection flag, no conflict, classification succeeded
      archive_not_an_enquiry   Classified as Risk & Regulatory Compliance
      existing_client          Not an existing client in CRM
      review_flags             3/3 runs agreed; no review flags
   >> complex_high_urgency     Complex with high urgency (inferred); practice head copied

  => Aaron Lindqvist (cc Catherine Doyle-Hart)
```

---

## Environment variables

See [`.env.example`](.env.example).

| Variable | Default | Notes |
|---|---|---|
| `OPENROUTER_API_KEY` | (none) | **Required.** From https://openrouter.ai/keys |
| `LLM_MODE` | `live` | Only `live` is supported in this build (D1) |
| `TRIAGE_MODEL` | `anthropic/claude-sonnet-5.5` | The model the app uses |
| `EVAL_MODELS` | `anthropic/claude-haiku-5.5,google/gemini-3.8-flash` | Comparison models for `make eval` |
| `TRIAGE_FALLBACK_MODELS` | `google/gemini-3.8-flash` | OpenRouter `models` fallback for the app (never used in eval). Leave empty to disable |
| `OPENROUTER_APP_URL` | `http://localhost:8000` | Attribution header, and the base URL for Accept/Reassign links |
| `OPENROUTER_APP_NAME` | `Intake Triage Demo` | Attribution header |
| `DATABASE_URL` | `sqlite:///data/app.db` locally; `sqlite:////app/var/app.db` in Docker | SQLModel / SQLAlchemy URL |

---

## Project structure

```
app/
  main.py              FastAPI app: pages, HTMX endpoints, configuration routes, JSON logging
  config.py            Settings (pydantic-settings) and loading config/*.yaml into a Domain
  configstore.py       Validate / preview / apply config edits, routing-impact replay, hashes, history
  schemas.py           Form schema, LLM output schema, strict JSON schema builder
  models.py            SQLModel tables: Enquiry, PipelineRun (audit), Notification, Feedback, ConfigChange
  feedback.py          Accept / reassign / mark misclassified
  metrics.py           Health metrics computed from the database
  evaluation.py        Run the pipeline over the dataset and score it
  seed.py              First-start seeding (live), with clearly labelled demo feedback
  pipeline/            intake.py · enrich.py · classify.py · route.py · notify.py · run.py
  llm/                 client.py (LLMClient + OpenRouter) · prompts.py (prompt built from YAML)
  templates/           Jinja2 templates
  static/              style.css, htmx.min.js (vendored, 1.9.12)
config/                taxonomy.yaml · rubric.yaml · routing.yaml · crm_clients.json
data/                  synthetic_enquiries.jsonl (labelled dataset)
eval_results/          Per-model results JSON + failure_notes.md
scripts/               run_eval.py · triage.py · export_feedback.py
tests/                 81 tests
docs/screenshots/      README screenshots
```

---

## Synthetic dataset

[`data/synthetic_enquiries.jsonl`](data/synthetic_enquiries.jsonl) holds 50 records, each with the form fields plus a gold label. The gold label carries the primary and secondary service lines, complexity, true urgency, whether it should be reviewed, a duplicate link, the case type and reasoning notes.

- **25 clear cases** cover all 7 service lines and all 3 complexity levels.
- **25 hard cases:** 2 each of multi-service, vague, urgency mismatch, small company with a complex problem, existing client, conflict flag, out of scope, prompt injection, risk vs audit, tech vs strategy and duplicate submission, plus 1 each of spam, job application and vendor pitch.
- **Written to read like real people:** varied length, typos, rambling, British and American spelling.
- **Every gold label was checked by hand.** Labels are left null where no correct answer exists (for example, "advice re our accounts" has no gold service line), and those records are excluded from that metric (D4).
- [`config/crm_clients.json`](config/crm_clients.json) contains matching existing clients, conflict-flagged companies, and two companies whose stated size disagrees with the record.

---

## Tests

81 tests, all passing on Python 3.12 in the image and 3.13 locally. Unit tests never hit the network; LLM calls go through a scripted fake or a mocked HTTP layer.

| Area | What's covered |
|---|---|
| Routing | One test per rule, rule precedence, each hard stop, each review trigger, exempt flags, routing read from YAML |
| Classification | Three runs and the temperature schedule, agreement counting, validation retry then `classification_failed`, API retry with backoff, an injection flag from any run |
| OpenRouter client (HTTP mocked) | Request shape: `require_parameters`, strict `json_schema`, usage accounting, attribution headers, fallback `models`. Status codes classified as retryable or not; a 429 is retried |
| Pipeline | Audit row contents, original text kept exactly, dedupe skips triage, failure goes to review, archive sends no notification, feedback rows |
| Prompt | Every taxonomy id and rubric criterion appears in the prompt; the delimiter can't be closed early |
| Web app | Submit → progress → detail flow, validation errors, review-queue actions, **reassign changes the Health metrics**, a reassignment for capacity is not counted as a model error, empty states, inbox sorting, filtering and search |
| Configuration | Validation blocks bad edits; the preview shows the routing impact; save applies **live without a restart** and preserves comments; rename a lead then revert; a taxonomy edit changes the prompt sent and the stored prompt hash; stale-eval warning |
| Live end-to-end | The real model on the injection case: flagged, sent to review, never routed to a "CEO" |

---

## Costs

Costs below are as reported by OpenRouter for the default model, `claude-sonnet-5.5`.

| Action | Approximate cost |
|---|---|
| One enquiry (3 runs) | $0.04 |
| First-start seeding (50 enquiries) | $2 |
| `make eval`, all three models | $3 |
| `make test` (one live call) | $0.05 |
| Real volume, about 10 enquiries a day | $0.40 a day |

Most of Sonnet's cost is the roughly 4k-token system prompt, sent three times per enquiry. Prompt caching would remove most of it.

---

## Limitations and security notes

- **No authentication.** Every page, including Configuration, is open to anyone who can reach it. In production all of it sits behind SSO, with role-based access to Configuration.
- **Integrations are mocked:** CRM, company data and the conflict check read a JSON file, and notifications are written to a log instead of being sent. ARCHITECTURE.md has a table of real, mocked and not-built components.
- **Live-only.** There is no offline or mock mode, so reviewers need an OpenRouter key to run it.
- **Client data goes to a third party.** In production, restrict OpenRouter to zero-data-retention providers, keep prompt logging off, redact personal data before the call, or call the chosen provider directly (ARCHITECTURE.md).
- **Secrets:** `.env` is gitignored and no keys are in the git history.
- **The evaluation is synthetic and single-labeller.** See [Evaluation](#evaluation) for what it can and can't show.

---

## What would break first in production

See **[WRITEUP.md](WRITEUP.md)** (half a page).
- **Breaks first:** confident misroutes from taxonomy drift, where all three runs agree on the wrong line and nothing triggers review.
- **The signal:** reassignment rate per service line.
- **The fallback:** hard stops, the review queue, one-click Reassign, and a shadow → assisted → automatic rollout.

## What I'd do with more time

- **A held-out test set**, written and labelled by someone other than the taxonomy author, plus a second labeller on complexity. Then fix the "board-level" rubric wording (now a form field on the Configuration page) and measure the change on that set.
- **Evidence-based complexity:** the model reports each rubric criterion with a quote from the enquiry, and code works out the level. That targets the measured over-calling directly and makes it tunable without prompt changes.
- **An audit-independence rule:** an existing audit client asking for a prohibited non-audit service (for example valuations or system implementation) becomes a hard stop. This is the most important domain rule not yet modelled.
- **Test whether the agreement check earns its cost:** measure it in shadow mode. If it never fires, drop it and save two-thirds of the spend.
- **Prompt caching**, and an **embedding or fine-tuned first-pass classifier** once a few hundred feedback-labelled enquiries exist.
- **Real integrations:** CRM, conflicts system, email and Slack with signed action links, and SSO.
