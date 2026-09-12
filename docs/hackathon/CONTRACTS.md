# Interface contract — version 0.1

**HTML-1 local boundary — awaiting INT-1 agreement:** `SubtaskRequest` / `SubtaskReport` **0.2** is implemented in [browser_worker/schemas.py](../../browser_worker/schemas.py), not adopted as a project-wide contract. See [worker usage and examples](../../browser_worker/README.md). It adds run/subtask IDs, ownership, budgets, dependencies, domain/action restrictions, success/evidence requirements, field provenance, independent checks and visual handoff. It is not wire-compatible with the older `TaskRequest`. INT-1 must reconcile the shapes, then ARGUS (Abel), Ghost (Sting), and moderator/visual (Thomas) must verify their receivers. The 0.1 documentation below is preserved as historical/provisional context.

Status: provisional version 0.1. Executable Python types and synthetic fixtures were built under `backend/contracts/` in a local experiment; they are not included in this documentation commit. This is a reference for that narrow scaffold, not a frozen specification of the full moderator/subtask/vision architecture. Read [current decisions](../ai/DECISIONS.md) before extending it. Current assignments are in [TEAM.md](../ai/TEAM.md). Examples below are synthetic; live browser execution and qualification are not established.

## Request and execution boundary

`TaskRequest`: `schema_version`, `request_id`, `site_id`, `operation`, `parameters`, `mode`.

```json
{
  "schema_version": "0.1",
  "request_id": "request-example",
  "site_id": "demo-catalog",
  "operation": "search_products",
  "parameters": {"query": "headphones", "max_price": 150},
  "mode": "auto"
}
```

`site_id` resolves to a configured origin; it is not an arbitrary browser URL. Operation settings fix currency and maximum result count. Mode is `auto` or `explore`; the latter forces the baseline measurement. ARGUS may accept plain language from the UI, but must expose interpreted parameters and reject/clarify missing required values before execution. Structured fields are the MVP fallback.

Semantic matching never overrides hard compatibility: same supported site/operation, parameters within supported scope, output coverage, permitted action class, qualified skill status, and current preconditions. Unknown filters cannot be silently dropped. For this MVP, partial matches fall back to full exploration; automatic procedure composition is deferred.

## Browser interface — consumed by Ghost and ARGUS

Conceptual functions (the local experiment's provisional signatures are in `backend/contracts/interfaces.py`, if that unpublished scaffold is available):

- `explore(request, checks, emit) -> ExplorationResult`
- `execute_step(session, bound_step) -> StepOutcome`
- `observe(session) -> Observation`
- `extract(session, output_schema) -> ExtractionResult`
- `propose_target(session, step, failure) -> TargetProposal | unsupported`
- `close_session(session)`

The browser adapter owns browser mechanics and the exploratory model loop. Ghost owns skill meaning, binding, checks, and repair acceptance. ARGUS owns cancellation, overall budgets, state, and cleanup orchestration. The browser adapter guarantees cleanup in its lifecycle even when exceptions occur.

`ExplorationResult` includes `session_ref`, `trace`, `items`, `evidence`, `metrics`, and an explicit outcome. Session refs are opaque runtime handles, never saved as reusable skill data.

`Observation`: observation ID, URL, timestamp, current visible controls/text or snapshot reference. `StepOutcome`: `succeeded|failed`, before/after observation refs, and typed failure if present. Operational click success is distinct from final task validation.

`ActionRecord` includes step ID, observation refs, action, semantic target, actual input source/value, outcome, and timestamp. Record enough information to reconstruct parameter bindings; omit secrets and transient credentials.

Allowed initial actions: navigate to configured site path, fill field, select option, click control, wait for expected state, extract records. No arbitrary generated shell/JavaScript execution in skill data.

## Ghost workflow schema and interface

The authoritative workflow schema, version lifecycle, matching, binding, compilation, validation, qualification, and repair interfaces live in [the Ghost API contract](../../ghostapi/CONTRACTS.md). ARGUS remains the run controller, the browser adapter executes steps, and shared storage persists versions and run records.

## Result and validation

`ProductRecord`: `title`, `price`, `currency`, `url`, `source_observation_id`, `retrieved_at`; optional stable site item ID. Unknown price/currency is explicit missing data, never zero or a guessed conversion.

`ValidationReport`: overall `passed|failed|inconclusive`, validator version, and individual checks with `check_id`, status, expected condition, actual observation, evidence refs, and reason.

Required checks are defined independently from the generated procedure: schema completeness, exact currency, numeric price within bound, evidence that the requested query/filter took effect, final results or explicit empty state, valid configured-domain source links, and current-run provenance. The controlled catalog must provide a known expected result set to catch missed or incorrectly included items. On a public site without ground truth, report that completeness is unverified; do not claim exhaustive search or semantic relevance merely because fields are populated.

`RunResult`: run ID, status, executed mode, optional skill ID/version, items, validation, evidence, metrics, error if any. Modes: `exploration|reuse|repair_then_reuse|fallback_exploration`. Statuses: `succeeded|failed|cancelled`; inconclusive validation cannot be presented as validated success.

Metrics: elapsed milliseconds, browser action count, model call count, token counts when available. Unknown token counts are null. Include repair/fallback work in totals. Skill qualification has separate run IDs and costs; show this one-time cost separately from reuse, not hidden inside a claimed saving.

## API and frontend events

- `POST /api/runs`: accepts TaskRequest; returns `run_id` and initial state promptly.
- `GET /api/runs/{run_id}`: current state and final result when present.
- `GET /api/runs/{run_id}/events`: ordered server-sent events; polling the current-run endpoint is the fallback if streaming delays integration.
- `GET /api/skills`: skill summaries, versions, qualification state and evidence references.
- `GET /api/skills/{skill_id}/versions/{version}`: details needed by the skill viewer.

Do not add scheduling, editing, or deletion endpoints for the MVP. A stop control is shown only when cancellation works end to end.

```json
{
  "schema_version": "0.1",
  "run_id": "run-example",
  "sequence": 1,
  "timestamp": "2026-09-12T17:00:00Z",
  "type": "stage_changed",
  "stage": "exploring",
  "message": "Searching the catalog",
  "data": {}
}
```

Stages: `queued`, `matching`, `exploring`, `replaying`, `validating`, `compiling`, `repairing`, `completed`, `failed`, `cancelled`. Events: `stage_changed`, `action_observed`, `validation_completed`, `skill_candidate_created`, `skill_qualified`, `repair_proposed`, `run_completed`, `run_failed`. The controller keeps sequence numbers increasing per run; the dashboard deduplicates replayed events and shows terminal states consistently.

A successful task result may exist before skill qualification finishes. Candidate creation is optional and a compilation failure does not erase a valid task result. Qualification runs are separate and only qualified skills are selected automatically. The dashboard must distinguish “task completed,” “candidate saved,” and “skill qualified.”

Errors have `code`, `message`, `retryable`, optional `step_id`, and evidence refs. Initial codes: `NO_MATCH`, `INVALID_INPUT`, `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS`, `PRECONDITION_FAILED`, `NAVIGATION_TIMEOUT`, `AUTH_REQUIRED`, `EXTRACTION_FAILED`, `VALIDATION_FAILED`, `UNSUPPORTED_CHANGE`, `BUDGET_EXCEEDED`, `CANCELLED`.

Repair budget: at most one repair attempt per failed replay in the MVP, followed by at most one bounded exploration fallback if appropriate. Authentication and unsupported action changes stop with an actionable error. Agree and document overall time/action limits for the live integration. No recursive retry loops.

## Required fixture messages and change procedure

Shared examples cover exploration success, qualified reuse, candidate creation, validation failure, repaired replay, fallback exploration, and terminal failure. The dashboard uses these only in clearly labeled sample mode. Ghost tests use labeled synthetic skill fixtures. Browser integration must supply real trace examples with sensitive data removed.

Before breaking this contract, record the exact field/behavior change, increment its version, update examples, and identify affected consumers. Check compatibility before integration. This documentation cleanup changes status and personnel labels only; it does not change the executable version 0.1 schema.
