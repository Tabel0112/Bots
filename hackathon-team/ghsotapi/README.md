# Ghost API

## Live workflow demo

From the `hackathon-team` project directory:

```sh
python3 ghsotapi/demo/live_demo.py
```

Open [the local viewer](http://127.0.0.1:8765) to watch matching, executed steps, results, and qualification on an interactive flowchart while the task runs. Drag the empty chart canvas to pan, click nodes to inspect their evidence, use the history slider or Step buttons to revisit execution, adjust zoom, and select Follow live to resume tracking. Additional tasks queue while the viewer remains responsive. The browser/catalog are simulated; events and database writes come from the running demo. Stop the server with Ctrl+C.


Ghost receives a task from ARGUS, finds a compatible saved workflow, and binds the new inputs for execution. When no suitable workflow exists, a browser agent discovers a procedure and Ghost turns the successful trace into a candidate for qualification and future reuse.

The folder is named `ghsotapi/` as requested. The product name remains Ghost API.

## Read first

| Document | Purpose |
| --- | --- |
| [Development guide](DEVELOPMENT.md) | Current code, setup, storage, implementation sequence, and acceptance criteria |
| [Workflow structure](WORKFLOW-STRUCTURE.md) | Detailed design for finding, saving, using, and creating workflows |
| [Ghost contract](CONTRACTS.md) | Workflow schema, lifecycle, and function interfaces |
| [Demo instructions](demo/README.md) | Interactive and JSON task examples |
| [Person B work log](B-ghost.md) | Original Ghost workstream assignments and live integration checklist |

## Try the demo

From the `hackathon-team` project directory:

```sh
python3 ghsotapi/demo/ghost_demo.py
```

Search `headphones` at `150`, then `keyboard` at `100`. On a new database, the first task creates a workflow and the next reuses it. Database persistence is real; discovery, products, and browser execution are simulated.

## Tests and coverage

Twelve Python tests cover workflow lifecycle, persistence, the live queue, error handling, and HTTP endpoints. Five JavaScript tests cover pointer panning and cleanup. CI generates Cobertura XML and LCOV reports and uploads them to Codecov using the `ghost-python` and `ghost-flowchart` flags. See the [demo instructions](demo/README.md) for local commands.

## Shared dependencies

- [Shared contracts](../CONTRACTS.md): task requests, browser interfaces, results, API/events, errors, and recovery budgets.
- [Team plan](../PLAN.md): ownership, dependencies, and integration gates.
- [Evaluation](../EVALUATION.md): live browser acceptance and demonstration criteria.
- [Browser workstream](../people/A-browser.md), [ARGUS workstream](../people/C-argus.md), and [interface workstream](../people/D-experience.md).

## File moves

| Previous location | Current location |
| --- | --- |
| `GHOST-WORKFLOW-STRUCTURE.md` | `ghsotapi/WORKFLOW-STRUCTURE.md` |
| `people/B-ghost.md` | `ghsotapi/B-ghost.md` |
| `demo/` | `ghsotapi/demo/` |
| Workflow schema and Ghost interface sections in root `CONTRACTS.md` | `ghsotapi/CONTRACTS.md`, with links from the shared contract |

Shared documents retain the Ghost references needed to describe cross-component integration. The Ghost-specific definitions have one authoritative home here.
