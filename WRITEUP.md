# What breaks first, what to monitor, and the fallback

**What breaks first: confident misroutes.**
- **Most likely failure:** an enquiry that falls between the rules is routed wrongly with full confidence. All 50 synthetic enquiries route correctly, but I wrote both the taxonomy and the test cases, so treat that as an upper bound.
- **Hardest to see:** when new work appears (ESG, "AI governance"), the model files it under the nearest existing line, all three runs agree, and nothing triggers review. On the test set the agreement check never fired once. Taxonomy drift is the most likely failure and the least visible one.
- **Second:** complexity is over-called. 28 of 29 errors are one level too high, mostly because the model treats any mention of a board as board-level. It rarely changes who receives the enquiry, but it misleads the team lead about the effort involved.

**What to monitor** (on `/metrics` from day one):
- **Reassignment rate per predicted service line.** This is the main drift signal. It needs a reason captured on every reassignment, because a reassignment for workload is not a model error.
- **A weekly spot-check of about 10% of auto-routed enquiries.** A confident error is invisible until a lead objects, so this measures precision directly.
- **Review-queue rate and the reasons behind it,** and the 7- and 30-day service-line mix against the baseline.
- **Rates of `out_of_scope` and `vague`.**
- **Failure rate, p95 latency, and the model and provider behind every call.**
- **`make eval` after any change to the model, prompt or config.**

**Fallback when the model is wrong:**
- **Before routing.** Possible injection, CRM conflicts and classification failures always go to a person. The model can only output taxonomy ids, never a person or destination. If the API fails, the system retries, then tries a fallback model, then sends the enquiry to the review queue. Nothing is dropped.
- **For one enquiry.** Every notification carries one-click Accept and Reassign links. Corrections are stored, and `export_feedback.py` turns them into new evaluation data.
- **For a pattern.** Fix the logic on the Configuration page, for example tightening a "not this" note or pointing a line at another lead. Before saving, the preview shows which existing enquiries would route differently, and every change can be reverted. Any prompt change is re-measured with `make eval` before it is trusted.
- **During rollout.** Run in shadow mode, then assisted mode, before anything routes on its own. A line whose reassignment rate rises goes back to assisted.
