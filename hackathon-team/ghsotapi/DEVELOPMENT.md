# Ghost API development guide

Updated: 2026-09-12.

## Objective

Build the reusable workflow layer that ARGUS calls for each task:

```text
Task → validate → find compatible qualified workflow
  Match → bind current inputs → replay → verify → record result
  No match → discover → verify → compile → save candidate → qualify
```

Ghost stores procedures rather than cached answers. Every reuse must execute the procedure again and return current results with evidence. A successful discovery can complete the current task before the new workflow is qualified for future reuse.

This guide incorporates the repository's team plan, interface contract, evaluation checklist, four workstream logs, workflow design, and demo documentation. It describes the actual demo separately from the planned live implementation.

## Current implementation

| Area | Available now | Still required for live use |
| --- | --- | --- |
| Task input | Terminal prompts, JSON task files, and a local web form with progress endpoints | ARGUS integration and shared-contract HTTP run endpoints |
| Matching | Exact site/operation lookup; qualified state and fixed schema/action compatibility | General schema/scope checks and live preconditions |
| Storage | Local SQLite identities, versions, and run records | Shared repository interface, migrations, stronger integrity and transactional boundaries |
| Discovery | Adapter returns a predefined fixture procedure | Browser agent exploration and real normalized action traces |
| Compilation | Fixture procedure already contains parameter references | Trace compiler that proves input origins and rejects ambiguous/incomplete traces |
| Replay | Ordered actions interpreted against an in-memory catalog | Browser adapter with semantic target resolution, expected-state checks, and fresh sessions |
| Validation | Fixture result set, input application, duplicates, price/currency, empty state | Complete result schema, current observation provenance, source checks, and inconclusive outcomes |
| Qualification | Three isolated fixture executions, persisted reports | Fresh browser sessions and coverage chosen relative to the discovery inputs |
| Recovery | Unsupported task fails explicitly | Typed browser failures, bounded repair/fallback, quarantine, cancellation, cleanup |

There is no live Steel connection, automatic website learning, integrated ARGUS dashboard, repair agent, or deployed API. A local web viewer now shows real fixture execution events while work proceeds. The local demo is single-process and read-only. Its database is a prototype of the storage boundary that C will own in the integrated system.

## Repository layout

```text
ghsotapi/
  README.md                 Documentation index and move map
  DEVELOPMENT.md            This guide
  WORKFLOW-STRUCTURE.md     Workflow architecture
  CONTRACTS.md              Ghost schema and function contract
  B-ghost.md                Ghost workstream log
  demo/
    README.md               Demo walkthrough
    ghost_demo.py           Runnable Python prototype and event callbacks
    live_demo.py            Local HTTP viewer and background execution queue
    live.html               Interactive flowchart and fixture observation view
    test_live_demo.py       Concurrent observation and queue checks
    flowchart.js            Reusable pointer-drag behavior
    test_flowchart.js       Pointer interaction tests
    task.json               Example task
    test_ghost_demo.py      Fixture lifecycle tests
    .gitignore              Excludes local database and Python cache
```

`demo/ghost.sqlite3` is created at runtime and stays beside the script, regardless of the shell's current directory. The folder move preserves an existing database if one was present. `--db` selects a different database file; its parent directory must already exist.

## Setup and local development

Python 3.9 or later is sufficient for the current demo. It uses only the standard library; no packages, credentials, or external services are required. Run these commands from the repository root:

```sh
python3 ghsotapi/demo/live_demo.py
# Open http://127.0.0.1:8765 for live progress.
python3 ghsotapi/demo/ghost_demo.py
python3 ghsotapi/demo/ghost_demo.py --task ghsotapi/demo/task.json
python3 ghsotapi/demo/ghost_demo.py --list
python3 -m unittest discover -s ghsotapi/demo -p 'test_*.py' -v
```

For an independent registry, pass a new filename:

```sh
python3 ghsotapi/demo/ghost_demo.py --db /tmp/ghost-development.sqlite3
```

To observe candidate-only storage, add `--skip-qualification`. Another automatic request will not select that candidate. `mode: "explore"` in a JSON task forces discovery even when a qualified workflow exists.

## Live workflow viewer

Run `python3 ghsotapi/demo/live_demo.py` from the repository root, then open `http://127.0.0.1:8765`. Use `--port` or `--db` to select a different port or database. Stop with Ctrl+C.

The viewer shows an interactive flowchart with matching/discovery branches, each executed action, bound catalog fields, extracted records, validation checks, candidate staging, qualification cases, and the final committed result. Users can pan the chart by dragging its empty canvas with a mouse, pen, or touch pointer. Starting a manual pan disables automatic following until `Follow live` is selected. Node clicks remain reserved for inspecting node details. These observations come from optional callbacks in `run_task`, `replay`, and `qualify`; the UI does not fabricate progress with a separate animation script.

`LiveRuns` executes tasks on one background worker. The HTTP server handles progress reads concurrently, and additional tasks queue in order to preserve the prototype's single-writer database behavior. This supports watching work as it happens; parallel browser-agent execution is still future work. A 350 ms presentation delay makes events visible and must not be used as a latency benchmark.

Local demo endpoints are `POST /api/live/runs`, `GET /api/live/runs`, `GET /api/live/runs/{job_id}`, and `GET /api/live/workflows`. They are a prototype viewer API, not implementations of the shared `/api/runs` contract. A live job ID identifies the queue entry; its final result includes the persisted execution run ID. The page polls every 350 ms and exposes connection failures. Nodes are clickable and keyboard accessible; the inspector shows their role or recorded event evidence. Zoom controls adjust the graph, while Step buttons and a history slider reconstruct the graph and catalog observation at a selected event. Follow live returns to the newest event. Inspecting history does not pause server execution. Queue state and live events are held in memory until shutdown; workflow definitions and successful execution records remain in SQLite.

The candidate event says **staged** because the current transaction commits after qualification. Production task completion and qualification still need separate persistence boundaries. Viewer exceptions become failed live jobs, but not every exception currently produces a durable failed-run record. The local history is capped at 200 submissions per server session.

## Code entry points

The core prototype functions are in [ghost_demo.py](demo/ghost_demo.py). The web server and queue are in [live_demo.py](demo/live_demo.py).

| Function | Current responsibility |
| --- | --- |
| `main` | Parse CLI options, run an interactive loop or execute one JSON task |
| `connect` | Open SQLite, create prototype tables and lookup index |
| `validate_request` | Enforce exact task fields, supported input names, types, and price bounds |
| `run_task` | Coordinate lookup, fixture discovery/replay, validation, persistence, and optional qualification |
| `simulated_discovery` | Return the known catalog procedure; this is the replacement point for real exploration/compilation |
| `replay` | Bind parameters and interpret fixture steps with local execution state |
| `verify` | Compare records and applied inputs against fixture ground truth |
| `qualify` | Execute three fixed fixture cases, persist reports, and update candidate status |
| `list_workflows` | Display workflow IDs, versions, and qualification status |

`run_task` temporarily combines responsibilities so the demo can run alone. In the integrated system, ARGUS owns the orchestration loop, Ghost supplies callable workflow operations, and the browser layer executes actions. Do not create a second Ghost run controller alongside ARGUS.

## Task contract and matching

Use the [shared TaskRequest](../CONTRACTS.md#request-and-execution-boundary). The current fixture accepts `demo-catalog / search_products`, with `query` and `max_price`. Currency is CAD. Unknown filters are rejected; unsupported sites or operations return `UNSUPPORTED_DISCOVERY` in this demo.

For live matching:

1. Validate the task against the configured operation before querying workflows.
2. Retrieve exact site/operation candidates with qualified status.
3. Check every input and constraint, output coverage, supported scope, and permitted actions.
4. Rank only compatible versions and return the chosen ID/version with a reason.
5. Bind inputs without modifying the stored definition.
6. Check live preconditions before executing steps.

The demo orders candidates by version creation time, then version number. Performance-aware ranking is planned, not implemented. Semantic similarity is optional later; it must never override hard compatibility. Partial matches trigger exploration for the MVP. Missing task information requires correction, rather than inventing values during discovery.

## Storage and versioning

The actual prototype schema is defined in `connect`:

| Table | Current columns |
| --- | --- |
| `workflows` | `skill_id` primary key, `site_id`, `operation`, `description` |
| `workflow_versions` | Composite primary key `skill_id/version`, `status`, JSON `definition`, JSON `qualification`, `created_at` |
| `workflow_runs` | `run_id` primary key, `request_id`, `mode`, JSON `request`, JSON `result`, `created_at` |

Current runs contain workflow references inside their result JSON. The intended storage design adds explicit relational references and other metadata described in [Workflow structure](WORKFLOW-STRUCTURE.md#3-how-workflows-are-saved). Do not assume that proposed columns already exist.

For the integrated implementation, C provides storage operations to load compatible versions, append a candidate, record executions, and update qualification/quarantine state. Keep procedure definitions immutable; repairs create new candidate versions. Persist source run IDs and qualification evidence. Enforce valid workflow/run references and atomic version allocation before concurrent execution is enabled.

Define duplicate-request behavior before adding HTTP retries. The current CLI executes a new run even if `request_id` repeats. Qualification cases currently use a simplified request payload in run history; normalize those records to the shared contract during integration.

Store only evidence needed for verification. Large traces and screenshots should use artifact references; credentials, cookies, and active browser session handles do not belong in reusable definitions.

## Creating and qualifying a workflow

The live sequence must preserve the distinction between completing a task and learning a reusable procedure:

1. ARGUS requests exploration through the browser adapter when Ghost returns no match.
2. The browser agent records actions, observations, outcomes, and parameter origins.
3. Ghost validates the task results against checks defined independently of the trace.
4. Compile supported actions into a complete procedure with named bindings and expected states. Reject missing evidence and ambiguous mappings.
5. Persist a candidate linked to the successful source run. Return the verified task result even if compilation fails.
6. Run qualification separately in fresh sessions, with changed inputs and an evidenced empty-result case.
7. Promote only when all required checks pass; otherwise retain the candidate with the failure or missing coverage.

The fixture currently performs qualification synchronously inside `run_task` and uses fixed keyboard, mouse, and empty-result inputs. These cases are not guaranteed to differ from every possible discovery input. Move qualification behind its own execution/report boundary and choose its cases from the declared input scope before claiming the live contract is satisfied.

## Development sequence

| Step | Work | Acceptance criterion |
| --- | --- | --- |
| 1. Freeze executable types | C and B implement shared request, workflow, result, evidence, and error types | One authoritative serialized contract is consumed by ARGUS, Ghost, and browser adapters |
| 2. Separate Ghost functions | Extract matching, binding, compilation, validation, qualification, and repair interfaces; C supplies storage | Existing fixture lifecycle passes through these interfaces without a duplicate controller |
| 3. Integrate real trace capture | A supplies `explore`, normalized ActionRecords, observations, and value origins | A successful real task has sufficient evidence to reconstruct every required step |
| 4. Implement compilation | B replaces the predefined procedure with trace compilation | A real trace creates a candidate; incomplete traces and ambiguous bindings are rejected |
| 5. Integrate browser replay | A executes bound steps with current semantic targets, preconditions, and expected states | Changed values visibly take effect in fresh sessions and current results pass checks |
| 6. Qualify and expose state | ARGUS schedules qualification; C persists reports and exposes existing run/skill routes | Candidate is excluded from matching until required replays pass; task and qualification states are distinct |
| 7. Add bounded recovery | A reports typed failures; B proposes/validates repair; C controls budgets | Supported target change produces a new tested version; unsupported changes stop or fall back explicitly |
| 8. Evaluate live behavior | Run shared scenarios and record all relevant costs | Claims and dashboard state are backed by actual run evidence |

Suggested future Ghost modules under this folder are `matcher.py`, `binder.py`, `compiler.py`, `validator.py`, `qualification.py`, and `repair.py`, plus `tests/`. These files are proposals, not existing modules. The current `demo/` remains a runnable fixture while integrations are built.

## Recovery and known gaps

Follow the shared MVP limit of one repair attempt and at most one bounded exploration fallback when appropriate. Authentication and unsupported action changes need an actionable stop/handoff. Never weaken the validator to make a repair pass.

Before live execution, add typed exception handling, persistent failed-run outcomes, finite budgets, cancellation, and guaranteed browser cleanup. The prototype assumes valid fixture definitions; malformed saved JSON or unsupported steps can currently raise exceptions. It does not implement quarantine or transactional isolation between a successful task result and qualification failure.

General validation must check schema completeness, configured-domain URLs, observation IDs, retrieval timestamps, numeric values, currency, applied filters, and an explicit completion/empty state. Current fixture records and validation reports use simplified shapes and are not full shared-contract implementations. On public sites without ground truth, mark completeness as unverified and use `inconclusive` when evidence is insufficient.

## Validation

The Python tests in [test_ghost_demo.py](demo/test_ghost_demo.py) and [test_live_demo.py](demo/test_live_demo.py) cover:

- Discovery, persisted qualification, reopening the database, changed-input reuse, and empty results.
- Exclusion of candidate workflows from automatic reuse.
- Rejection of unknown filters, invalid prices, and nonfinite numbers.
- Explicit failure for unsupported sites without saving a workflow.
- Detection of missing expected records and stale input evidence.

The JavaScript tests in [test_flowchart.js](demo/test_flowchart.js) cover drag-to-pan movement, control and mouse-button exclusions, pointer identity, cancellation, lost capture, and teardown. The tests exercise the same `flowchart.js` module loaded by the demo page.

Run all tests and create Codecov-compatible coverage reports with the commands in the [demo README](demo/README.md). CI uploads Python and JavaScript reports under separate `ghost-python` and `ghost-flowchart` flags. The Python report enforces a 70% local floor; Codecov applies project and patch status checks across uploaded reports.

All twelve Python tests and five flowchart tests passed when this coverage integration was added. This establishes local fixture behavior only.

The shared [S1–S10 evaluation scenarios](../EVALUATION.md#fixed-scenarios) remain the live acceptance criteria. Real discovery, trace compilation, browser qualification, live reuse, UI-change repair, and dashboard integration have not been verified by the fixture tests.

## Ownership and shared documents

Ghost-specific documents, demo code, and future Ghost modules live in `ghsotapi/`. B owns Ghost semantics. A owns browser mechanics. C owns ARGUS orchestration, shared contracts, database integration, and API delivery. D owns the dashboard, controlled site, and release evaluation.

The root [contract](../CONTRACTS.md) links to the [Ghost schema/interface](CONTRACTS.md), while preserving request, browser, result, and event definitions for all consumers. The root [plan](../PLAN.md) and [evaluation](../EVALUATION.md) retain cross-team coordination. The [Person B log](B-ghost.md) preserves the original live workstream checklist; report actual integration progress there as it occurs.
