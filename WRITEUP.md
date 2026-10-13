# What breaks first, how we'd know, and what happens when the model is wrong

## Summary

**What breaks first.** A confident wrong answer. When an enquiry doesn't fit the firm's categories neatly, the model files it under the nearest service line, all three runs agree, and nothing sends it for review.

The prototype routed all 50 test enquiries correctly, but those enquiries were written alongside the categories. They show the model following the firm's definitions, not coping with real-world variety. New kinds of work are the sharpest risk: they get forced into an existing line without warning.

The second weakness is complexity. The model tends to overrate it, usually because it reads any mention of a board as board-level involvement. That rarely changes who receives the enquiry, but it can mislead the team lead about the effort involved.

**What I'd monitor:**
- **Model errors for each service line.** Every reassignment records why, so a busy lead isn't mistaken for a model error.
- **A weekly spot-check of automatically routed enquiries,** because a confident mistake stays invisible until someone looks.
- How often enquiries go to review and why, shifts in the service-line mix, failures and response times.

**When the model gets it wrong:**
- **Before anything is sent.** Suspected manipulation, conflicts and failed classifications always go to a person. The model can't send anything to an unexpected destination. If it's unavailable, the system tries a backup model, then the review queue.
- **For a single enquiry.** Leads accept or reassign with one click, and each correction becomes new test data.
- **For a recurring mistake.** Definitions and rules can be changed on a configuration screen, which shows which past enquiries a change would affect before it goes live.
- **During rollout.** The system runs silently first, then makes suggestions, then routes on its own. Any service line that starts slipping goes back to suggestions.

---

## Supporting detail

### Not every mistake costs the same

At 40 to 60 enquiries a week, a misrouted enquiry can sit with the wrong person for days before anyone notices, and a slow first response can lose the work. The system treats three kinds of error differently:

- **Must never happen quietly:** missing a conflict of interest, or acting on instructions hidden in an enquiry. These are blocked by fixed rules, whatever the model says.
- **Costly but recoverable:** sending an enquiry to the wrong service line. Monitoring is mainly aimed at catching this quickly.
- **Cheap:** a complexity rating one level out, or sending an enquiry for review when it didn't need it. A person spends a minute on it.

The system is tuned to send more to review rather than less. Catching the cases that need a person matters more than keeping the review queue short.

### Risks, from most to least likely

| Risk | Likelihood | Impact | Early warning | Current safeguard | Next step |
|---|---|---|---|---|---|
| New kinds of work forced into an existing category | High | Medium | Reassignments into one line from many directions; rising out-of-scope share | Leads reassign; definitions can be updated with an impact preview | Let the model say "doesn't fit", and track how often it does |
| Confusion between neighbouring lines (internal vs external audit, technology vs strategy) | Medium | Medium | Enquiries passed back and forth between the same two lines | Each definition says how it differs from its neighbours | A dedicated test set of borderline cases, rechecked on every change |
| Enquiries needing more than one practice | Medium | Medium | Rate of multi-service flags; reassignments between the primary and secondary lines | Secondary lines shown on every enquiry | Copy the secondary line's lead automatically |
| A conflict or existing client missed (name variations, subsidiaries, personal email addresses) | Low to medium | High | Partners reporting a missed client; CRM match rate | Matching on email domain and company name; subsidiaries resolved to their group; conflicts always go to a person | Fuzzy matching, the firm's conflicts system as a gate, and external ownership lookups for prospects |
| Too little known about a new prospect to judge size or complexity | High | Low to medium | Share of enquiries with no CRM or company record | The model works from the enquiry text; the questions-for-the-client list covers the gaps | On-demand external enrichment (Exa, Parallel or Clay), limited to company name and domain |
| A deadline missed because the client marked the enquiry low priority | Medium | High for that client | Rate of urgency mismatches; age of items in review | The model reads urgency from the text and flags mismatches; urgent complex work copies the practice head | Extract deadlines as dates and alert when one is under five working days away |
| Complexity overstated | High (measured) | Low | Complexity corrections in feedback | Shown with its reasoning, and editable | The model cites evidence for each criterion; the rating is calculated from that |
| Provider outage, or a model changing without notice | Medium | Medium | Failure rate, response times, version recorded on every decision | Retry, then a backup model, then the review queue | Pin model versions; run the test suite weekly as an early warning |
| Hidden instructions in an enquiry | Low | High | Rate of injection flags; spot-checks | Enquiry text treated as untrusted; any flag sends it to a person; the model cannot choose a destination | Add a simple pattern check that runs before the model |
| Thin or inaccurate form input | High | Low | Vague-enquiry rate; size mismatches against the CRM | Vague enquiries go to review with suggested questions for the client | Improve the form itself, and draft the follow-up email automatically |
| Client data leaving the firm's control | Low | High | Contract and provider review, not metrics | Not addressed in the prototype | Zero-retention providers only, personal details removed, or a provider inside the firm's own cloud |

### Monitoring plan

| What to watch | Alert when | Owner | How often |
|---|---|---|---|
| Model errors per service line (reassigned because the line was wrong, or corrected) | Above 10% for any line over two weeks | Practice operations | Weekly |
| Accuracy of automatic routing, from a 10% spot-check | Below 95% | Analyst, reported to practice operations | Weekly |
| Share of enquiries sent to review | Outside 10% to 30% | Practice operations | Weekly |
| Time items wait in the review queue | Anything older than one working day | Analyst | Daily |
| Out-of-scope and vague enquiries | Double the four-week average | Practice operations | Weekly |
| Mix of service lines | Any line up or down by half against its usual level | Practice operations | Monthly |
| Classification failures | Above 2% in a day | Engineering | Daily alert |
| Response time (95th percentile) | Above 30 seconds | Engineering | Daily alert |
| Results on the test suite | Any safety case missed, or any measure down more than five points | Engineering | On every change, and weekly |
| Cost per enquiry | Double the usual level | Engineering | Monthly |

### Escalation, from prevention to learning

1. **Prevent.** Suspected manipulation, conflicts and failed classifications go straight to a person.
2. **Catch.** Disagreement between runs, vague requests and out-of-scope enquiries go to the review queue, each with the reason shown.
3. **Correct a single enquiry.** A lead or reviewer reassigns it or corrects the classification with one click, and gives a reason.
4. **Correct a pattern.** When the same mistake keeps appearing, practice operations updates the relevant definition, rule or assignment. They check which past enquiries the change would affect before saving, and rerun the test suite after any change to the model's instructions.
5. **Step back.** If a service line keeps making mistakes, or the provider is down, that line returns to suggestions only and the analyst triages it from the queue.
6. **Learn.** Each week, corrections are added to the test set, so the next change to the model or its instructions is measured against real cases.

### Rollout

| Phase | Length | What changes | Ready to move on when |
|---|---|---|---|
| Shadow | Two to four weeks | The system runs on every enquiry without notifying anyone; the analyst carries on as normal; the two are compared weekly | At least 95% of enquiries needing a person are caught, at least 95% of automatic routes are correct, and no conflict or manipulation attempt is missed |
| Assisted | Four to eight weeks | Team leads receive the suggestion and confirm it with a click; the analyst handles only the review queue | Model errors below 10% for every service line, and spot-checks at or above 95% |
| Automatic for clear-cut enquiries | Ongoing | Enquiries where the runs agree, nothing is flagged and the line is performing well are routed without confirmation | Continues only while each line stays within its thresholds; a breach returns that line to assisted |

The expected result is that the analyst's eight hours a week drop to one or two: the review queue (about one enquiry in six in testing) plus the weekly spot-check. Shadow mode will confirm the real figure.

### The first 90 days

- **First month.**
  - Connect the real CRM and conflicts system, and put single sign-on in front of the application.
  - Send notifications by email and Slack.
  - Start shadow mode.
  - Build a fresh test set labelled independently by two people.
- **Second month.**
  - Make complexity ratings evidence-based.
  - Add a rule that sends audit clients asking for services the firm can't also provide to them straight to a person.
  - Extract deadlines as dates.
  - Move to assisted mode.
- **Third month.**
  - Turn on automatic routing for the service lines that have earned it.
  - Reduce cost by caching the model's standing instructions.
  - Test whether a cheaper first-pass classifier, trained on the accumulated corrections, can handle the easy cases.
  - Pilot on-demand external enrichment for prospects not in the CRM, comparing Exa, Parallel and Clay on accuracy, coverage and cost per lookup.

### Assumptions

- Most of the analyst's eight hours goes on reading and routing, not on talking to clients.
- Team leads will give feedback if it takes one click in the message they already receive.
- The firm has a conflicts system that can be checked before an enquiry is routed.
- Volume stays at around ten enquiries a day, so a single application processing enquiries as they arrive is enough.
