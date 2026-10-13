# What breaks first, what to monitor, and the fallback

> **Half-page answer.** The section [Detail and plan](#detail-and-plan) below has the supporting context: a failure-mode table, a monitoring plan with thresholds and owners, the fallback ladder, and the rollout.

**What breaks first: confident misroutes.** All 50 synthetic enquiries route correctly, but I wrote both the rules and the tests, so that is an upper bound. When new work appears (ESG, "AI governance"), the model files it under the nearest line, all three runs agree, and nothing triggers review. The agreement check never fired on the test set. Taxonomy drift is the likeliest failure and the least visible one. Second, complexity is over-called (28 of 29 errors one level too high, mostly from treating any mention of a board as board-level). That misleads team leads about effort, though it rarely changes routing.

**What to monitor** (on `/metrics` from day one):
- **Model-error reassignment rate per predicted service line,** the main drift signal. Each reassignment records a reason, so workload moves aren't counted as model errors.
- **A weekly spot-check of about 10% of auto-routed enquiries,** because a confident error stays invisible until a lead objects.
- **Review rate and its reasons; the service-line mix against the baseline; the `out_of_scope` and `vague` rates.**
- **Failure rate, p95 latency,** and the model and provider behind each call.

**Fallback when the model is wrong:**
- **Before routing:** injection, CRM conflicts and failed classifications always go to a person. The model can only output taxonomy ids, so it cannot name a destination. If the API fails, the system retries, then tries a fallback model, then sends the enquiry to the review queue. Nothing is dropped.
- **One enquiry:** one-click Accept and Reassign. Corrections are stored and exported as new evaluation data.
- **A pattern:** fix the rule or definition on the Configuration page. The preview shows which existing enquiries would route differently, every change is revertible, and prompt changes are re-measured with `make eval` before they are trusted.
- **Rollout:** shadow mode, then assisted, before anything routes on its own. A line whose reassignment rate rises goes back to assisted.

---

## Detail and plan

### Context: what "wrong" costs here

At 40–60 enquiries a week, a misroute delays the client's first response by days (an enquiry sitting with the wrong lead) and quietly loses work. That matters more than any model metric. Three kinds of error are not symmetric, and the system is built around that:

- **Must never happen silently:** a missed **conflict of interest**, which is a regulatory and reputational breach, and **following injected instructions**. These are hard stops in code, independent of how confident the model is.
- **Expensive but recoverable:** the **wrong service line**. The client waits, and the lead reassigns. This is what monitoring has to catch quickly.
- **Cheap:** **complexity one level off**, or an **unnecessary review**. A human spends a minute. I deliberately tuned towards over-reviewing: review recall matters more than review rate.

### Failure modes, ranked

| # | Failure | Likelihood | Impact | How we'd see it | Mitigation now | Next step |
|---|---|---|---|---|---|---|
| 1 | **Taxonomy drift:** new kinds of work forced into an existing line | High | Medium | Model-error reassignments into one line from many; `out_of_scope` share rising; weekly spot-check | Leads reassign; definitions editable on the Config page with an impact preview | Add a "doesn't fit the taxonomy" output and count it on the Health page |
| 2 | **Neighbouring-line confusion** (internal vs external audit, tech vs strategy) | Medium | Medium | Confusion matrix on exported feedback; pairs of lines reassigned back and forth | "Not this" notes in the taxonomy | A held-out set of confusable cases, measured on every prompt change |
| 3 | **Multi-service enquiries** owned by one lead, the other line finds out late | Medium | Medium | `multi_service` flag rate; reassignments between primary and secondary lines | Secondary lines shown on every enquiry | Copy the secondary line's lead (a one-rule change) |
| 4 | **Conflict or existing client missed** (name variants, subsidiaries, personal email) | Low–Medium | **High** | Partners reporting "this was our client"; CRM match rate | Domain + normalised-name match; conflicts are a hard stop | Fuzzy matching with match strength; group/subsidiary lookup; the real conflicts system as a gate |
| 5 | **Urgency missed** (a regulator deadline stated as "low") | Medium | High for that client | Urgency-mismatch rate; review-queue age | Model infers urgency and flags mismatches; complex + high copies the practice head | Extract deadlines as dates; alert on a deadline within 5 working days |
| 6 | **Complexity over-called** | High (measured) | Low | Complexity corrections in feedback | Shown with its rationale, editable | Model reports each rubric criterion with a quote; code decides the level |
| 7 | **Provider outage or silent model change** | Medium | Medium | Failure rate; p95 latency; prompt/config/model hash on every run | Retry → fallback model → review queue | Pin model versions; weekly `make eval` canary; consider calling the provider directly |
| 8 | **Prompt injection, or a new attack style** | Low | High | Injection flag rate; spot-check | Untrusted-input delimiting; flag from any run is a hard stop; model can't name destinations | Add a keyword check before the model runs, as a second detector |
| 9 | **Poor form input** (one-liners, wrong size band) | High | Low | `vague` rate; size mismatches against CRM | Vague → review with questions to ask the client | Fix the form: required fields, examples, and a drafted clarifying email |
| 10 | **Client data exposure** via a third-party API | Low | High | Contract and provider audit, not metrics | Nothing beyond the API terms (demo) | Zero-data-retention providers only; redact personal data; or a provider in the firm's own cloud account |

### Monitoring plan

| Signal | Source | Alert threshold | Owner | Cadence |
|---|---|---|---|---|
| Model-error reassignment rate, per line | Feedback (reason = wrong service line, or misclassified) | > 10% for any line over 2 weeks | Practice ops | Weekly |
| Auto-route precision (spot-check) | Random 10% of auto-routed enquiries, reviewed by the analyst | < 95% | Analyst → practice ops | Weekly |
| Review-queue rate | Pipeline runs | Outside 10–30% | Practice ops | Weekly |
| Review-queue age | Enquiries in review | Any item > 1 working day | Analyst | Daily |
| `out_of_scope` + `vague` share | Classifications | Doubles against the 4-week baseline | Practice ops | Weekly |
| Service-line mix | Classifications | Any line ±50% against baseline | Practice ops | Monthly |
| Classification failure rate | Pipeline runs | > 2% in a day | Engineering | Daily alert |
| p95 latency | Pipeline runs | > 30s | Engineering | Daily alert |
| Eval regression | `make eval` on synthetic + feedback set | Review recall < 100% on hard stops; any metric down > 5 pts | Engineering | On every change, plus weekly |
| Cost per enquiry | Reported by OpenRouter | > 2× baseline | Engineering | Monthly |

### Fallback ladder (when the model is wrong)

| Level | Trigger | What happens | Who acts |
|---|---|---|---|
| 0. Prevent | Injection, conflict, classification failure | Hard stop to review, regardless of the model | Automatic |
| 1. Catch | Runs disagree, vague, out of scope | Review queue, with the reason shown on the card | Analyst |
| 2. Correct one | A lead or reviewer disagrees | One-click Reassign (with a reason) or Mark misclassified; logged as feedback | Team lead / analyst |
| 3. Correct the pattern | Repeated errors in a line, found by metrics or the spot-check | Edit the definition, rule or lead on the Config page; check the impact preview; save; `make eval` for prompt changes | Practice ops |
| 4. Degrade | A line's error rate stays over threshold, or the provider is down | That line (or everything) goes back to assisted mode; the analyst triages from the queue | Practice ops / engineering |
| 5. Learn | Weekly | Export feedback → add to the eval set → measure before the next prompt or model change | Engineering |

### Rollout

| Phase | Duration | What changes | Exit criteria |
|---|---|---|---|
| **Shadow** | 2–4 weeks | System runs on every enquiry; no notifications; the analyst keeps doing it by hand; decisions compared weekly | Review recall ≥ 95% against the analyst's judgement; auto-route precision ≥ 95%; zero missed conflicts or injections |
| **Assisted** | 4–8 weeks | Leads get the suggestion and confirm with one click; the analyst works only the review queue | Model-error reassignment rate < 10% per line; spot-check precision ≥ 95% |
| **Automatic for high-confidence enquiries** | Ongoing | 3/3 agreement, no flags, not an existing client, and the line is below its error threshold → routed with no confirmation | Stays only while the per-line thresholds hold; any breach sends that line back to assisted |

**Expected effect:** the analyst's 8 hours a week becomes roughly 1–2. That covers the review queue (about 17% of enquiries in the synthetic set) plus the weekly spot-check, and it would be measured in shadow mode rather than assumed.

### First 90 days of engineering

1. **Days 0–30:**
   - Put the real CRM and conflicts system behind the existing interfaces, and SSO in front of the app.
   - Email and Slack notifications with signed action links.
   - Start shadow mode.
   - Build a held-out test set labelled by two people.
2. **Days 30–60:**
   - Evidence-based complexity scoring.
   - The audit-independence rule (an audit client asking for prohibited non-audit work becomes a hard stop).
   - A deadline extractor.
   - Assisted mode.
3. **Days 60–90:**
   - Turn on automatic routing for qualifying lines.
   - Prompt caching.
   - Evaluate an embedding-based first-pass classifier on the feedback data, with the model kept for the hard cases.

### Assumptions behind this plan

- The analyst's 8 hours a week is mostly reading and routing, not client contact.
- Team leads will use one-click feedback if it's in the notification they already receive.
- A conflicts system exists and can be queried before routing.
- About 10 enquiries a day stays the order of magnitude, so a single service with an in-request pipeline is enough.
