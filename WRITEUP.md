# What breaks first, what to monitor, and the fallback

**What breaks first: confident misroutes.** All 50 synthetic enquiries route correctly, but I wrote both the rules and the tests, so that is an upper bound. When new work appears (ESG, "AI governance"), the model files it under the nearest line, all three runs agree, and nothing triggers review. The agreement check never fired on the test set. Taxonomy drift is the likeliest failure and the least visible one. Second, complexity is over-called (28 of 29 errors one level too high, mostly from treating any mention of a board as board-level). That misleads team leads about effort, though it rarely changes routing.

**What to monitor** (on `/metrics` from day one):
- **Reassignment rate per predicted service line,** the main drift signal. Capture a reason on each reassignment, so workload moves aren't counted as model errors.
- **A weekly spot-check of about 10% of auto-routed enquiries,** because a confident error stays invisible until a lead objects.
- **Review rate and its reasons; the service-line mix against the baseline; the `out_of_scope` and `vague` rates.**
- **Failure rate, p95 latency,** and the model and provider behind each call.

**Fallback when the model is wrong:**
- **Before routing:** injection, CRM conflicts and failed classifications always go to a person. The model can only output taxonomy ids, so it cannot name a destination. If the API fails, the system retries, then tries a fallback model, then sends the enquiry to the review queue. Nothing is dropped.
- **One enquiry:** one-click Accept and Reassign. Corrections are stored and exported as new evaluation data.
- **A pattern:** fix the rule or definition on the Configuration page. The preview shows which existing enquiries would route differently, every change is revertible, and prompt changes are re-measured with `make eval` before they are trusted.
- **Rollout:** shadow mode, then assisted, before anything routes on its own. A line whose reassignment rate rises goes back to assisted.
