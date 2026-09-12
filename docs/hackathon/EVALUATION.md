# Evaluation and demo checklist

Status: planned live-demo checks; none has been run. Current workstreams are assigned in [TEAM.md](../ai/TEAM.md); ownership of the overall demo/acceptance effort remains open. The synthetic scaffold's earlier checks are recorded separately in [CURRENT.md](../ai/CURRENT.md). The [Ghost fixture demo](../../ghostapi/DEVELOPMENT.md#validation) has separate local checks and does not establish live browser readiness.

## What the demo must prove

1. A browser agent can complete the selected read-only task from current page observations.
2. Ghost constructs a candidate procedure from the actual successful trace.
3. The procedure contains parameter references instead of the first request's fixed values.
4. A later compatible request selects a qualified skill, binds different values, and fetches current results.
5. Independent checks reject an incorrect result or failed workflow.
6. A supported UI change causes a detectable failure and a bounded repair or exploration fallback.
7. The dashboard accurately reports the path, skill state, evidence, and observed measurements.

## Fixed scenarios

Freeze exact input values after selecting the public site. Do not tune them during judging unless the live site becomes unavailable.

| ID | Scenario | Required observation |
| --- | --- | --- |
| S1 | Public-site exploration with initial input set | Fresh results and source evidence pass available checks; actual actions are recorded |
| S2 | Candidate compilation from S1 | Candidate refers to request parameters; source run is linked; status is candidate |
| S3 | Qualification replays with changed input sets | Required qualification cases pass in fresh sessions; skill becomes qualified only then |
| S4 | Compatible request with different input | Qualified skill is selected, new values appear on the page, fresh results pass checks |
| S5 | Unsupported parameter or operation | Matcher explains incompatibility and explores or rejects; unsupported input is never ignored |
| S6 | Deliberately invalid extracted record | Validator fails the price/currency/schema/source check and UI shows failure |
| S7 | Controlled site v1 | Exploration and skill lifecycle complete for the controlled site's own skill |
| S8 | Controlled site v2 with supported control change | Old version fails observably; one repair is proposed, replayed and qualified before use |
| S9 | Controlled unsupported change | Run stops or falls back with a clear state; no false success |
| S10 | Empty real result set | Explicit empty page is accepted as a valid result only when filters and empty state are evidenced |

## Evidence to record for every run

- Run ID, revision, website/config version, request parameters, mode, and skill ID/version if used.
- Start/end time, elapsed time, browser action count, model-call count, and token counts when available.
- The interpreted request and the visible evidence that each parameter took effect.
- Result records with current source links and observation references.
- Every required validation check and its pass/fail/inconclusive status.
- Failure category, repair/fallback attempt, and total cost of all work performed.

Do not record passwords, cookies, access tokens, or personal account data. Redact authenticated artifacts before saving them.

## Comparison method

Compare exploration and reuse only on the same site, operation, result limit, environment, and comparable inputs. Run each path at least twice if time permits and report individual values plus the median. Do not claim that reuse reduces browser clicks unless the data shows it; the expected advantage may be fewer model decisions, lower token use, lower latency, or more consistent execution.

Report qualification cost separately. The honest calculation is:

`total learned-capability cost = original exploration + compilation + qualification replays`

`per-reuse cost = one qualified replay, including validation and any recovery`

The demo can show both. Avoid claiming break-even unless enough comparable runs establish it.

## Manual acceptance checklist

- [ ] From a clean start, setup instructions lead to a running backend, dashboard, and controlled site.
- [ ] Sample-data mode is visibly labeled and cannot be confused with live execution.
- [ ] The UI shows whether a run explored, reused, repaired, fell back, failed, or was cancelled.
- [ ] Candidate and qualified skills have distinct labels.
- [ ] Changed parameters appear in the interpreted request and browser evidence.
- [ ] All displayed result links come from the current run.
- [ ] Empty, validation-failed, browser-failed, and API-disconnected states are understandable.
- [ ] No button is displayed unless its action works end to end.
- [ ] No secret or private browsing artifact appears in the interface, repository, logs, or recording.
- [ ] The backup recording is playable without network access.
- [ ] The live demo path has been completed twice from the documented starting state.

## Three-minute demo outline

| Time | Presenter shows | Claim supported |
| --- | --- | --- |
| 0:00–0:20 | Problem and one-line architecture | Repeated browser reasoning is slow and brittle; ARGUS calls reusable Ghost capabilities |
| 0:20–0:55 | First unfamiliar request and live browser | The agent observes and performs a real workflow |
| 0:55–1:20 | Trace → candidate → qualification record | Ghost learned a bounded, parameterized procedure and tested its supported scope |
| 1:20–1:55 | New inputs using the qualified skill | Matching, binding, replay, fresh extraction, and validation work |
| 1:55–2:30 | Controlled site update and recovery | Failure is detected; supported repair is tested and versioned |
| 2:30–2:50 | Evidence and measured comparison | Observed benefits and costs, with qualification cost shown separately |
| 2:50–3:00 | Broader vision | ARGUS can compose more learned site capabilities after the hackathon |

If a live path fails, explain the detected state and switch to the backup recording. A detected failure supports the architecture better than manipulating state until a false success appears.

## Claim review before submission

| Potential claim | Evidence required | Safe wording if evidence is incomplete |
| --- | --- | --- |
| “Learns workflows automatically” | Actual action trace compiled without a human-authored step list | “Creates a candidate procedure from a successful trace; current supported actions are…” |
| “Self-healing” | Reproducible failure, repair, replay, and qualification | “Detects changes and attempts one bounded repair for supported target changes” |
| “General” | Held-out variations across declared dimensions | “Demonstrated on these sites, operations, inputs and UI changes” |
| “More efficient” | Controlled comparisons with full costs | “In our measured runs, reuse changed these metrics…” |
| “Verified” | Independent checks and evidence | “Passed the listed checks; completeness/relevance remain unverified where stated” |

## Final release record

Fill this in rather than deleting the template.

| Item | Value |
| --- | --- |
| Demo revision | Not set |
| Public-site scenario result | Not run |
| Controlled repair result | Not run |
| Setup test result | Not run |
| Secrets scan result | Not run |
| Backup video path/link | Not created |
| Remaining risks | Not assessed |
| Presenter and backup presenter | Not assigned |
