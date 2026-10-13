# What breaks first, how we'd know, and what happens when the model is wrong

## Summary

**What breaks first.** A confident wrong answer. When an enquiry doesn't fit the firm's categories neatly, the model files it under the nearest service line, all three runs agree, and nothing sends it for review.

The prototype routed all 50 test enquiries correctly, but those enquiries were written alongside the categories. They show the model following the firm's definitions, not coping with real-world variety. New kinds of work are the sharpest risk: they get forced into an existing line without warning.

The second weakness is complexity. The model tends to overrate it, usually because it reads any mention of a board as board-level involvement. That rarely changes who receives the enquiry, but it can mislead the team lead about the effort involved.

**What I'd monitor:**
- **Model errors for each service line.** Every reassignment records why, so a busy lead isn't mistaken for a model error.
- **A weekly spot-check of automatically routed enquiries,** because a confident mistake stays invisible until someone looks.
- How often enquiries go to review and why, shifts in the service-line mix, failures and response times.

**When the model gets it wrong.** The principle is that no mistake should be silent. Every wrong answer has either a person in front of it or a deadline behind it.
- **Before anything is sent:** suspected manipulation, conflicts and failed classifications always go to a person. An enquiry is archived as spam only if all three runs agree, so a genuine client can't disappear quietly.
- **After it's sent:** if the lead neither accepts nor reassigns the enquiry within a deadline (4 hours for urgent work, 24 for normal), it comes back to the review queue automatically. A misroute nobody notices still resurfaces.
- **For one enquiry:** a one-click reassign, with a reason. The correction becomes test data.
- **For a pattern:** fix the definition or rule, with a preview of which past enquiries it would affect. If the cause isn't clear yet, switch that service line, or the whole system, to human triage while it's investigated. Every change can be reverted in one click.

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

### The fallback for each way the model can be wrong

| What goes wrong | How it's caught | What happens automatically | How we recover |
|---|---|---|---|
| **Wrong service line, stated confidently** (the most likely failure) | The lead reassigns it; the weekly spot-check; model-error rate per line | If nobody accepts or reassigns it within the deadline (4 hours urgent, 24 normal, 48 low), it returns to the review queue | One-click reassign with a reason; the correction becomes test data. Repeated errors in a line: fix its definition, or put the line in fallback mode |
| **A genuine enquiry mistaken for spam** | The three runs disagree; a weekly look through the archive | Archived only when all three runs agree; otherwise it goes to review | Restore it from the archive with one click (reassign to a lead) |
| **Urgency missed** (a deadline the client didn't flag) | The model reads urgency from the text and flags mismatches with the form | The acknowledgement deadline uses whichever is higher, stated or inferred urgency; complex urgent work copies the practice head | The reviewer adjusts and routes it; the case is added to the test set |
| **A conflict or existing client missed** | A known conflict is a hard stop; subsidiaries are resolved to their group; partners report misses | Known conflicts never route automatically | Reassign with the reason "conflict"; correct the CRM record; in production, the conflicts system becomes a gate |
| **Instructions hidden in an enquiry** | Flagged if any one of the three runs spots it | Always goes to a person; the model can't choose a destination anyway | The reviewer handles it; the example joins the test set |
| **Complexity misjudged** | Shown with its reasoning; the lead corrects it | Rarely changes routing (only urgent complex work copies the practice head) | Correction recorded; rubric wording reviewed if the pattern persists |
| **The model or provider fails** | Failure rate and response-time alerts | Retry, then a backup model, then the review queue; nothing is dropped | Pause automatic routing until it's stable |
| **A change makes things worse** (prompt, model or rules) | Test-suite run on every change; model-error rate after release | Every decision records which prompt and configuration produced it | Revert the change in one click; pause or put lines in fallback mode meanwhile |

### Switches for when something is wrong

All three switches sit on the configuration screen. Each shows its effect on past enquiries before it's saved.
- **Pause automatic routing.** Every enquiry goes to the review queue, as it would have before the system existed.
- **Fallback mode for one service line.** Its enquiries go to a person while the problem is investigated; other lines are unaffected.
- **Revert.** Any configuration change can be undone in one click from the change history.

### Learning from mistakes

Each week the corrections, and the reasons given for them, are added to the test set. The next change to the model or its instructions is then measured against real cases before it goes live.

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
