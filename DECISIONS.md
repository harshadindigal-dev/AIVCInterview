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
