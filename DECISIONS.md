# Decisions

Short entries: decision, alternatives considered, why.

## D1. Live-only LLM mode (no mock client, no recorded cache)
- **Decision:** The app always calls OpenRouter and assumes a valid `OPENROUTER_API_KEY`. It fails at startup with a clear message if the key is missing.
- **Alternatives:** The original brief's default `LLM_MODE=mock`, with recorded responses and a heuristic fallback.
- **Why:** Project owner's instruction. Consequences: `docker compose up` seeds the database with real LLM calls (about 50 enquiries × 3 runs), so first start takes minutes and costs a small amount. There is no `app/llm/cache/` and no `record_cache.py`. The `LLMClient` interface remains, so a mock or direct-provider client is one new class.

## D2. Dataset is 50/50 clear vs hard, not 60/40
- **Decision:** 25 clear cases and 25 hard cases.
- **Alternatives:** 30/20, which would need categories to share records.
- **Why:** The brief asks for at least 2 of each of 11 hard categories (22+) within 40–50 records. Keeping each hard case to one category makes per-category failure analysis unambiguous.

## D3. Extra gold fields
- **Decision:** `gold` also carries `case_type`, `urgency` (true urgency) and `duplicate_of`.
- **Why:** `case_type` drives the grouped "Load sample" menu and per-category eval breakdowns. `urgency` lets us score inferred urgency and mismatch detection. `duplicate_of` defines the expected destination for duplicates.

## D4. Null gold labels where no correct answer exists
- **Decision:** Gold `complexity` is null for vague, out-of-scope and not-an-enquiry cases. Gold `primary_service_line` is null for E029 ("advice re our accounts").
- **Alternatives:** Force a best guess.
- **Why:** Forcing a label would penalise or reward the model on noise. Null-gold cases are excluded from the matching accuracy metric but still count for routing and review recall, where the correct action (review) is well defined.

## D5. Hard-stop rule ahead of the brief's override order
- **Decision:** A new first rule, `hard_stop_review`, sends an enquiry to review when any of these is true: the `possible_injection` flag is set, the CRM `conflict_flag` is true, or classification failed. It runs before archive and before existing-client routing. The order is: hard stop → archive → existing client → other review triggers → complex+high → default.
- **Alternatives:** The brief's order, where existing client comes before all review flags.
- **Why:** With the brief's order, an injection or a conflict from an existing client goes straight to a relationship partner without review. That is the most damaging silent misroute this system could make.
  - **Classification failure is a hard stop:** when the call fails, the injection check never ran, and there is no service-line lead to copy.
  - **Injection outranks archive:** an attacker could otherwise make a real enquiry disappear by getting it labelled spam.
  - **Not hard stops:** vague, out-of-scope and other review triggers from existing clients still go to the relationship partner, who is best placed to call the client back.
- **Dataset note:** both conflict cases are prospects. The rule is covered by unit tests rather than dataset records.

## D6. Structured output via `response_format: json_schema` (strict), not a forced tool call
- **Decision:** Every call sends a strict JSON schema named `record_triage` as `response_format`. No tools are sent.
- **Alternatives:** A single function tool with `tool_choice` forced to it, as the brief prefers.
- **Why:** I probed OpenRouter with `provider.require_parameters: true` on 2026-10-13. The current Anthropic models (`claude-sonnet-5.5`, `claude-haiku-5.5`) have no provider that accepts a forced `tool_choice`, or even `"required"`, so the request fails with 404 "No endpoints found". `json_schema` was accepted by every candidate model. The brief allows this as long as one method is used consistently. The schema's enums are generated from `taxonomy.yaml`. Output is validated again with Pydantic, which also checks the ids against the taxonomy.

## D7. Temperature is sent only where the model supports it
- **Decision:** At startup, the client reads `supported_parameters` from OpenRouter's `/models` endpoint. Models that support temperature get 0 / 0.7 / 0.7. Models that don't get three calls at the provider's default sampling. The temperature actually sent (or `null`) is stored for every run.
- **Alternatives:** Always send temperature, which makes Claude and GPT-5.6 requests fail under `require_parameters`. Or drop `require_parameters`, which risks a provider silently ignoring the schema.
- **Why:** The agreement check still measures sampling variance. Run 0 stays the primary result and the other two only vote. The trade-off: for no-temperature models, run 0 is not deterministic. This is documented as a known limitation.

## D8. Models
- **Decision:** `TRIAGE_MODEL=anthropic/claude-sonnet-5.5` ($2 / $10 per M tokens). `EVAL_MODELS=anthropic/claude-haiku-5.5` (cheap tier, $0.10 / $0.50) and `google/gemini-3.8-flash` (different vendor, $0.75 / $3.75).
- **Why:** Sonnet is a strong mid-tier model. Haiku isolates the effect of price within one vendor. Gemini tests a second vendor and is also the only one of the three that accepts temperature, so it runs the brief's 0 / 0.7 design exactly. All three accept strict `json_schema` under `require_parameters` (verified 2026-10-13).
- **Fallback:** The app pipeline passes `models: [primary, google/gemini-3.8-flash]` so a provider outage falls through. The model that actually answered is stored and shown. Evaluation runs never use the fallback, so each eval measures exactly one model.

## D9. All three runs execute concurrently
- **Why:** Running them in parallel cuts per-enquiry latency from about 15s to about 5–9s. The calls are independent, so the agreement check is unaffected.

## D10. A `possible_injection` flag from any of the three runs triggers the hard stop
- **Why:** Other flags come from the primary run only. Injection is the one where a single detection is enough, because a false positive costs a human glance and a false negative can misroute.

## D11. "High urgency" for the complex+high rule means stated OR inferred
- **Why:** Stated urgency is often wrong in both directions (E030, E031). Copying the practice head on a false positive is cheap. Missing a regulator deadline is not. The trace says which urgency source fired the rule.

## D12. Routing trace records every rule evaluated, not only the one that fired
- **Why:** "Why not tax lead?" is as common a question as "why tax lead?". The detail page shows each check in order with its result, and highlights the deciding rule. Exempt flags (urgency_mismatch, multi_service) appear as notes. When an existing-client enquiry would otherwise have gone to review, that is noted too.

## D13. "Mark misclassified" also reroutes when the service line changes
- **Alternatives:** Record the corrected label only.
- **Why:** A reviewer who corrects the service line almost always wants the enquiry to go to that line's lead. Doing both in one action avoids a second click. Feedback is recorded with action `misclassified`, so it is distinguishable from a plain reassignment.

## D14. Submit runs the pipeline in a background thread and the page polls for progress
- **Alternatives:** Run it inside the POST request, which shows a spinner for about 10s with no step-by-step progress. Or a job queue, which the brief rules out.
- **Why:** The brief asks for steps shown as they complete. The pipeline function takes an `on_step` callback, and HTMX polls `/enquiries/{id}/progress`. If the thread crashes, the enquiry is parked in review, so it is never dropped. Progress is held in memory; that is fine for one process at about 10 enquiries a day.

## D15. Seeding shifts dataset dates to end yesterday, and seeds some feedback marked `source=seed`
- **Why:** The 7- and 30-day metrics need recent data whenever the app is started, and the relative gaps between records (which dedupe relies on) are preserved. The reassignment metrics need some feedback rows. Seeded feedback is limited to:
  - corrections where the model disagreed with gold (none for the default model on this set);
  - twelve accepts.

  An earlier version also seeded two "plausible" reassignments. I removed them: the model was right on both by gold, so they invented disagreement and inflated the reassignment rate.

  Seeded rows are labelled "seeded demo" in the UI and excluded from `export_feedback.py` by default.

## D16. Evaluation runs the real pipeline, with a fresh database per model
- **Alternatives:** Call `classify()` directly.
- **Why:** Routing accuracy, review recall and duplicate handling can only be measured end to end. A fresh SQLite file per model keeps dedupe faithful. Records are processed in parallel, except that repeats of the same email and text run in submission order, after their originals.

## D17. No prompt tuning against the eval set
- **Why:** The complexity errors suggest a rubric fix ("board-level stakeholders"). Tuning against the same 50 records would make the numbers look better and mean less. The fix and its measurement belong with a held-out, independently labelled set. This is recorded in `failure_notes.md` and the README.

## D18. `config/` is mounted read-only into the container
- **Why:** This is an acceptance criterion: changing a team lead in `routing.yaml` and restarting must change routing with no rebuild. Verified on a fresh clone.

## D19. htmx is vendored (`app/static/htmx.min.js`, 1.9.12)
- **Why:** There is no build step either way. Vendoring removes a CDN dependency from "runs first time".

## D20. "Accept" in the review queue means "route to the suggested lead"
- **Why:** A reviewer accepting a flagged enquiry is accepting the model's service line. Accept sends the enquiry to that lead with a notification. For out-of-scope enquiries, with no lead, the button reads "Close: no team lead needed".

## D21. The routing destination is the primary recipient only; copies are separate
- **Why:** Routing accuracy compares who owns the enquiry. Copies (practice head, service-line lead on an existing-client enquiry) are shown in the trace and in the notification, but don't count towards accuracy.
