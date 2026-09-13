# Ghost API development guide

For the implemented DOM-first worker connection, use [INTEGRATION.md](INTEGRATION.md).
It documents `0.2` messages, the shared client, semantic replay, visual handoff,
qualification, the connected local Chrome demo and current validation limits. The
`0.1` service examples below remain compatible historical examples.

Updated: 2026-09-12.

## Objective

Provide a reusable workflow-memory backbone that each Steel-powered subagent calls
directly:

```text
Subagent task → Ghost lookup
  Match → Ghost binds current inputs → subagent replays with Steel → report run
  No match → subagent explores with Steel → submit trace → Ghost saves candidate
  Candidate → fresh Steel replay reports → Ghost qualifies for later lookup
```

Ghost stores procedures rather than cached answers. Every reuse must execute the procedure again and return current results with evidence. A successful discovery can complete the current task before the new workflow is qualified for future reuse.

Task assignment and browser execution are deliberately outside this module. Ghost
does not create Steel sessions, drive pages, or coordinate a task graph.

This guide incorporates the repository's team plan, interface contract, evaluation checklist, four workstream logs, workflow design, and demo documentation. It describes the actual demo separately from the planned live implementation.

## Current implementation

| Area | Available now | Still required for live use |
| --- | --- | --- |
| Agent API | FastAPI endpoints for lookup, candidate submission, run reports, qualification, registry, activity, and health | Authentication, request idempotency, deployment configuration |
| Graph | `/graph` is served by the FastAPI process and polls registry/activity state | Per-attempt step streaming and richer evidence inspection |
| Matching | Exact site/operation lookup; qualified state, input type/range, output, and action compatibility | Live precondition results and ranking by current execution history |
| Storage | SQLite workflow identities, immutable version definitions, qualification reports, run reports, and activity | Formal migrations, artifact storage, retention, stronger transactional boundaries |
| Discovery | Agents explore independently with Steel and submit traces | First real agent connection check |
| Compilation | Successful action traces become parameterized candidates; mismatched parameter origins are rejected | More complete trace/evidence and operation-specific compiler checks |
| Replay | Ordered actions interpreted against an in-memory catalog | Browser adapter with semantic target resolution, expected-state checks, and fresh sessions |
| Validation | Qualification accepts external validation reports; candidates cannot be reused | Independent operation-specific validators and current observation provenance |
| Qualification | Requires three distinct passed parameter sets and an evidenced empty-result report | Verify those reports come from fresh Steel sessions |
| Recovery | Unsupported task fails explicitly | Typed browser failures, bounded repair/fallback, quarantine, cancellation, cleanup |

There is no live Steel connection, independent live-site validator, repair agent,
authentication, or deployed service yet. The API never executes a browser action; it
returns bound steps and stores reports supplied by agents.

## Repository layout

```text
ghostapi/
  README.md                 Documentation index and move map
  DEVELOPMENT.md            This guide
  WORKFLOW-STRUCTURE.md     Workflow architecture
  CONTRACTS.md              Ghost schema and function contract
  B-ghost.md                Ghost workstream log
  requirements.txt         Runtime dependencies
  api/
    app.py                  FastAPI application and routes
    models.py               Strict agent request models
    service.py              Matching, binding, and trace compilation
    storage.py              SQLite registry and activity persistence
    graph.html              Live workflow lifecycle graph
    __main__.py             `python -m ghostapi.api` entry point
  tests/
    test_api.py             Agent-facing lifecycle and graph tests
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

## FastAPI setup and local development

Python 3.10 or later is required for the FastAPI service. Run from the repository
root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r ghostapi/requirements-dev.txt
python -m ghostapi.api
```

The process binds to `127.0.0.1:8765`. Open `/graph` for the live registry graph and
`/docs` for FastAPI's interactive OpenAPI client. The only environment variable is:

| Name | Required | Purpose |
| --- | --- | --- |
| `GHOST_DATABASE_PATH` | No | SQLite path; defaults to `ghostapi/ghostapi.sqlite3` |

No Steel or model API key belongs in Ghost. Those stay in each browser agent.

### Agent lifecycle endpoints

| Method and path | Input | Result |
| --- | --- | --- |
| `POST /v1/workflows/lookup` | Task scope, parameters, outputs, allowed actions | `reuse` with bound steps or `explore` with `NO_MATCH` |
| `POST /v1/workflows/candidates` | Successful Steel action trace and evidence refs | Immutable candidate identity/version |
| `POST /v1/workflows/{id}/versions/{version}/runs` | Result, evidence, metrics, and failure data | Persisted run ID |
| `POST /v1/workflows/{id}/versions/{version}/qualification` | Three or more replay validation reports | `qualified` or retained `candidate` |
| `GET /v1/workflows` | None | Workflow/version summaries |
| `GET /v1/workflows/{id}/versions/{version}` | None | Definition and qualification evidence |
| `GET /v1/activity` | Optional `limit` | Events displayed by `/graph` |

Lookup example:

```sh
curl -s http://127.0.0.1:8765/v1/workflows/lookup \
  -H 'content-type: application/json' \
  -d '{
    "schema_version":"0.1",
    "task_id":"task-1",
    "agent_id":"steel-agent-1",
    "site_id":"shop.example",
    "operation":"search_products",
    "parameters":{"query":"headphones"},
    "required_outputs":["title","url"],
    "allowed_actions":["navigate","fill","click","extract"]
  }'
```

No match is a successful lookup response, not an HTTP error:

```json
{
  "schema_version": "0.1",
  "decision": "explore",
  "reason": {
    "code": "NO_MATCH",
    "message": "No qualified workflow is compatible with this task.",
    "retryable": false
  }
}
```

Malformed payloads and unknown fields return FastAPI `422` validation responses.
Unknown workflow versions return `404` with `WORKFLOW_NOT_FOUND`. A candidate stays
out of lookup results until qualification receives at least three distinct passed
parameter cases, including one evidenced empty result.

### Run tests

```sh
python -m unittest discover -s ghostapi/tests -p 'test_*.py' -v
python -m unittest discover -s ghostapi/demo -p 'test_*.py' -v
node --test ghostapi/demo/test_flowchart.js
```

Runtime dependencies are FastAPI, Pydantic (through FastAPI), and Uvicorn. Developer
dependencies add HTTPX and Coverage. SQLite comes from Python's standard library.

## Simulated demo setup

Python 3.9 or later is sufficient for the older simulated demo. It uses only the standard library; no packages, credentials, or external services are required. Run these commands from the repository root:

```sh
python3 ghostapi/demo/live_demo.py
# Open http://127.0.0.1:8765 for live progress.
python3 ghostapi/demo/ghost_demo.py
python3 ghostapi/demo/ghost_demo.py --task ghostapi/demo/task.json
python3 ghostapi/demo/ghost_demo.py --list
python3 -m unittest discover -s ghostapi/demo -p 'test_*.py' -v
```

For an independent registry, pass a new filename:

```sh
python3 ghostapi/demo/ghost_demo.py --db /tmp/ghost-development.sqlite3
```

To observe candidate-only storage, add `--skip-qualification`. Another automatic request will not select that candidate. `mode: "explore"` in a JSON task forces discovery even when a qualified workflow exists.

## Live workflow viewer

Run `python3 ghostapi/demo/live_demo.py` from the repository root, then open `http://127.0.0.1:8765`. Use `--port` or `--db` to select a different port or database. Stop with Ctrl+C.

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

`run_task` temporarily combines responsibilities so the old demo can run alone. In
the live service, every subagent calls Ghost directly and executes returned steps in
its own Steel session. Ghost records workflow attempts but does not become a browser
or task controller.

## Task contract and matching

Use the [shared TaskRequest](../docs/hackathon/CONTRACTS.md#request-and-execution-boundary). The current fixture accepts `demo-catalog / search_products`, with `query` and `max_price`. Currency is CAD. Unknown filters are rejected; unsupported sites or operations return `UNSUPPORTED_DISCOVERY` in this demo.

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

1. A subagent explores with Steel when Ghost returns no match.
2. The subagent records actions, observations, outcomes, and parameter origins.
3. Ghost validates the task results against checks defined independently of the trace.
4. Compile supported actions into a complete procedure with named bindings and expected states. Reject missing evidence and ambiguous mappings.
5. Persist a candidate linked to the successful source run. Return the verified task result even if compilation fails.
6. Run qualification separately in fresh sessions, with changed inputs and an evidenced empty-result case.
7. Promote only when all required checks pass; otherwise retain the candidate with the failure or missing coverage.

The fixture currently performs qualification synchronously inside `run_task` and uses fixed keyboard, mouse, and empty-result inputs. These cases are not guaranteed to differ from every possible discovery input. Move qualification behind its own execution/report boundary and choose its cases from the declared input scope before claiming the live contract is satisfied.

## Development sequence

| Step | Work | Acceptance criterion |
| --- | --- | --- |
| 1. Stabilize executable types | Maintain request, workflow, result, evidence, and error models under `api/models.py` | One authoritative serialized contract is consumed by Ghost and every browser agent |
| 2. Complete Ghost operations | Extend matching, binding, compilation, validation, qualification, and repair services | Operations remain independent of browser/session control |
| 3. Integrate real trace capture | Connect normalized Steel ActionRecords, observations, and value origins | A successful real task has sufficient evidence to reconstruct every required step |
| 4. Strengthen compilation | Expand the initial parameter-origin compiler | A real trace creates a candidate; incomplete traces and ambiguous bindings are rejected |
| 5. Integrate browser replay | Subagents execute bound steps with current targets and expected states | Changed values visibly take effect in fresh sessions and current results pass checks |
| 6. Qualify and expose state | Subagents submit fresh replay reports; Ghost persists and evaluates them | Candidate is excluded from matching until required replays pass |
| 7. Add bounded recovery | Agents report typed failures; Ghost proposes and validates repairs | Supported target change produces a new tested version; unsupported changes stop explicitly |
| 8. Evaluate live behavior | Run shared scenarios and record all relevant costs | Claims and dashboard state are backed by actual run evidence |

The initial implementations of matching, binding, compilation, and qualification
live in `api/service.py`; split them into focused modules as they gain operation-
specific policies. The current `demo/` remains a runnable fixture while integrations
are built.

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

The shared [S1–S10 evaluation scenarios](../docs/hackathon/EVALUATION.md#fixed-scenarios) remain the live acceptance criteria. Real discovery, trace compilation, browser qualification, live reuse, UI-change repair, and dashboard integration have not been verified by the fixture tests.

## Ownership and shared documents

Ghost-specific documents, API code, storage, graph, and demo live in `ghostapi/`.
Ghost owns workflow semantics and persistence. Each subagent owns its Steel browser
mechanics and sends normalized evidence to this service. The workflow graph is part
of the Ghost API process; it is not a separate dashboard deployment.

The shared [contract](../docs/hackathon/CONTRACTS.md) links to the [Ghost schema/interface](CONTRACTS.md), while preserving request, browser, result, and event definitions for all consumers. The [team board](../docs/ai/TEAM.md) and [evaluation](../docs/hackathon/EVALUATION.md) retain cross-team coordination. The [Person B log](B-ghost.md) preserves the original live workstream checklist; report actual integration progress there as it occurs.
