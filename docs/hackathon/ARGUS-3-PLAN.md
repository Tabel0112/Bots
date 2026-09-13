# ARGUS-3 — connecting the real components (plan)

Date: 2026-09-13. Status: **packages 1, 2, 3, 4, 5 implemented on `feat/argus-3`; INT-2 and the qualify/reuse slice passed locally the same night with the real moderator (see TEAM); Steel run, P3-LEARN route and P3-SESSION open.** Written by Abel from the
source on `fix/main-stabilize` (main `7e28641` plus the stabilization fix). It
supersedes the prompts in [ARGUS-HANDOFF-3.md](ARGUS-HANDOFF-3.md) where they
conflict and turns [END-TO-END-PLAN.md](END-TO-END-PLAN.md) into ordered packages.

Working mode: **Abel implements everything, tonight, alone.** Teammates are
unavailable until tomorrow; they verify their boundary and run the demos then.
Decisions below are Abel's, recorded for them to challenge tomorrow. Every package is
one commit on a branch with tests; pull requests are opened for the record and merged
by Abel once CI is green, since no other reviewer is awake. Reuse existing code
wherever it fits; write new code only where nothing exists.

## Goal

INT-2 tonight: one natural-language request in Mission Control runs through the
real ARGUS interpreter and planner, Tianqi's DOM worker, Sting's Ghost API (lookup
and candidate save) and Thomas's moderator module adapted to the controller's
interface, with the controller rendering the answer from validated records and real
evidence. Local Chromium first; Steel is a configuration switch once the catalog is
hosted. INT-3 first slice (qualify, then reuse on a changed input) if time allows,
otherwise tomorrow.

## What already exists and is reused

| Existing code | Author | Reused as |
| --- | --- | --- |
| `Agents/browser_worker/ghost.py` (`GhostWorkflow`), `ghost_adapter.py`, `Agents/visual/browser_subagent/ghost_adapter.py` | Sting | The whole worker-side path: Ghost lookup, replay or explore, DOM→UI-TARS continuation on the same session, independent validation, candidate save. ARGUS calls `Worker.run` with `GHOST_API_URL` set and lets this code decide. Unchanged. |
| `ghostapi/api/`, `ghostapi/client.py` | Sting | The registry. Unchanged. Runs as a separate process. |
| `argus/api/app.py`, `service.py`, `frontend/dist/` | Sting | Mission Control transport and UI. Kept; gains a `connected` runtime, a clarification route and evidence serving. |
| `argus/demo_runtime.py` | Sting | Offline `controlled` mode. Unchanged. |
| `argus/live_runtime.py` `LiveGhostBridge.match` and `.validate` | Sting | Lifted into `argus/adapters/ghost_bridge.py` (lookup preview, binding checks). |
| `argus/live_runtime.py` `_failed_report` | Sting | Lifted into `argus/adapters/worker_contracts.py` as the exception-to-report path. |
| `argus/demo_runtime.py` `DemoModerator.synthesize` | Sting | Deterministic selection baseline inside the adapted moderator (cheapest per category, day filter). |
| `argus/fakes.py` `StubModerator` | Abel | Deterministic assess/reconcile/synthesize rules the adapted moderator starts from. |
| `moderator/moderator.py` `_monitor_trace` (120 s stall), budget kill, `_aggregate` | Thomas | Adapted into the module's new `observe_progress`, `assess_report` and `reconcile`. |
| `Agents/visual` `UITarsSubagent.run(session_ref=...)`, `worker_report.py` | Thomas | Already used by the DOM worker's handoff; also the basis for a direct visual toolbox later. |
| `Agents/browser_worker/demo/run.py` `catalog_server` | Tianqi | Serves the controlled catalog locally for tonight's run. |
| `Agents/browser_worker/browser/adapter.py` Steel create/attach/release | Tianqi | Copied into the optional ARGUS session manager if it is built. |

Thrown away: the three site scraping functions in `live_runtime.LiveSteelToolbox`
and the keyword router in the connected path. `live_runtime.py` is deleted once
INT-2 passes; until then the UI labels it `scrape (deprecated)`.

## Decisions (Abel, 2026-09-13; teammates challenge tomorrow)

1. **Memory is worker-resolved.** ARGUS stage 4 returns `explore` with a lookup
   preview; the worker's `GhostWorkflow` makes the real decision and saves the
   candidate. ARGUS never writes to Ghost; stage 11 stores the reference the worker
   reported in its `ghost` block.
2. **Validation is the worker's verifier plus ARGUS binding checks.** Ghost has no
   validate endpoint. `passed` only when every record cites an observation from this
   run and subtask, the site matches, and the worker's check list is all passed.
   Otherwise `failed` or `inconclusive`. No fixture catalog is consulted.
3. **Models.** Worker: `OPENAI_MODEL=gpt-5.4`, `OPENAI_REASONING_EFFORT=medium`
   (its allowlist). ARGUS interpreter, planner and moderator: `ARGUS_MODEL=gpt-5.6-sol`.
   Both set in `.env` tonight. The adapted moderator reads `ARGUS_MODEL`.
4. **Sessions are worker-owned for INT-2.** `SessionSpec(ownership="worker")`;
   ARGUS's `open_session` returns a virtual handle and `close_session` is a no-op.
   This keeps local Chromium usable (local mode refuses ARGUS-owned sessions) and
   loses nothing tonight, because the DOM→visual continuation lives inside the
   worker's own session handling. `visual_fallback_available=False` until a UI-TARS
   endpoint is configured. The ARGUS Steel session manager (P3-SESSION) is deferred;
   it is needed only for the controller's own observe/verify step.
5. **Ports.** The local catalog server and the Ghost API both default to 8765. Ghost
   runs on 8766 tonight (`GHOST_API_URL=http://127.0.0.1:8766`); the catalog keeps
   8765 so `sites.json` stays valid.
6. **Steel run.** Requires the catalog at a reachable URL. Fastest option: publish
   `Agents/browser_worker/demo/catalog.html` as a static page (GitHub Pages on this
   repository, or any static host) and point a copy of `sites.json` at it through
   `WORKER_SITES_FILE`. Done tonight if hosting takes under 30 minutes, otherwise
   tomorrow morning before the demo.
7. **Moderator.** `moderator/moderator.py` becomes an implementation of
   `argus.interfaces.Moderator` and `ProgressObserver`. Its subprocess dispatch and
   event emission are removed from the ARGUS path; the old harness entry point is
   kept behind `python -m moderator --harness` for Thomas's standalone demo, or
   deleted if it cannot be kept working. Thomas reviews tomorrow.
8. **First request is registry-tier.** `demo-catalog` / `search_products` mapped to
   `search_extract`. Public sites need `SiteConfig` entries and worker operation
   expansion; they are after INT-2, not part of it.
9. **Storage.** ARGUS JSON runs, Ghost SQLite. Recorded in DECISIONS as the exception.

## Packages, in execution order for tonight

Estimated hours are for one person who knows both sides. Run the full ARGUS suite
and Ruff after each package.

### 1. P3-CONTRACTS — request/report translation (1.5 h)

- Files: `argus/adapters/__init__.py`, `argus/adapters/worker_contracts.py`,
  `argus/tests/test_worker_contracts.py`.
- `to_subtask_request(subtask_input, sites) -> SubtaskRequest`: objective from
  `goal` or operation plus parameters; operation table `search_products →
  search_extract`, anything else raises `PRECONDITION_FAILED` before any browser
  work; `site_id`, allowlists and patterns from `load_sites()`; registry success
  conditions translated where a typed `SuccessCondition` exists, the rest recorded as
  a limitation; budgets ceiled and clamped to the worker's ranges; session per
  decision 4; `visual_fallback_available` from settings; `required_evidence` all three.
- `to_worker_report(report: SubtaskReport, subtask_input) -> (WorkerReport, context)`:
  `worker="dom"`, `worker_model=reasoning_backend`, findings = `record.data +
  source_observation_id + retrieved_at`, actions from `action_trace`, evidence
  `{screenshots: evidence_refs, observations: [...], final_url}`, metrics
  `browser_action_count/model_call_count/elapsed_ms`. Typed failure table:
  `BUDGET_EXHAUSTED→BUDGET_EXCEEDED`, `ACTION_REJECTED→ACTION_CLASS_NOT_ALLOWED`,
  `MODEL_TIMEOUT/MODEL_ERROR/NO_PROGRESS/STALE_OBSERVATION→EXTRACTION_FAILED`,
  `AUTH_REQUIRED`, `CANCELLED` pass through. Outcomes: `needs_visual` (after the
  worker's own continuation) → `failed` + `TARGET_NOT_FOUND`; `inconclusive` →
  `failed` + `VALIDATION_FAILED` retryable; `cancelled` → `failed` + `CANCELLED`.
  `context = {"validation": report.validation, "ghost": report.ghost, "limitations":
  ...}` is returned separately and never placed in the ARGUS report.
- `failed_report(subtask_input, error_type)` lifted from `live_runtime`.
- Tests build real `SubtaskReport` objects from the worker's schemas: success,
  `needs_visual`, `inconclusive`, `BUDGET_EXHAUSTED`, cancelled, unknown operation,
  no worker validation in the ARGUS report, `WorkerReport.from_dict` round trip.

### 2. P3-DOM — toolbox facade over the worker (1.5 h)

- Files: `argus/adapters/dom_toolbox.py`, `argus/tests/test_dom_toolbox.py`.
- `DomToolbox(worker_factory=Worker, sites=None)` implementing `Toolbox`. One
  long-lived asyncio loop in a daemon thread; `run_subtask` submits
  `worker.run(request, cancel=event)` with `run_coroutine_threadsafe`, waits with the
  subtask's remaining seconds, sets the cancel event on controller stop, converts
  with P3-CONTRACTS and keeps `context` in a dict keyed by `(run_id, subtask_id)` for
  the Ghost bridge. `open_session`/`close_session` per decision 4. `observe`,
  `dom_interpret`, `vision_interpret` raise `PRECONDITION_FAILED` with a message
  naming P3-SESSION.
- Tests with a fake worker: success, cancellation sets the event, timeout produces
  `BUDGET_EXCEEDED`, exceptions become a failed report without leaking text, context
  retrievable.

### 3. P3-GHOST — bridge (1 h)

- Files: `argus/adapters/ghost_bridge.py`, `argus/tests/test_ghost_bridge.py`,
  one-line change in `argus/controller.py` `_stage_validate` to pass
  `report_context` for registry subtasks as well, with a test.
- `GhostBridge(toolbox: DomToolbox, lookup=None)`: `match` runs Sting's lookup
  (in-process `WorkflowStore` + `service.lookup`, as `LiveGhostBridge.match` does)
  and returns `explore` with `reason="worker-resolved: <preview>"`; `validate` does
  Sting's binding checks plus the worker check list from the toolbox context;
  `compile` returns the reference from the context's `ghost` block or `None`. A test
  greps the module for `create_candidate` and `submit_qualification` and fails if
  either appears.

### 4. P3-MOD — adapt Thomas's module (2 h)

- Files: `moderator/moderator.py` (rewrite), `moderator/__main__.py`,
  `moderator/README.md`, `argus/tests/test_moderator_module.py`.
- `class Moderator` implements `argus.interfaces.Moderator` and `ProgressObserver`:
  - `assess_report`: `StubModerator` rules first (failed → fail with the typed
    failure; `TARGET_NOT_FOUND`/`EXTRACTION_FAILED` on first attempt →
    `retry_other_path`; succeeded without evidence → `verify`). A succeeded report
    with evidence gets one bounded call to `ARGUS_MODEL` through
    `argus.model_client.OpenAICompatibleClient.parse_json` with a strict output
    `{decision, reason, unmet_conditions, verification_question}`; invalid or refused
    output degrades to `accept`-with-note only when the toolbox cannot verify, else
    `verify`. Tonight the toolbox cannot verify (decision 4), so degrade to `fail`
    with `VALIDATION_FAILED` retryable rather than accept blindly.
  - `reconcile`: Thomas's `_aggregate` merge by URL, every dropped record named in
    `gaps`; a model call only on a field conflict for a shared URL.
  - `synthesize`: `DemoModerator`/`StubModerator` deterministic filter/rank/limit;
    returns `AnswerSelection` only. Optional model call may reorder or choose fields.
  - `observe_progress`: Thomas's 120 s stall → `flag`; elapsed over budget →
    `stop_subtask`.
  - Every decision string from `contracts.MODERATOR_DECISIONS`. No prose leaves the
    module; the old `_synthesize` prompt is dropped or rewritten to emit the
    selection JSON.
- The harness (`_run_worker`, subprocess tailing, `run()`) moves behind
  `python -m moderator --harness` with its worker path fixed to `Agents/visual`, or
  is removed if that takes more than 20 minutes. README states the new role.
- Tests with `FakeModelClient`: every branch, refusal degrade, reconcile with and
  without conflict, criteria application, fallback note, observer flag/stop/continue,
  `isinstance` for both protocols, and a full `Controller` run on the registry
  fixture and the salary chain fixture asserting the `StubModerator` outcomes.

### 5. P3-RUNTIME — connected mode in Mission Control (1.5 h)

- Files: `argus/runtime.py`, `argus/api/service.py`, `argus/api/app.py`,
  `frontend/dist/app.js`, `index.html`, `argus/README.md`, `frontend/README.md`.
- `ARGUS_RUNTIME=connected` composes `DomToolbox`, `GhostBridge`, `moderator.Moderator`,
  `JsonStore`, real `argus.interpreter.interpret` and `planner.plan`. Startup refuses
  without `OPENAI_API_KEY`, `ARGUS_MODEL`, `GHOST_API_URL` (health-checked) and a
  loadable site config, naming what is missing; `STEEL_API_KEY` only when
  `WORKER_BROWSER=steel`. `/api/health` reports the mode and the checks.
- `submit` passes raw text to `Controller.run`. `needs_input` runs show the gate's
  question; `POST /api/runs/{id}/clarifications` starts the follow-up request with
  the original text plus the answer.
- Sidebar label `Connected runtime`; `live` renamed `scrape (deprecated)`.
- Evidence: `GET /api/runs/{id}/evidence/{observation_id}` serves only files the
  store registered for that run; the result panel links records to their images
  when present (the worker's evidence refs are observation ids; image files arrive
  with P3-SESSION or a worker change, so this is a link when available, not a
  requirement tonight).
- Tests through the FastAPI client with fakes for the toolbox and model client;
  startup refusal messages; health payload; evidence route rejects paths outside
  the run.

### 6. INT-2 — first connected run, local Chromium (0.5 h plus debugging)

- Start: Ghost API on 8766; catalog server on 8765 (`python -m
  Agents.browser_worker.demo.run --serve-only` or the equivalent flag; add one if it
  does not exist); Mission Control in `connected` mode with `WORKER_BROWSER=local`,
  `WORKER_BROWSER_EXECUTABLE` set, `GHOST_API_URL=http://127.0.0.1:8766`.
- Request: "Find headphones under 150 USD and show the five cheapest with their
  prices and sources." Then the two-category request (headphones and keyboards).
- Record in TEAM: run id, revision, status, validation status, record count, browser
  actions, model calls per scope (worker, interpreter and planner, moderator),
  elapsed, the Ghost candidate id from `GET /v1/workflows`, and every failure
  observed. Failures are recorded as failures; no silent retries.
- Acceptance: evaluation items 1, 2, 7; scenarios S1, S2; S6 through a test with a
  wrong record from the fake worker.

### 7. INT-2 on Steel (0.5 h if hosting is quick)

- Host the catalog (decision 6), `WORKER_SITES_FILE` pointing at the hosted copy,
  `WORKER_BROWSER=steel`. Same request, same records to TEAM, plus session count and
  confirmation the worker released its session.

### 8. INT-3 first slice — qualify and reuse (1.5 h, tonight if time allows)

- `POST /api/workflows/{skill_id}/versions/{version}/qualifications` runs
  `GhostWorkflow.qualify` with three explicit changed-input cases as a separate job
  with its own events. Then the same request with a different price limit: the
  worker's lookup returns `reuse`, the run shows zero worker model calls and fresh
  records. Scenarios S3, S4, S5.

### Deferred to tomorrow

- P3-SESSION (ARGUS Steel session manager, ARGUS-owned sessions, `observe` and the
  controller's verify step). Only needed for the moderator's `verify` path and for
  ARGUS-side screenshots.
- P3-EVENTS (worker `on_status` forwarded into the run's event stream while the
  worker runs).
- Deleting `live_runtime.py`; public-site `SiteConfig` entries; controlled site v2
  and repair; restoring anything from the replaced dashboard prototype.

## Tomorrow's verification list for the team

- **Tianqi:** review `worker_contracts.py` against `schemas.py`; confirm the operation
  and failure tables; run one live Steel `search_extract` through Mission Control and
  sign the HTML-1 receiver check. Decide about the replaced dashboard prototype.
- **Sting:** confirm decisions 1, 2 and 9; check the candidate ARGUS references in
  `GET /v1/workflows`; run the qualification job and sign GHOST-1's receiver check.
- **Thomas:** review the rewritten `moderator/`; decide whether the harness entry
  point stays; configure `UITARS_BASE_URL` and run one `needs_visual` continuation on
  the lent session; sign VLM-1 and MOD-1.
- **All:** run the demo script from the evaluation checklist twice from a clean start
  and record the backup.

## Explicitly not in ARGUS-3

Public-site execution through the real worker (needs `SiteConfig` entries and the
worker operation expansion in END-TO-END-PLAN step 6); the two-version controlled site,
quarantine and repair (S8, S9); authenticated actions, purchases, submissions.

## Rules

- Offline: `python3 -m unittest discover -s argus/tests` and Ruff stay green after
  every package; CI blocks on both.
- Live: every live command is recorded in TEAM with revision, run id and observed
  output. No live claim without a run id.
- No connection is marked Integrated until its receiver has verified the exact
  revision tomorrow.
