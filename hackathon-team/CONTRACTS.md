# Interface contract — version 0.1

Owner: C. Status: proposed live implementation contract; the Ghost terminal demo implements a simplified subset. A/B/C agree on exact language types at kickoff; D depends on the JSON shapes. Examples below are synthetic.

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

`site_id` resolves to a configured origin; it is not an arbitrary browser URL. Operation settings fix currency and maximum result count. Mode is `auto` or `explore`; the latter forces the baseline measurement. C may accept plain language in the UI, but must show the interpreted parameters and reject/clarify missing required values before execution. Structured fields are the MVP fallback.

Semantic matching never overrides hard compatibility: same supported site/operation, parameters within supported scope, output coverage, permitted action class, qualified skill status, and current preconditions. Unknown filters cannot be silently dropped. For this MVP, partial matches fall back to full exploration; automatic procedure composition is deferred.

## Browser interface — A supplies, B/C consume

Conceptual functions (language signatures finalized by C):

- `explore(request, checks, emit) -> ExplorationResult`
- `execute_step(session, bound_step) -> StepOutcome`
- `observe(session) -> Observation`
- `extract(session, output_schema) -> ExtractionResult`
- `propose_target(session, step, failure) -> TargetProposal | unsupported`
- `close_session(session)`

A owns browser mechanics and the exploratory model loop. B owns skill meaning, binding, checks, and repair acceptance. C owns cancellation, overall budgets, state, and cleanup orchestration. A guarantees cleanup in its browser lifecycle even when exceptions occur.

`ExplorationResult` includes `session_ref`, `trace`, `items`, `evidence`, `metrics`, and an explicit outcome. Session refs are opaque runtime handles, never saved as reusable skill data.

`Observation`: observation ID, URL, timestamp, current visible controls/text or snapshot reference. `StepOutcome`: `succeeded|failed`, before/after observation refs, and typed failure if present. Operational click success is distinct from final task validation.

`ActionRecord` includes step ID, observation refs, action, semantic target, actual input source/value, outcome, and timestamp. Record enough information to reconstruct parameter bindings; omit secrets and transient credentials.

Allowed initial actions: navigate to configured site path, fill field, select option, click control, wait for expected state, extract records. No arbitrary generated shell/JavaScript execution in skill data.

## Skill schema — B supplies, C stores

The [workflow schema and version lifecycle](ghsotapi/CONTRACTS.md#skill-schema--b-supplies-c-stores) now live in the Ghost API folder.

## Ghost interface — B supplies, C consumes

The [Ghost function interface](ghsotapi/CONTRACTS.md#ghost-interface--b-supplies-c-consumes) now lives in the Ghost API folder. C remains the run controller and storage owner.

## Result and validation

`ProductRecord`: `title`, `price`, `currency`, `url`, `source_observation_id`, `retrieved_at`; optional stable site item ID. Unknown price/currency is explicit missing data, never zero or a guessed conversion.

`ValidationReport`: overall `passed|failed|inconclusive`, validator version, and individual checks with `check_id`, status, expected condition, actual observation, evidence refs, and reason.

Required checks are defined independently from the generated procedure: schema completeness, exact currency, numeric price within bound, evidence that the requested query/filter took effect, final results or explicit empty state, valid configured-domain source links, and current-run provenance. D's controlled catalog provides a known expected result set to catch missed or incorrectly included items. On a public site without ground truth, report that completeness is unverified; do not claim exhaustive search or semantic relevance merely because fields are populated.

`RunResult`: run ID, status, executed mode, optional skill ID/version, items, validation, evidence, metrics, error if any. Modes: `exploration|reuse|repair_then_reuse|fallback_exploration`. Statuses: `succeeded|failed|cancelled`; inconclusive validation cannot be presented as validated success.

Metrics: elapsed milliseconds, browser action count, model call count, token counts when available. Unknown token counts are null. Include repair/fallback work in totals. Skill qualification has separate run IDs and costs; show this one-time cost separately from reuse, not hidden inside a claimed saving.

## API and frontend events — C supplies, D consumes

- `POST /api/runs`: accepts TaskRequest; returns `run_id` and initial state promptly.
- `GET /api/runs/{run_id}`: current state and final result when present.
- `GET /api/runs/{run_id}/events`: ordered server-sent events; polling the current-run endpoint is the fallback if streaming delays integration.
- `GET /api/skills`: skill summaries, versions, qualification state and evidence references.
- `GET /api/skills/{skill_id}/versions/{version}`: details needed by the skill viewer.

Do not add scheduling, editing, or deletion endpoints for the MVP. A stop control is shown only if C implements cancellation end to end.

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

Stages: `queued`, `matching`, `exploring`, `replaying`, `validating`, `compiling`, `repairing`, `completed`, `failed`, `cancelled`. Events: `stage_changed`, `action_observed`, `validation_completed`, `skill_candidate_created`, `skill_qualified`, `repair_proposed`, `run_completed`, `run_failed`. C keeps sequence numbers increasing per run; D deduplicates replayed events and shows terminal states consistently.

A successful task result may exist before skill qualification finishes. Candidate creation is optional and a compilation failure does not erase a valid task result. Qualification runs are separate and only qualified skills are selected automatically. D must distinguish “task completed,” “candidate saved,” and “skill qualified.”

Errors have `code`, `message`, `retryable`, optional `step_id`, and evidence refs. Initial codes: `NO_MATCH`, `INVALID_INPUT`, `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS`, `PRECONDITION_FAILED`, `NAVIGATION_TIMEOUT`, `AUTH_REQUIRED`, `EXTRACTION_FAILED`, `VALIDATION_FAILED`, `UNSUPPORTED_CHANGE`, `BUDGET_EXCEEDED`, `CANCELLED`.

Repair budget: at most one repair attempt per failed replay in the MVP, followed by at most one bounded exploration fallback if appropriate. Authentication and unsupported action changes stop with an actionable error. C sets and documents overall time/action limits at kickoff. No recursive retry loops.

## Required fixture messages and change procedure

C owns examples for exploration success, qualified reuse, candidate creation, validation failure, repaired replay, fallback exploration, and terminal failure. D uses these only in clearly labeled sample mode. B provides synthetic skill fixtures within their own test paths. A supplies real trace examples with sensitive data removed.

Before breaking this contract, C records the exact field/behavior change, increments its version, updates examples, and lists consumers that must adapt. A/B/D confirm compatibility in their work logs before the next integration gate.
