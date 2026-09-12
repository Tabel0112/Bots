# Person A — browser execution and recording

Name: TBD  
Branch/worktree: `feat/browser` (proposed; not created)  
Owned paths: `backend/browser/`, `tests/browser/`  
Current status: not started / not yet reported  
Last updated by owner: not yet  
Next checkpoint: prove one selected workflow and produce a sanitized trace  
Blocked by: site and stack selection at kickoff

## Mission

Give ARGUS a reliable way to observe and operate a real browser. Produce the source trajectory Ghost uses for compilation and the deterministic executor Ghost uses for replay. Your component reports facts about actions and page state; B decides what constitutes a valid reusable skill, and C decides how a full run proceeds.

## Interfaces and dependencies

You implement the browser functions in [CONTRACTS.md](../CONTRACTS.md). Ask C to change that shared contract; do not create a parallel message format.

| You receive | From | You deliver | To |
| --- | --- | --- | --- |
| TaskRequest, checks, budgets, event callback | C | ExplorationResult and typed errors | C |
| Bound step and current session | B through C | StepOutcome and observations | B/C |
| Output schema | B/C | Records and evidence refs | B/C |
| Target-repair request | B/C | Semantic TargetProposal or unsupported | B/C |
| Controlled site v1/v2 and change description after first attempt | D | Reproducible observations and relocation behavior | B/C/D |

You can begin with fake TaskRequest values and the contract before B's compiler or C's API exists. B can begin with a fake executor before your replay is complete.

## Work order and acceptance checks

### A1. Prove the browser adapter — hours 0–2

- Test the chosen Steel/browser-agent starter against one candidate public site.
- Confirm that page observations, actions, current URLs, result extraction, and session cleanup are accessible.
- Confirm that you can capture action-level data rather than only a final model answer.
- Reject or switch sites quickly if access, anti-bot behavior, or dynamic controls are unreliable.

Done when one saved run contains a readable observation/action sequence, current source evidence, and confirmed cleanup. Record the actual library/version and run command below.

### A2. Implement exploration and normalized recording — hours 2–5

- Wrap the exploratory agent behind `explore`.
- Emit typed stages/actions for C without coupling to the dashboard.
- Normalize supported actions into ActionRecords with before/after observation references.
- Mark which filled values came from request parameters.
- Remove secrets and transient session data from anything persisted.

Done when the same request completes twice or the second failure is classified with useful evidence. Hand B a real trace by hour 4 even if extraction still needs work.

### A3. Implement deterministic step execution and extraction — hours 5–10

- Resolve semantic targets from current labels/roles/text and stable site context.
- Execute the bounded action vocabulary in CONTRACTS.md.
- Check each step's expected state and return a typed failure when it does not hold.
- Extract the ProductRecord fields and current-run evidence.
- Always close sessions on success, failure, timeout, or cancellation.

Done when a hand-authored test procedure can run on the controlled site and changed parameter values are visible in evidence. This tests the executor; do not present it as learned-skill evidence.

### A4. Support one repair path — hours 10–15

- When a target is missing/ambiguous, observe the current page and propose one semantically compatible replacement.
- Return the proposal to B/C; do not silently mutate stored skill data.
- Distinguish a relocated control from a different operation, authentication gate, or unsupported flow.

Done when controlled site v2 produces an initial target failure, a proposal, and a successful replay candidate that B can qualify. If that fails, make fallback exploration reliable and report the limitation.

### A5. Stabilize and hand off — hours 15–20

- Run the assigned S1/S3/S4/S7/S8/S10 cases in [EVALUATION.md](../EVALUATION.md).
- Document clean-start commands, required environment-variable names, browser-session limits, and known failure modes.
- Give D safe, minimal evidence for the UI/video.

## How my code works — owner fills this from the implementation

Do not describe planned behavior as implemented. Replace these prompts with file links and actual control flow.

- Entry point and public functions:
- Browser/session provider and versions:
- How observations are captured:
- How exploratory actions become ActionRecords:
- How semantic targets are resolved during replay:
- How values remain linked to request parameters:
- How extraction and evidence references work:
- Cleanup, cancellation, timeout, and retry behavior:
- Error mapping and unsupported cases:
- Files a teammate should read first:

```text
TaskRequest + checks
    -> session
    -> observe / reason / act loop
    -> normalized trace + records + evidence

BoundProcedure
    -> fresh session
    -> resolve current semantic target
    -> execute step and check expected state
    -> extract records + evidence
```

## Current task board

| ID | Task | Status | Evidence / revision | Next action |
| --- | --- | --- | --- | --- |
| A1 | Choose and prove browser adapter | Not started | Not run | Test candidate site |
| A2 | Exploration wrapper | Not started | — | Await stack freeze |
| A3 | Normalized ActionRecord capture | Not started | — | Use contract fixture |
| A4 | Semantic step executor | Not started | — | Start against controlled v1 |
| A5 | Extraction and evidence | Not started | — | Align with ProductRecord |
| A6 | Target relocation proposal | Not started | — | Await controlled v2 |
| A7 | Documentation and stabilization | Not started | — | Begin after first path |

Allowed status values: `not started`, `in progress`, `blocked`, `ready for integration`, `done`, `cut`. “Done” requires recorded evidence.

## Handoffs and requests

| Time | To/from | Exact request or delivered artifact | Needed by | Status |
| --- | --- | --- | --- | --- |
| — | B | Sanitized real trace and ActionRecord notes | Hour 4 | Pending |
| — | C | Browser interface implementation and config | Hour 4 | Pending |
| — | D | Safe observation/evidence fixtures | Hour 8 | Pending |

## Validation actually run

| Time | Revision | Command/scenario | Result | Artifact/notes |
| --- | --- | --- | --- | --- |
| — | — | No checks run | Not run | Initial planning state |

## Decisions, blockers, and risks

| Time | Type | Detail | Owner / next step |
| --- | --- | --- | --- |
| — | Decision needed | Select public site and browser library | A + C at kickoff |

## Checkpoint update template

Copy one row into the task board and append evidence above; then update the header.

```text
Time / revision:
Status and concrete outcome:
Files changed:
Interface behavior added or changed:
Validation actually run and result:
Unverified items / blocker and owner:
Next action before the next gate:
```
