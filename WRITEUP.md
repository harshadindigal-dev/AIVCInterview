# What breaks first, what to monitor, and the fallback

**What breaks first: confident misroutes of enquiries that fall between the rules.** The prototype routes all 50 synthetic enquiries correctly, but I wrote both the taxonomy rules and the test cases, so treat that as an upper bound. When new kinds of work arrive (ESG, "AI governance"), the model will put them in the nearest existing service line *confidently*. The three runs will agree and no review triggers. On the test set the agreement check never fired once. Taxonomy drift is the most likely failure and the least visible one. Second: complexity is over-called (28 of 29 errors are one level too high, mainly from the "board-level stakeholders" criterion). It rarely changes routing, but it misleads the team lead about effort.

**What to monitor** (on `/metrics` from day one):
- reassignment rate per predicted service line, the clearest drift signal;
- review rate and its reasons;
- 7- and 30-day service-line mix against the baseline;
- `out_of_scope` and `vague` rates;
- failure rate, p95 latency, and which model and provider answered.

Weekly, re-run `make eval` on synthetic data plus exported feedback.

**Fallback when the model is wrong:**
- **Before routing.** Prompt injection, CRM conflicts and classification failures always go to a human. The model can only output taxonomy ids, so it cannot invent a destination. If the API fails: retry, then a fallback model, then the review queue. Nothing is dropped.
- **After routing.** Every notification has one-click Accept and Reassign. Corrections are stored, and `export_feedback.py` turns them into evaluation data.
- **Rollout.** Shadow mode, then assisted, before anything routes on its own. A line whose reassignment rate rises goes back to assisted.
