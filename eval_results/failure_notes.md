## Headline

All three models route every one of the 50 records correctly, catch every enquiry that needed a human (review recall 100%), and flag both injection attempts without following them. They differ only on **complexity**, where every model leans the same way: it calls work more complex than the gold label. Of 29 complexity errors across the three models, 28 are one level too high and 1 is two levels too high. None is too low.

## Why the routing numbers are too good to trust

- **The eval set and the taxonomy share an author.** Every "confusable" case (internal audit vs SOC 1, "digital strategy" that is really an MES selection) is resolved by a `not_this` note in `taxonomy.yaml` written alongside the dataset. The models are being tested on whether they follow written rules, which they do. Real enquiries will fall between the rules. Treat 100% service-line accuracy as an upper bound, not an estimate.
- **The agreement check never fired.** All three runs agreed on the service line for 48 of 48 triaged enquiries, for every model. On this dataset the "runs disagreed" review trigger has zero recall and zero precision. Every review decision came from model flags (`vague`, `out_of_scope`, `possible_injection`) or the CRM conflict flag. Whether sampling disagreement catches anything in production is unproven. That is the first thing shadow mode should measure.
- **Two of the three models do not accept temperature** (D7), so their three runs are samples at the provider default. They agreed anyway. Gemini, which did run 0 / 0.7 / 0.7, also agreed 48/48.

## What fails, and why

- **"Board-level stakeholders" is read too literally.** E045 (Audit & Risk Committee), E048 ("take to the board"), E035 (board business case) and E019 (remuneration committee) are pushed to complex because a committee is mentioned. Gemini sent E019, a pay benchmark for five roles, from simple to complex on this criterion alone. The rubric means the board is the client stakeholder for the engagement, not that a board will read a report. **Fix:** reword the criterion in `rubric.yaml` and add a counter-example. Re-evaluate on a fresh held-out set, not this one.
- **"Any single complex criterion is enough" (rubric note) amplifies the above.** It was written to stop the model under-calling small companies with hard problems (E032, E033). Every model got those right, but the note costs precision everywhere else.
- **Gold labels at the simple/moderate boundary are themselves debatable.** E002 (four entity audits + consolidation + a prior qualified opinion), E008 (a 50% buyout valuation) and E022 (gender pay gap report + narrative + action plan) were missed by Sonnet with reasonable rationales. A second labeller would likely disagree with me on one or two of them. With single-labeller gold, the complexity metric has a few points of noise either way.
- **Haiku reads "several deliverables" as "several workstreams".** Most of its 14 errors (E004, E022, E034, E046) count deliverables of a single workstream as workstreams. Its rationales are fluent but apply the rubric loosely.
- **Gemini's rationales are rubric fragments, not reasons.** "board-level stakeholders" alone does not tell a team lead why. It is also the slowest model (p95 36s, from thinking tokens).

## Price vs quality

- **Haiku is good enough for routing and not good enough for complexity.** Its routing, review and injection results match Sonnet at about 1/16th of the cost ($2.50 vs $40 per 1,000 enquiries). Complexity only changes routing in one case (complex + high copies the practice head), so a mis-scored complexity mostly misleads the team lead rather than misroutes.
- **Sonnet is the default** because the team lead reads the complexity and rationale. At 10 enquiries a day the difference is about $0.40 a day.
- **Sonnet's cost is input tokens:** 3 runs × about 4k tokens of system prompt. Prompt caching would remove most of it if volume grew.

## What I would not conclude

- That any model is production-ready. 50 synthetic records, labelled by one person, the same person who wrote the rules, cannot show that.
- That the 3-run agreement check is useless. It is untested, which is different.
