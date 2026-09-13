# ARGUS — controller and moderator boundary (draft for review)

Status: **draft, not agreed**. Written 2026-09-12 for Abel's review. Nothing here is recorded in DECISIONS or CONTRACTS yet. Once accepted, the agreed parts move to those files and this document becomes the ARGUS-1 worked example.

## Assumptions taken from the user

- Execution is sequential by stage. Only subagents may run concurrently, and each subtask carries its dependencies and its concurrency group at creation time.
- The moderator is a separate deliverable owned by Thomas. Abel owns the ARGUS controller: interpretation, gating, planning, dispatch, monitoring and publication. The controller stays the single owner of run state, budgets and sessions; it calls the moderator at fixed stages through a callable boundary and executes the decision it returns. The moderator is not a competing controller.
- Browser sessions belong to ARGUS. A session is lent to a subagent for one subtask and returned with the report. ARGUS may use it for verification before closing it.
- Visual interpretation (VLM) and HTML/DOM interpretation are tools in a shared toolbox, not separate agents. Thomas and Tianqi build the connection between subagents and Steel; the toolbox is what that connection exposes.
- Natural-language parsing is part of the MVP. The interpreted request is a handoff artifact that every teammate can read.
- Framework first, connections later. Everything below must work with a fake toolbox before Steel is wired in.


> **Local VLM capacity:** at most 4 VLM executions may run locally at once. For the MVP, each run is capped at 4 concurrent workers because any worker may need vision. Separately, an open plan has at most 4 total subtasks and depth 3. A subtask may open ten results sequentially within its action/time budget. The live shared toolbox must enforce four VLM slots across runs and callers; per-run worker caps alone do not enforce a machine-wide limit. Revisit these separate limits when hardware or scope changes.

## Components

| Component | Owns | Never does |
| --- | --- | --- |
| ARGUS controller (Abel) | Run state, stage transitions, budgets, sessions, event stream, the single terminal result; interpretation, gating, planning, dispatch | Interpret pages, act in the browser |
| Moderator (Thomas) | Report sufficiency, reconciliation across subtasks, selection and ordering of validated records and fields | Write user-facing prose, change budgets, edit validators, promote skills, emit results directly, hold run state |
| Subagent | One subtask, using the lent session and the toolbox | Open or close sessions, talk to Ghost, spawn other subagents |
| Toolbox (Thomas, Tianqi) | Browser actions via Steel, `dom_interpret`, `vision_interpret`, `observe`, `extract` | Hold run state, retry on its own |
| Ghost (Sting) | `match`, `bind`, `validate`, `compile`, `qualify`, `propose_repair` | Create controllers or browser backends |

## Stage by stage: rule or model

Each stage is a **rule** (plain code, deterministic), a **planner call** (model-backed, inside Abel's controller) or a **moderator call** (model-backed, implemented by the moderator owner and invoked by the controller). The controller executes every decision; the planner and moderator only return them.

| Stage | Kind | Input | Output | Notes |
| --- | --- | --- | --- | --- |
| 1. Interpret | Planner call | Raw user text, supported sites and operations | `InterpretedRequest`: site, operation, parameters each with the text span it came from, confidence, missing required fields, ambiguities | Must be the same shape whether the input was text or structured fields |
| 2. Gate | Rule | `InterpretedRequest` | `accept`, `clarify`, or `reject` with the rule that fired | Site known, operation supported, required parameters present, types and scope valid, no unknown filters. `clarify` ends the run as `needs_input` with the questions; nothing executes. `reject` ends with `INVALID_INPUT` |
| 3. Plan | Planner call | Accepted request | `Plan`: ordered subtasks, each with `depends_on`, `concurrency_group`, success conditions, output schema | Rule check afterwards: no cycles, every subtask maps to a supported operation, total within budget. MVP is usually one subtask |
| 4. Match | Rule via Ghost | Each subtask | `reuse` with a qualified skill, or `explore` with reason | Partial matches fall back to explore. Moderator is not consulted |
| 5. Dispatch | Rule | Plan, match decisions | Session opened per subtask, subagent started with budget, session handle, toolbox and mode | A concurrency group is a mutual-exclusion class: at most one subtask per group runs at a time, and subtasks in distinct groups with satisfied dependencies start together up to the concurrency limit. The planner gives intents that share a user-supplied parameter value the same group |
| 6. Monitor | Rule | Events from subagents | Budget enforcement, timeouts, cancellation | The moderator is not invoked per action. A budget breach ends the subtask with `BUDGET_EXCEEDED` |
| 7. Report intake | Rule then moderator call | `WorkerReport` | First a schema check. Then one of `accept`, `verify`, `retry_other_path`, `fail` | See decision rules below |
| 8. Reconcile | Moderator call, only if more than one subtask | All accepted reports | Merged findings, named gaps and conflicts, and for each one `resolve_from_evidence`, `verify`, or `report_as_gap` | Skipped for single-subtask runs |
| 9. Validate | Rule via Ghost | Records, evidence, checks | `ValidationReport` passed, failed or inconclusive | The moderator cannot override a failed validation. On failure it may choose one bounded re-exploration or fail the run |
| 10. Synthesize | Moderator call | Validated records, evidence, typed failures, gaps | `AnswerSelection`: validated-record indices in output order, structured claims (selected-record index plus fields), typed notes | The controller derives the final records, renders every answer line and fails the run on any invalid index, field or note subject; moderator prose never reaches the user |
| 11. Publish | Rule | Final answer, metrics | One terminal result and event; all sessions closed | Also triggers candidate compilation, which cannot erase the task result if it fails |

## Report intake decision rules

The moderator receives a schema-valid `WorkerReport` and the subtask's success conditions, and returns exactly one decision.

- `accept` when the records satisfy the success conditions and each record has a source observation from this run.
- `verify` when records are present but evidence is thin or one success condition is unconfirmed. The controller reuses the returned session, takes a fresh observation and calls `vision_interpret` or `dom_interpret` to confirm. At most one verification per subtask. Verification is read-only: no actions.
- `retry_other_path` when the report carries `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS` or `EXTRACTION_FAILED` and the other interpretation tool has not been tried yet. The subtask is reissued once with the same session and the alternate tool preferred. At most one retry per subtask.
- `fail` for `AUTH_REQUIRED`, `UNSUPPORTED_CHANGE`, `PRECONDITION_FAILED`, a second failure, or a budget breach. The typed failure is carried into the final result unchanged.

When a parallel sibling fails, the controller lets the other siblings finish unless the failed subtask is a dependency of theirs, in which case they are cancelled. The final answer reports which subtasks succeeded and which failed.

## Handoff between the controller and the moderator

Thomas implements three callables, plus an optional fourth for live monitoring (see below). The controller calls them and never lets them touch run state.

| Callable | Controller passes | Moderator returns |
| --- | --- | --- |
| `assess_report(subtask, report, success_conditions)` | One schema-valid `WorkerReport`, the subtask's success conditions, evidence references | `ModeratorDecision` with `accept`, `verify`, `retry_other_path` or `fail` |
| `reconcile(plan, accepted_reports)` | All accepted reports for a multi-subtask run | Merged findings plus a list of gaps and conflicts, each tagged `resolve_from_evidence`, `verify` or `report_as_gap` |
| `synthesize(request, records, validation, evidence, failures)` | Validated records, the validation report, evidence references and typed failures | `AnswerSelection(record_indices, claims, notes)`. `record_indices` point into the controller's validated input and establish output order; each `Claim(record_index, fields)` points into that selected order; `Note(kind, subject)` uses fixed kinds and a subject checked against the request. No records, evidence refs or prose are returned. The controller renders every user-facing line deterministically from validated data (decided 2026-09-13 after Tianqi's PR #10 review: free prose could assert anything while citing real evidence) |

Optional fourth callable for live monitoring, if Thomas wants the moderator to watch runs rather than only react to reports: `observe_progress(run_snapshot, event)`. The controller calls it on selected events only, such as subtask started, a stalled subtask, or every N actions, never on every action. It returns `continue`, `flag` (ask the controller for an early read-only verification) or `stop_subtask` with a reason. The controller still enforces budgets and still owns cancellation.

The controller runs the rule checks around these calls: schema validation before `assess_report`, the retry and verification caps, and strict selection checks after `synthesize` (record indices are unique and in range; claims cover the selection in order; fields exist and exclude provenance-only fields; each selected record's own observation belongs to the run; note subjects belong to a closed controller-owned set). Known prose-shaped fields are discarded before strict decoding and logged by field path only. Verification requested by the moderator is executed by the controller with the lent session and the read-only interpretation tools.

## Hard limits on the moderator

- Decisions come from fixed sets. Free text is confined to `reason` fields, which are internal (events), never user-facing. The final answer is rendered by the controller from validated records; the moderator selects and orders, it does not write.
- The moderator cannot change budgets, retry limits, the validator, or the set of supported sites and operations.
- It cannot promote a candidate skill or mark a run complete. Only the controller writes terminal state.
- Its inputs are the run's own reports and evidence. It does not browse and does not hold session handles.
- At most one moderator call per stage per subtask, so the number of model calls per run is bounded by the plan size.

## Session lifecycle

1. The controller opens a session through the toolbox when a subtask is dispatched.
2. The subagent receives an opaque handle. It acts through the toolbox and returns the handle inside its report.
3. The controller may use the same session for one read-only verification.
4. The controller closes every session in a `finally` block at run end, including on cancellation and crashes.
5. Session handles are never stored in skills or evidence. Evidence refers to observation IDs.

## Messages that must exist

These are the contract additions ARGUS needs. Field-level shapes are for INT-1 to settle.

- `InterpretedRequest` — stage 1 output, the handoff artifact.
- `Plan` and `Subtask` — stage 3 output with dependencies and concurrency groups.
- `SubtaskInput` — what a subagent receives: subtask, session handle, budget, mode, preferred interpretation tool.
- `WorkerReport` — subtask ID, outcome, records, evidence references, actions with parameter origins, typed failures, returned session handle.
- `ModeratorDecision` — stage, decision, reason, evidence references, next action.

## Open-world navigation (user direction 2026-09-12; built offline in phase 1b)

The product must handle any site and any read-only task. The controller, store, moderator boundary and session lifecycle above are unchanged. What changes:

| Component | Closed-registry behavior (built) | Open-world behavior (to build) |
| --- | --- | --- |
| Registry | Closed list; unknown operation is rejected | The set of *qualified* operations. Membership is earned by Ghost qualification, not by editing a file. Still consulted first so qualified skills are reused |
| Interpreter | Maps text onto registry entries only | Two-tier output: a registry match when one fits, otherwise an open intent: target site or domain (from the text or a resolvable name), goal in plain words, extracted parameters with spans, expected record shape, and the user's ranking or selection criteria as explicit fields ("best" becomes a criterion, never silently dropped) |
| Gate | Catalog rules G1 to G7 | Catalog rules for registry matches. For open intents, public targets only; exclude local/private network targets. Public provider documentation is permitted. Clarify ambiguous goals/criteria with concrete example answers. Execution must independently block login, payment and state-changing submissions; mentioning those subjects in a documentation request is not itself a forbidden action |
| Planner | Deterministic, one subtask per intent, no chains | Model-planned for open intents: subtasks with dependencies, so "search, then open each result" is expressible. Success conditions are written per run by the planner call, then rule-checked for acyclicity and budget. The deterministic path stays for registry matches |
| Subagent | Preferred tool DOM or vision | Unchanged interface. Explore mode becomes the common case, so the toolbox must return action traces with parameter origins on every run for Ghost compilation |
| Validation | Operation-specific checks | Generic checks always: every record cites an observation from this run, the query or filter visibly took effect, results present or an explicit empty state, no records outside the target domain. Operation-specific checks only when a qualified skill was used. Completeness is reported as unverified on open runs |
| Moderator | Assess, reconcile, synthesize | Synthesize also applies the user's stated criteria to rank or select among evidenced records, and says which criteria could not be applied |
| Ghost | Match, validate, compile | A successful open run compiles to a candidate; qualification promotes it into the registry. This is how the catalog grows |

Consequences to accept: one extra model call per open request for planning; weaker first-contact validation; a bounded blocklist and allowlist policy for domains; a hard cap on chain depth and total subtasks per run so open planning cannot explode budgets.

Approved by Abel after the Phase 0b review: keep four concurrent workers for local VLM capacity and four total open-plan subtasks as a separate MVP budget. Ask what unclear ranking terms mean and preserve explicit criteria. Example clarification: "What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience." Example answer: "Highest salary, remote only." Store ranking and filtering separately; examples are suggestions, never selected defaults.

Phase 0b carries target_domain, goal, criteria and expected_record_shape on each Subtask as well as Intent, so the worker and Ghost can consume that context without extra protocol arguments. The planner copies that context from the accepted request and re-checks it; the model owns scheduling only. inputs_from.field="url" collects top-level url values from a findings list in order (or retrieves the value from a findings mapping); "findings" passes the complete findings object. A missing required field fails the dependent task with PRECONDITION_FAILED and cancels its dependents; no silent dropping or transformation.

Public-target enforcement has two parts: offline hostname/IP preflight and live transport checks of DNS results, connected destinations, redirects and subsequent requests. The latter must prevent private-address access and DNS rebinding. Only offline preflight exists in ARGUS so far; live toolbox connection and action enforcement are pending.

### Phase 1b status (2026-09-12, offline)

Built and covered by the offline suite: the interpreter's second tier (open intents with target_domain, goal, criteria and expected record shape, every span verified), gate rules S5 then S1 to S4 (a requested action is rejected before any clarification) with the approved ranking clarification, model-planned chains through one `ModelClient` call for scheduling only, `inputs_from` transfer between subtasks, generic open-world validation through a `report_context` keyword on `Ghost.validate`, criteria applied at synthesis, and reconciliation that merges a chain's reports by record url so one job is answered once. Rejections carry their own codes: S2 fails a run with DOMAIN_NOT_ALLOWED, S5 with ACTION_CLASS_NOT_ALLOWED, an over-cap plan with PLAN_TOO_LARGE. Actual behavior, examples and limits are in [argus/README.md](../../argus/README.md).

Not built and not claimed: live DNS/connection/redirect/rebinding and request enforcement, execution-time blocking of login, payment and submissions, shared four-slot VLM arbitration across runs and callers, real Steel sessions, Thomas's moderator in place of the stub, and Sting's verification of the Ghost boundary including the new `report_context` keyword, which he has not seen. No live model call has been made from ARGUS.

## Execution mode (direction 2026-09-12, not designed yet)

See the DECISIONS entry "Execution beyond read-only". Affects: `contracts.Subtask` (action class), `gate.py` (S5 becomes a classifier with a committing class), `controller.py` (a `needs_confirmation` terminal-or-paused state and a resume path), the toolbox (execution-time enforcement of the class, before/after evidence, request capture), the moderator (never accept a committing report without the confirmation record), Ghost (committing skills are never replayed automatically). Sequenced after INT-2.

## Open questions for review

- Does `clarify` need a way to resume a run after the user answers, or is a new request enough for the MVP?
- Should `verify` be allowed to use browser actions, or stay strictly read-only as drafted?
- Is one retry via the other interpretation tool enough, or should the moderator be able to request full re-exploration once?
- Who defines the success conditions per operation: the plan stage, or a fixed table per supported operation?
