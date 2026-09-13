# Ghost API

Ghost API is a shared workflow-memory service for Steel-powered agents. Agents own
their browser sessions and research; Ghost finds reusable procedures, binds current
inputs, saves successful traces as candidates, records executions, and qualifies
versions for later reuse.

## Worker connection: DOM first, visual fallback

The [connection guide](INTEGRATION.md) covers setup, messages, examples, testing and
limitations. `GHOST_API_URL` connects the existing DOM worker to Ghost. It explores or
replays through DOM first, then uses UI-TARS in the same Steel session when page content
requires visual interpretation. Validated traces become candidates; explicit fresh
qualification runs unlock reuse. A local Chrome demo is available with
`python -m Agents.browser_worker.ghost_cli --demo` while Ghost is running.

## FastAPI service and workflow graph

Requires Python 3.10 or later. From the repository root:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r ghostapi/requirements.txt
python -m ghostapi.api
```

One process serves all agent endpoints and the workflow graph:

- Workflow graph: <http://127.0.0.1:8765/graph>
- Interactive API documentation: <http://127.0.0.1:8765/docs>
- Health check: <http://127.0.0.1:8765/health>

Set `GHOST_DATABASE_PATH` to select the SQLite registry. It defaults to
`ghostapi/ghostapi.sqlite3`, which is ignored by Git. See the
[development guide](DEVELOPMENT.md) for endpoint examples, failure behavior, tests,
dependencies, and limitations.

An agent starts with `POST /v1/workflows/lookup`. A compatible qualified version
returns `{"decision":"reuse","workflow":{"bound_steps":[...]}}`; otherwise the
successful lookup response is `{"decision":"explore","reason":{"code":"NO_MATCH",...}}`.
After Steel exploration, submit the successful normalized trace to
`POST /v1/workflows/candidates`. Invalid or unknown fields return HTTP 422, and an
unknown workflow version returns HTTP 404 with `WORKFLOW_NOT_FOUND`.

Current limitations: no authentication, deployment configuration, general migration
framework, artifact retention service or quarantine/repair. Integrated `0.2` messages
store evidence snapshots and require stored qualification run references; `0.1` remains
the older fixture boundary. The worker performs independent configured-site checks;
Ghost cannot independently prove arbitrary caller results. Live Steel/model verification
of the new connection is pending.

## Simulated workflow demo

From the repository root:

```sh
python3 ghostapi/demo/live_demo.py
```

Open [the local viewer](http://127.0.0.1:8765) to watch the older simulated catalog lifecycle. Use a different port if the FastAPI service is already running. The browser/catalog are simulated; events and database writes come from the demo. Stop the server with Ctrl+C.


Each subagent asks Ghost for a compatible saved workflow and receives bound inputs
for execution. When no suitable workflow exists, that subagent discovers a procedure
with Steel and sends the successful trace to Ghost as a candidate for qualification
and future reuse.

## Read first

| Document | Purpose |
| --- | --- |
| [Development guide](DEVELOPMENT.md) | Current code, setup, storage, implementation sequence, and acceptance criteria |
| [Workflow structure](WORKFLOW-STRUCTURE.md) | Detailed design for finding, saving, using, and creating workflows |
| [Ghost contract](CONTRACTS.md) | Workflow schema, lifecycle, and function interfaces |
| [Demo instructions](demo/README.md) | Interactive and JSON task examples |
| [Person B work log](B-ghost.md) | Original Ghost workstream assignments and live integration checklist |

## Try the demo

From the repository root:

```sh
python3 ghostapi/demo/ghost_demo.py
```

Search `headphones` at `150`, then `keyboard` at `100`. On a new database, the first task creates a workflow and the next reuses it. Database persistence is real; discovery, products, and browser execution are simulated.

## Tests and coverage

Twelve legacy Python tests cover the fixture lifecycle and four FastAPI tests cover
the agent-facing lifecycle, graph, validation, and qualification. Five JavaScript
tests cover pointer panning and cleanup. See the [development guide](DEVELOPMENT.md)
for local commands.

## Shared dependencies

- [Shared contracts](../docs/hackathon/CONTRACTS.md): task requests, browser interfaces, results, API/events, errors, and recovery budgets.
- [Team tasks](../docs/ai/TEAM.md): ownership, dependencies, and integration gates.
- [Evaluation](../docs/hackathon/EVALUATION.md): live browser acceptance and demonstration criteria.
- [Team workstreams](../docs/ai/TEAM.md): Ghost, ARGUS, visual interpretation, HTML/code interpretation, and integration handoffs.

## File moves

| Previous location | Current location |
| --- | --- |
| `GHOST-WORKFLOW-STRUCTURE.md` | `ghostapi/WORKFLOW-STRUCTURE.md` |
| `people/B-ghost.md` | `ghostapi/B-ghost.md` |
| `demo/` | `ghostapi/demo/` |
| Workflow schema and Ghost interface sections in root `CONTRACTS.md` | `ghostapi/CONTRACTS.md`, with links from the shared contract |

Shared documents retain the Ghost references needed to describe cross-component integration. The Ghost-specific definitions have one authoritative home here.
