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

## D5. Conflict-flagged companies are non-clients in the CRM
- **Decision:** Both conflict cases are prospects, not existing clients.
- **Why:** The rule order in the brief puts "existing client → relationship partner" before "review flags". So a conflicted existing client would bypass review. The dataset avoids that combination. The open question is recorded in the README as a production risk.
