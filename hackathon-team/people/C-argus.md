# Person C — ARGUS, shared contracts, API, and integration

Name: TBD  
Role: integration lead  
Branch/worktree: `feat/argus` (proposed; not created)  
Owned paths: `backend/argus/`, `backend/contracts/`, `backend/storage/`, `backend/api/`, `tests/integration/`, backend/root setup and shared config  
Current status: not started / not yet reported  
Last updated by owner: not yet  
Next checkpoint: publish executable contract types, fixture events, and an in-memory run path  
Blocked by: team stack decision and repository authentication

## Mission

Build the thin ARGUS brain and make the four workstreams one product. Interpret the request into the operation contract, choose exploration or qualified skill reuse, coordinate browser execution and Ghost validation, store run/skill evidence, expose progress to the dashboard, and enforce finite budgets. You own shared contracts and integration decisions.

## Interfaces and dependencies

| You receive | From | You deliver | To |
| --- | --- | --- | --- |
| Exploration, step outcomes, records, evidence, browser metrics | A | Budgets, cancellation, run context, stored outcomes | A/B/D |
| Match/compile/bind/validate/qualify/repair reports | B | Orchestration order and durable version/run records | A/B/D |
| Dashboard needs and controlled-site URLs/versions | D | API/events, examples, terminal states | D |

Your first job is to let all owners work without waiting. Generate language-native types and sample messages from [CONTRACTS.md](../CONTRACTS.md), or implement them directly once with tests for serialization. Do not let A and B create separate definitions of the same object.

## Run behavior

```text
accept request
  -> interpret and validate inputs
  -> ask Ghost for a compatible qualified skill
       -> match: bind and replay through A
       -> no match: explore through A
  -> validate records through B
  -> if exploration succeeded: attempt candidate compilation separately
  -> if replay target fails: permit one repair attempt, then bounded fallback
  -> persist evidence, measurements, skill state and terminal result
  -> emit ordered events throughout
```

A valid task result is not dependent on candidate compilation succeeding. Skill qualification uses separate runs and status. Make this distinction visible in storage and events.

## Work order and acceptance checks

### C1. Kickoff and executable contracts — hours 0–2

- Record names A–D, actual event deadline, workflow/site, stack, currency, output limit, and budgets in PLAN.md.
- Initialize project structure and one setup path after Git access is ready.
- Implement shared types, errors, serialization, and versioned example messages.
- Supply typed fake browser/Ghost implementations so integration and UI can proceed.
- Freeze contract 0.1 and own migration when it changes.

Done when A/B/D can import or consume one authoritative schema and sample mode is clearly labeled.

### C2. Run state, storage, and API — hours 1–5

- Create run IDs and an explicit run-state machine.
- Store requests, events, results, validation reports, evidence refs, skill versions, and qualification records.
- Start with an embedded database or simple durable store; only C writes its schema/migration.
- Expose create/status/events/skills endpoints from CONTRACTS.md.
- Make run/event creation idempotent enough for UI reconnects; events have sequence numbers.

Done when a fake exploration can be started through the API, followed to completion, and recovered by polling after a simulated stream disconnect.

### C3. Thin ARGUS orchestration — hours 3–8

- Interpret plain language or fall back to structured fields; show the resulting parameters.
- Enforce site/operation configuration, action/time/model budgets, cancellation, and one terminal state.
- Sequence match → bind/replay or exploration → validation → result.
- Attempt candidate compilation after a successful exploration without invalidating its task result if compilation fails.
- Emit meaningful events and store all reports.

Done when one real exploration returns validated data through D's dashboard by hour 8.

### C4. Reuse, qualification, and repair integration — hours 8–15

- Connect B's candidate/qualification state transitions.
- Ensure automatic selection only sees qualified skills.
- Route A's typed target failure into one bounded repair attempt and B's repair qualification.
- Include fallback/repair costs in metrics and preserve previous versions.

Done when S4/S8/S9 have correct paths and terminal states. Remove repair from the demo if it threatens the working exploration/reuse path.

### C5. Integration and release — hours 15–24

- Be the only checkpoint integrator. Resolve contract conflicts and protect unrelated changes.
- Run focused integration cases after hours 4, 8, 12, and feature freeze.
- Maintain `.env.example` with variable names only, setup instructions, deterministic demo commands, and a clean-start check.
- Coordinate the final demo revision and freeze it before recording.

## How my code works — owner fills this from the implementation

- Application entry point and setup command:
- Shared type/schema source and generated outputs:
- Run state transitions and terminal-state enforcement:
- Orchestration path for exploration:
- Orchestration path for qualified reuse:
- Candidate compilation/qualification scheduling:
- Repair/fallback budget and decisions:
- Storage layout and versioning:
- API/event delivery and reconnect behavior:
- Cancellation, cleanup, timeouts, and error mapping:
- Files a teammate should read first:

## Current task board

| ID | Task | Status | Evidence / revision | Next action |
| --- | --- | --- | --- | --- |
| C1 | Team decisions and repository scaffold | Blocked | Repo restore failed auth | Restore authenticated checkout |
| C2 | Shared executable contracts | Not started | — | Freeze stack/operation |
| C3 | Fixture services/events | Not started | — | Implement sample mode |
| C4 | Run state and storage | Not started | — | Choose minimal durable store |
| C5 | API and event stream/polling | Not started | — | Implement after C2 |
| C6 | ARGUS controller | Not started | — | Connect fake interfaces |
| C7 | Real integration | Not started | — | Await A/B interfaces |
| C8 | Release setup and demo revision | Not started | — | Begin after feature freeze |

## Integration gates

| Gate | Required behavior | Revision/result | Open issues and owners |
| --- | --- | --- | --- |
| Hour 4 | API runs fake path; A trace delivered; B/D fixtures compatible | Not run | TBD |
| Hour 8 | Real exploration through dashboard with validation/evidence | Not run | TBD |
| Hour 12 | Changed-input qualified replay | Not run | TBD |
| Hour 16 | Repair or honest fallback; feature freeze | Not run | TBD |
| Hour 20 | Clean-start demo and backup recording | Not run | TBD |

## Contract change log

| Time | Old → new | Exact change | Reason | Consumers migrated |
| --- | --- | --- | --- | --- |
| — | 0.1 | Initial proposed contract | Team baseline | Not implemented |

## Validation actually run

| Time | Revision | Command/scenario | Result | Artifact/notes |
| --- | --- | --- | --- | --- |
| — | — | No implementation checks run | Not run | Documentation only |

## Decisions, blockers, and risks

| Time | Type | Detail | Owner / next step |
| --- | --- | --- | --- |
| 2026-09-12 | Blocker | Private `Tabel0112/Bots` checkout was absent; HTTPS restore requested credentials unavailable to the terminal | C/user restores Git authentication; docs remain local meanwhile |

## Checkpoint update template

```text
Time / revision:
Status and integrated behavior:
Files changed:
Contract/API/storage change:
Validation actually run and result:
Open integration issue and owner:
Next gate and stopping condition:
```
