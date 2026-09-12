# Person B — Ghost skills, matching, validation, and repair

Name: TBD
Branch/worktree: `feat/ghost` (proposed; not created)
Owned paths: `ghostapi/` (shared contracts and storage changes remain coordinated by C)
Current status: not started / not yet reported
Last updated by owner: not yet
Next checkpoint: compile a sample trace into a strict candidate skill
Blocked by: final operation/check definitions; real trace arrives from A by hour 4

## Current local demo

A simulated terminal demo now exists in [demo/](demo/README.md), with SQLite persistence and five fixture lifecycle tests. The implementation map, limitations, and next work are documented in [DEVELOPMENT.md](DEVELOPMENT.md). The task board below is the original live-integration plan; its unreported statuses do not describe the local fixture demo.

## Mission

Implement Ghost's procedural memory. Turn a validated successful trace into a bounded candidate skill, match compatible requests, bind new parameters, validate the outcome independently, and qualify or quarantine skill versions. A owns browser interaction; C owns storage and run orchestration.

## Skill boundary for the MVP

One skill represents one configured site, one operation, one declared input schema, one output schema, one validator, and one permitted action class. It is reusable across supported parameter values and declared minor UI states. It does not mean “search anything anywhere.”

Matching begins with hard compatibility. Semantic similarity can rank already compatible skills, but it cannot excuse a wrong site, operation, missing input, incompatible output, unqualified status, or new side effect. Partial compatibility falls back to exploration for this MVP.

## Interfaces and dependencies

| You receive | From | You deliver | To |
| --- | --- | --- | --- |
| Stable types, storage API, operation contract | C | MatchDecision, skill versions, reports | C |
| Real normalized trace and step executor | A | CandidateSkill and BoundProcedure | A/C |
| Records and current evidence | A | ValidationReport | C/D |
| Relocation proposal after target failure | A through C | Repair candidate and qualification decision | C |
| Controlled-site ground truth and UI versions | D | Validators and repair tests | C/D |

Start immediately using synthetic fixtures clearly labeled as such and a fake executor. Replace fixture assumptions with A's trace at hour 4; do not shape the trace format independently from C's contract.

## Work order and acceptance checks

### B1. Freeze the operation, outputs, and checks — hours 0–2

- With C, define required parameters, currency, output limit, ProductRecord schema, and unsupported inputs.
- Define task checks independently of the procedure that will be compiled.
- Establish candidate, qualified, and quarantined states.

Done when invalid inputs, unknown fields, missing evidence, over-price items, wrong currency, wrong domain, and ambiguous completion each have explicit outcomes.

### B2. Implement strict binding and matching — hours 1–4

- Match only qualified skills that pass hard compatibility and current preconditions.
- Bind parameter references with type/range checks.
- Reject unknown or missing fields; never reuse the first run's value silently.
- Return a reason for every match/explore decision.

Done when changed valid inputs bind correctly and incompatible inputs produce exploration/rejection in focused tests.

### B3. Compile a candidate from trace — hours 2–7

- Accept only successful, sufficiently evidenced supported action traces.
- Replace values proven to originate in request parameters with named references.
- Preserve semantic target information, expected states, output schema, validator ID, and source run IDs.
- Reject ambiguity instead of guessing parameter mappings.

Done when A's actual trace produces a complete candidate and a deliberately incomplete/ambiguous trace is rejected. A manually authored fixture may prove the interface, but cannot prove automatic compilation.

### B4. Validate and qualify — hours 4–11

- Validate structured records, parameter application, final state/empty state, source-domain evidence, and current-run provenance.
- Keep `failed` and `inconclusive` distinct.
- Qualify only after the agreed fresh-session replay matrix passes.
- Store reports through C's interface; B does not create a competing database.

Done when S3, S6, and S10 behave as specified in [EVALUATION.md](../docs/hackathon/EVALUATION.md).

### B5. Repair lifecycle — hours 10–15

- Accept typed target failures and A's proposed replacement target.
- Verify that the proposal still performs the same semantic step and stays within the skill's authority.
- Create a new candidate version; keep the previous version and failure evidence.
- Reuse only after replay checks qualify the new version. Quarantine the failing scope/version when appropriate.
- Never change the validator as the repair mechanism.

Done when S8 versions and qualifies a supported repair, while S9 stays honest. Keep failure detection and fallback if automatic repair misses the gate.

## How my code works — owner fills this from the implementation

- Entry points and public functions:
- Skill model and version/state storage boundary:
- Hard compatibility rules and optional ranking:
- Trace-to-parameter binding inference:
- Compiler rejection conditions:
- Validator definitions and evidence requirements:
- Qualification policy and actual cases:
- Repair proposal review and version creation:
- Concurrency/idempotency assumptions:
- Files a teammate should read first:

```text
Request + qualified skills -> hard compatibility -> match or explore
Successful trace + original request + fixed checks -> candidate skill
Candidate + replay reports -> qualified or remains candidate
Request + qualified skill -> strict bind -> BoundProcedure
Records + evidence + fixed checks -> ValidationReport
Failure + compatible target proposal -> new candidate version -> replay qualification
```

## Current task board

| ID | Task | Status | Evidence / revision | Next action |
| --- | --- | --- | --- | --- |
| B1 | Operation/check contract | Not started | — | Pair with C |
| B2 | Strict parameter binder | Not started | Not run | Use contract examples |
| B3 | Hard-compatible matcher | Not started | Not run | Define decision reasons |
| B4 | Trace compiler | Not started | Not run | Begin with labeled fixture |
| B5 | Validator | Not started | Not run | Implement independent checks |
| B6 | Qualification state machine | Not started | Not run | Await replay reports |
| B7 | Repair versioning | Not started | Not run | Await controlled v2 |

## Test matrix

| Test | Expected | Status/evidence |
| --- | --- | --- |
| Same site/operation, changed valid inputs | Qualified match and new bindings | Not run |
| Unsupported filter | Explore/reject with reason | Not run |
| Candidate skill in registry | Never selected automatically | Not run |
| Fixed literal where parameter is required | Compiler rejects or test detects | Not run |
| Result exceeds price | Validation fails | Not run |
| Explicit evidenced empty state | Validation may pass with zero items | Not run |
| Missing/ambiguous target proposal | Repair unsupported | Not run |
| Same-semantic target proposal | New candidate version, not instant promotion | Not run |

## Handoffs and requests

| Time | To/from | Exact request or delivered artifact | Needed by | Status |
| --- | --- | --- | --- | --- |
| — | A | Real trace plus value-origin/evidence notes | Hour 4 | Pending |
| — | C | Storage interface and check configuration | Hour 2–4 | Pending |
| — | D | Controlled catalog truth set and change description | Hour 8–12 | Pending |

## Validation actually run

| Time | Revision | Command/scenario | Result | Artifact/notes |
| --- | --- | --- | --- | --- |
| — | — | No checks run | Not run | Initial planning state |

## Decisions, blockers, and risks

| Time | Type | Detail | Owner / next step |
| --- | --- | --- | --- |
| — | Decision needed | Freeze exact input/output/check scope | B + C at kickoff |

## Checkpoint update template

```text
Time / revision:
Status and concrete outcome:
Files changed:
Skill behavior added or changed:
Validation actually run and result:
Unverified items / blocker and owner:
Next action before the next gate:
```
