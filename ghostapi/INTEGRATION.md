# Worker connection

Status: implemented locally on `feat/ghost-api-interactive-demo` based on main
`60a45ec`. Live Steel + model verification and teammate receiver checks are pending.

## Execution order

1. The worker asks Ghost for a qualified compatible procedure.
2. A match runs through the DOM worker's guarded action loop with fresh observations
   and zero reasoning-model calls. A miss uses normal DOM exploration.
3. If DOM reports `needs_visual`, or canvas evidence accompanies an appropriate
   target/validation failure, the existing UI-TARS worker continues in the same Steel
   session. Canvas without readable DOM results requests visual help immediately
   after the first DOM inspection. Authentication, domain, cancellation and budget
   failures do not trigger visual fallback.
4. Independent result checks run before candidate saving. A successful task can still
   have `ghost.candidate_skipped` if its trace cannot be generalized safely.
5. Qualification is an explicit separate command. Qualified workflows can subsequently
   replay changed inputs; every replay is independently validated and recorded.

Ghost owns memory and binding. The worker connection owns the execution session when
the task uses `ownership: worker`; both DOM and visual adapters attach/detach, and
the connection releases Steel once after all work. An ARGUS-owned session stays open
unless `close_on_finish: true` transfers cleanup. A process-wide session lease prevents
simultaneous control through this connection. Separate processes must be coordinated
by their caller; no distributed lock is provided.

## Setup and run

Python 3.11 or later, from the repository root:

```sh
python -m pip install -e '.[test,visual]'
python -m playwright install chromium
python -m ghostapi.api
```

Ghost serves `/graph`, `/docs` and the API on port 8765. Set `GHOST_DATABASE_PATH`
to choose its SQLite file. In a second terminal:

```sh
export GHOST_API_URL=http://127.0.0.1:8765
python -m Agents.browser_worker.ghost_cli --live-steel --request Agents/browser_worker/examples/subtask.json
```

For Steel tasks, set `WORKER_BROWSER=steel`, `STEEL_API_KEY`, `OPENAI_API_KEY`, and
`UITARS_BASE_URL` for the fallback model endpoint. Use the explicit `--live-steel` flag
with a request; it requires a hosted, read-only target and never starts the localhost
fixture. Configure `WORKER_SITES_FILE` and
the request URLs for a reachable site: Steel cannot reach the sample's localhost.
All existing site/domain/control restrictions apply. To permit canvas interaction,
the trusted site's control rules must explicitly permit actions on the canvas.

`Worker.run(...)` and the existing worker FastAPI routes automatically use this
connection when `GHOST_API_URL` is set. Without it, `Worker.run` retains its standalone
DOM behavior. The CLI always connects to Ghost. `visual_fallback_available` defaults
to true; set it to false to disable fallback. Local Chrome mode runs DOM only because
the visual implementation requires a Steel session.

Python callers may inject a client explicitly:

```python
from Agents.browser_worker.ghost import GhostWorkflow
from Agents.browser_worker.runner import Worker
from ghostapi.client import GhostClient

async with GhostClient("http://127.0.0.1:8765") as client:
    report = await GhostWorkflow(Worker(), client).run(request)
```

## Reproducible local connection demo

Keep Ghost running. These commands start their own local catalog on port 8766;
exploration uses a labeled scripted policy and actual Chrome, with no Steel/model calls.

```sh
python -m Agents.browser_worker.ghost_cli --demo
python -m Agents.browser_worker.ghost_cli --demo --qualify demo-catalog.search_extract 1 \
  --inputs ghostapi/examples/qualification-inputs.json
python -m Agents.browser_worker.ghost_cli --demo --query keyboard --max-price 150
```

Use the candidate version printed by the first command instead of `1` if the database
already contains versions. The first run returns two verified headphone records and
`ghost.candidate.status: candidate`. Qualification opens three fresh browsers: keyboard
at 100, headphones at 100, and an evidenced empty search. The last command returns
`ghost.mode: reuse`, the mechanical keyboard at 89 USD, and `metrics.model_calls: 0`.
The CLI exits 0 for validated success/qualification and 1 otherwise.

## Messages and replay

The integrated Ghost messages use `schema_version: "0.2"` on the existing `/v1` routes.
The old `0.1` fixture boundary remains accepted and is excluded from `0.2` lookups.
DOM requests/reports retain their native `0.2` shape; `report.ghost` records the memory
decision and persistence errors, and `report.visual_report` contains fallback evidence.

The shared [client](client.py) provides `lookup`, `create_candidate`, `get_workflow`,
`report_run` and `submit_qualification`, with bounded HTTP timeouts and typed errors.
It does not automatically retry writes. Candidate and run idempotency keys are persisted
in SQLite: the same key/body returns the original identity, while changed content
with that key returns HTTP 409. Distinct executions can create new candidates.

[DOM normalization](../Agents/browser_worker/ghost_adapter.py) preserves native parameter
types, stable role/name targets, before/after observations and a reusable field recipe.
`wait_for` maps to `wait`; `extract_records` maps to `extract`. Ephemeral element and row
references stay in evidence and never become saved locators. Every input must have an
observed explicit parameter binding. Site settings, schema, allowed domains/paths and
success checks are hashed into a compatibility key; changing them forces exploration.

The [visual adapter](../Agents/visual/browser_subagent/ghost_adapter.py) continues the
existing UI-TARS screenshot loop. Integrated typing replaces the focused value and
records the trusted control's parameter origin. Only named semantic actions can enter
a candidate; coordinates remain evidence. Screenshot artifacts are embedded with hashes
(12 MiB candidate limit), so a saved candidate does not depend on a teammate's local path.
Unsupported actions or ambiguous origins reject compilation, without erasing a validated
task result. Raw visual reports/logs remain in the temporary `ghost-visual-*` run directory.

An optional operator-owned `SiteConfig.extraction_fields` recipe enables independent
DOM verification after visual work; the catalog includes one. Canvas-only answers
without independently verifiable records remain `inconclusive`, with their screenshot
report and summary available to the caller. They are never silently promoted to skills.

For integrated qualification, each report must cite a stored qualification run for the
same skill/version. Ghost checks passed validation, unchanged check IDs, the same browser
backend, three distinct changed input sets and fresh execution/session/evidence IDs,
released sessions, and a passed empty result. Ghost does not itself browse or prove that
an external caller's validation is truthful; its trusted worker performs the checks.

## Failure behavior and limitations

- Ghost offline/malformed: DOM exploration can still finish; `report.ghost.errors`
  exposes the memory failure. No candidate/reuse claim is invented.
- Stale/missing/ambiguous targets: replay stops with a typed failure. Eligible canvas
  cases may switch once to visual. The original failed replay stays failed in Ghost
  even when the visual continuation succeeds.
- Cancellation and action/model/runtime budgets carry across the handoff. A visual
  call already in progress is joined before session release; its configured HTTP timeout
  and browser cleanup can add a bounded delay after cancellation.
- No automatic repair, quarantine, recursive fallback, arbitrary JS workflow steps,
  general canvas validator, artifact retention service, distributed session lock, or
  integration with the separate ARGUS/moderator branches is implemented here.
- Ghost has no authentication. This setup binds localhost; remote deployment is separate.
- The Claude visual backend retains its standalone interface; this connection uses UI-TARS.

## Checks

```sh
python -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker -q
python -m unittest discover -s ghostapi/tests -v
python -m unittest discover -s ghostapi/demo -p 'test_*.py' -v
node --test ghostapi/demo/test_flowchart.js
python Agents/visual/test_parser.py
```

The connection tests use real local Chrome plus the actual Ghost API and temporary
SQLite databases. Steel lifecycle and the UI-TARS handoff are simulated in the automated
tests; they do not establish a credentialed cloud/model connection. The local CLI has
also been exercised against Ghost running as a separate HTTP process. Current results
and the next receiver check are recorded in [TEAM](../docs/ai/TEAM.md).
