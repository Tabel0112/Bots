# ARGUS — controller and moderator boundary (draft for review)

Status: **draft, not agreed**. Written 2026-09-12 for Abel's review. Nothing here is recorded in DECISIONS or CONTRACTS yet. Once accepted, the agreed parts move to those files and this document becomes the ARGUS-1 worked example.

## Assumptions taken from the user

- Execution is sequential by stage. Only subagents may run concurrently, and each subtask carries its dependencies and its concurrency group at creation time.
- The moderator is a separate deliverable owned by Thomas. Abel owns the ARGUS controller: interpretation, gating, planning, dispatch, monitoring and publication. The controller stays the single owner of run state, budgets and sessions; it calls the moderator at fixed stages through a callable boundary and executes the decision it returns. The moderator is not a competing controller.
- Browser sessions belong to ARGUS. A session is lent to a subagent for one subtask and returned with the report. ARGUS may use it for verification before closing it.
- Visual interpretation (VLM) and HTML/DOM interpretation are tools in a shared toolbox, not separate agents. Thomas and Tianqi build the connection between subagents and Steel; the toolbox is what that connection exposes.
- Natural-language parsing is part of the MVP. The interpreted request is a handoff artifact that every teammate can read.
- Framework first, connections later. Everything below must work with a fake toolbox before Steel is wired in.

## Components

| Component | Owns | Never does |
| --- | --- | --- |
| ARGUS controller (Abel) | Run state, stage transitions, budgets, sessions, event stream, the single terminal result; interpretation, gating, planning, dispatch | Interpret pages, act in the browser |
| Moderator (Thomas) | Report sufficiency, reconciliation across subtasks, synthesis of the final answer | Change budgets, edit validators, promote skills, emit results directly, hold run state |
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
| 10. Synthesize | Moderator call | Validated records, evidence, typed failures, gaps | Final user answer where every claim cites an evidence reference | Rule check afterwards: no claim without a reference, failures and unverified completeness stated plainly |
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
| `synthesize(request, records, validation, evidence, failures)` | Validated records, the validation report, evidence references and typed failures | Final user answer where every claim carries an evidence reference |

Optional fourth callable for live monitoring, if Thomas wants the moderator to watch runs rather than only react to reports: `observe_progress(run_snapshot, event)`. The controller calls it on selected events only, such as subtask started, a stalled subtask, or every N actions, never on every action. It returns `continue`, `flag` (ask the controller for an early read-only verification) or `stop_subtask` with a reason. The controller still enforces budgets and still owns cancellation.

The controller runs the rule checks around these calls: schema validation before `assess_report`, the retry and verification caps, and the no-claim-without-evidence check after `synthesize`. Verification requested by the moderator is executed by the controller with the lent session and the read-only interpretation tools.

## Hard limits on the moderator

- Decisions come from fixed sets. Free text is confined to `reason` fields.
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

## Open-world navigation (user direction 2026-09-12, design not yet built)

The product must handle any site and any read-only task. The controller, store, moderator boundary and session lifecycle above are unchanged. What changes:

| Component | Closed-registry behavior (built) | Open-world behavior (to build) |
| --- | --- | --- |
| Registry | Closed list; unknown operation is rejected | The set of *qualified* operations. Membership is earned by Ghost qualification, not by editing a file. Still consulted first so qualified skills are reused |
| Interpreter | Maps text onto registry entries only | Two-tier output: a registry match when one fits, otherwise an open intent: target site or domain (from the text or a resolvable name), goal in plain words, extracted parameters with spans, expected record shape, and the user's ranking or selection criteria as explicit fields ("best" becomes a criterion, never silently dropped) |
| Gate | Catalog rules G1 to G7 | Catalog rules for registry matches. For open intents, safety rules only: read-only action class, no authentication, no purchases or submissions, target domain resolvable and not on a blocklist, required values present. Clarify when the goal or criteria are ambiguous |
| Planner | Deterministic, one subtask per intent, no chains | Model-planned for open intents: subtasks with dependencies, so "search, then open each result" is expressible. Success conditions are written per run by the planner call, then rule-checked for acyclicity and budget. The deterministic path stays for registry matches |
| Subagent | Preferred tool DOM or vision | Unchanged interface. Explore mode becomes the common case, so the toolbox must return action traces with parameter origins on every run for Ghost compilation |
| Validation | Operation-specific checks | Generic checks always: every record cites an observation from this run, the query or filter visibly took effect, results present or an explicit empty state, no records outside the target domain. Operation-specific checks only when a qualified skill was used. Completeness is reported as unverified on open runs |
| Moderator | Assess, reconcile, synthesize | Synthesize also applies the user's stated criteria to rank or select among evidenced records, and says which criteria could not be applied |
| Ghost | Match, validate, compile | A successful open run compiles to a candidate; qualification promotes it into the registry. This is how the catalog grows |

Consequences to accept: one extra model call per open request for planning; weaker first-contact validation; a bounded blocklist and allowlist policy for domains; a hard cap on chain depth and total subtasks per run so open planning cannot explode budgets.

## Open questions for review

- Does `clarify` need a way to resume a run after the user answers, or is a new request enough for the MVP?
- Should `verify` be allowed to use browser actions, or stay strictly read-only as drafted?
- Is one retry via the other interpretation tool enough, or should the moderator be able to request full re-exploration once?
- Who defines the success conditions per operation: the plan stage, or a fixed table per supported operation?
