# ARGUS DOM browser worker

This module implements Tianqi's browser subagent: receive one structured subtask, observe a configured website, ask GPT-5.4 for one typed operation, validate and execute it, observe again, and independently verify the extracted result. FastAPI and direct Python calls use the same `Worker.run` entry point. Reports include field sources, observations, action/value origins, failures, budgets, and a visual handoff suitable for ARGUS and Ghost consumers.

For copy-and-paste testing commands, start with the [testing runbook](TESTING.md).

The worker contract is an **implemented local 0.2 boundary awaiting INT-1 agreement**, separate from the historical synthetic contract 0.1 in `docs/hackathon/CONTRACTS.md`. GPT-5.4/FastAPI are HTML-1 implementation choices, not project-wide decisions. ARGUS/Ghost/visual receiver integration has not been verified. Ghost compilation, qualification, visual execution, and overall task planning remain other components.

## Quick test on this Windows checkout

Dependencies are installed in the repository's `.venv`. This command launches a real headless Chrome against the local catalog, uses a **scripted fixture policy** in place of GPT, prints the full report, and closes the browser/server:

```powershell
.\.venv\Scripts\python.exe -m browser_worker.demo.run --executable "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

Expected: `outcome: succeeded`, Studio headphones (129 USD), Travel headphones (79 USD), four operations, five reasoning decisions, all validation checks passing, `session_disposition: released`. `reasoning: scripted_fixture` explicitly identifies this as a local mechanics test, not a live GPT/Steel run. Token counts are null for the scripted policy.

## Setup on another machine

Requires Python 3.11+ (tested with 3.12.14). From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

On macOS/Linux use `.venv/bin/python` in place of `.\.venv\Scripts\python.exe` and `cp` instead of `Copy-Item`. Run `.venv/bin/python -m playwright install chromium` before local tests (Linux CI uses `install --with-deps chromium`). Tests can also discover common Windows/macOS Chrome/Edge paths, or use an explicit `WORKER_BROWSER_EXECUTABLE`. With no executable override/fallback, Playwright uses its installed Chromium. Steel supplies its own browser. Copy `.env.example` only if `.env` does not exist; merge needed names into an existing file without replacing teammate settings.

Runtime dependencies are declared in [pyproject.toml](../pyproject.toml). The Python package is `steel-sdk`; its import is `steel`. A tested dependency snapshot is in [requirements-tested.txt](requirements-tested.txt); install it before `pip install -e ".[test]"` to reproduce the tested versions.

## Environment

Set these in the root `.env` or process environment. Keys are never included in reports/model input. Existing process values take precedence over `.env`.

| Name | Purpose / default |
| --- | --- |
| `OPENAI_API_KEY` | Required for real GPT calls; absent keys return `MODEL_ERROR` |
| `STEEL_API_KEY` | Required in Steel mode; absent keys return `SESSION_UNAVAILABLE` |
| `OPENAI_MODEL` | `gpt-5.4`; also accepts pinned `gpt-5.4-2026-03-05` |
| `OPENAI_REASONING_EFFORT` | `medium`; optionally `high` |
| `OPENAI_MAX_OUTPUT_TOKENS` | 4096, constrained to 512–16384 |
| `MODEL_TIMEOUT_SECONDS` | 45 seconds per call |
| `BROWSER_TIMEOUT_SECONDS` | 10 seconds per browser operation |
| `WORKER_BROWSER` | `steel` by default; `local` for local Chromium |
| `WORKER_BROWSER_EXECUTABLE` | Optional local Chrome/Chromium executable path |
| `WORKER_SITES_FILE` | Trusted configuration JSON; defaults to [sites.json](sites.json) |
| `WORKER_API_TOKEN` | Optional bearer token; keep API on loopback when unset |

SDK automatic retries are disabled so the worker's own counters include every API attempt. The model receives one fresh compact context per call with strict function tools, `tool_choice="required"`, `parallel_tool_calls=False`, and `store=False`. Malformed calls and API timeouts feed typed errors into the next bounded decision. No provider-specific chain of thought is stored or requested.

## Start the API and run a subtask

For a real GPT + local browser run, set `OPENAI_API_KEY` in `.env`, set `WORKER_BROWSER=local`, and optionally set `WORKER_BROWSER_EXECUTABLE`. Start the fixture in one terminal:

```powershell
.\.venv\Scripts\python.exe -m browser_worker.demo.run --serve-only
```

Start the worker in another terminal:

```powershell
.\.venv\Scripts\python.exe -m uvicorn browser_worker.main:app --host 127.0.0.1 --port 8000
```

Submit the included [complete request example](examples/subtask.json):

```powershell
$subtaskBody = Get-Content -Raw browser_worker/examples/subtask.json
$job = Invoke-RestMethod http://127.0.0.1:8000/api/subtasks -Method Post -ContentType application/json -Body $subtaskBody
Invoke-RestMethod "http://127.0.0.1:8000/api/subtasks/$($job.job_id)"
```

Poll that URL until `report` is present. To cancel, POST `/api/subtasks/{job_id}/cancel`. For a single request that waits for the report, POST the same JSON to `/api/subtasks/execute`. Interactive schema documentation is available at `http://127.0.0.1:8000/docs`.

`python -m browser_worker.demo.run --live-model` is an alternative single-command GPT + local browser smoke test. It starts and stops the local fixture automatically. It still requires `OPENAI_API_KEY` and a local browser.

For **Steel + GPT**, set `WORKER_BROWSER=steel` and both API keys. Steel's cloud browser cannot reach the local fixture at `127.0.0.1`. Host the catalog or configure another reachable read-only site, create an operator-owned site configuration with its actual origin/paths/selectors, and point `WORKER_SITES_FILE` to it. Update the request's `start_url`, `allowed_domains`, and `allowed_url_patterns` to match. No website is deployed automatically by this module.

Before configuring a full site, validate the Steel credential and cloud-browser lifecycle without an OpenAI call:

```powershell
.\.venv\Scripts\python.exe -m browser_worker.demo.steel_sanity
```

The command reads the key through a hidden prompt, opens `https://example.com/`, captures a DOM observation, and confirms release. See the [testing runbook](TESTING.md) for expected output.

This Steel-only check succeeded on 2026-09-12 with `steel-sdk` 0.19.0: session created, Playwright connected, `Example Domain` observed, and the worker-owned session released. It does not establish GPT or configured task execution.

API routes:

| Route | Result |
| --- | --- |
| `GET /health` | Mode, model, site IDs, and key presence booleans; no key values |
| `POST /api/subtasks` | 202 with opaque `job_id`, current status, polling URL |
| `POST /api/subtasks/execute` | Waits for authoritative `SubtaskReport` |
| `GET /api/subtasks/{job_id}` | Current status and report when complete |
| `POST /api/subtasks/{job_id}/cancel` | Requests cancellation, including an in-flight model/browser call |
| `GET /api/schemas/subtask-request` | Current request JSON schema |
| `GET /api/schemas/subtask-report` | Current report JSON schema |

API storage is process-local: at most four concurrent jobs and 100 retained reports, with oldest completed reports evicted. Repeating the same request/run/subtask IDs and body returns the existing job while retained; different inputs with the same IDs return 409. Parallel use of one supplied session returns 409. Use one Uvicorn process; multiple processes would need a shared lock/store. No queues, SSE, or durable storage are implemented.

## Direct Python entry point

This is the intended ARGUS/toolbox path: no HTTP layer is required. FastAPI is an optional standalone transport, not a prerequisite for integration.

```python
import asyncio
import json
from pathlib import Path
from dotenv import load_dotenv
from browser_worker.runner import Worker

load_dotenv()
request = json.loads(Path("browser_worker/examples/subtask.json").read_text())
report = asyncio.run(Worker().run(request))
print(report.model_dump_json(indent=2))
```

`await worker.run(request, cancel_event, on_status)` also accepts an `asyncio.Event` and a synchronous status callback. Direct callers must coordinate exclusive use of shared sessions themselves. Invalid input raises `WorkerError` before any browser/model initialization; execution failures return a report. The FastAPI wrapper maps input errors to HTTP 422.

## Request semantics and site configuration

Every request includes IDs, version, objective, `site_id`, `operation: search_extract`, session ownership, parameters, known output schema ID, explicit success checks, allowed actions/domains, budgets, and required evidence. `start_url` can be omitted; a newly created browser uses the configured URL and an attached browser uses its current page. Prerequisites are optional but must all be `succeeded` if supplied. All supported evidence types are always returned, even if the request asks for fewer.

The **trusted operator configuration**, not the model or request, defines:

- Exact hostnames, whole-origin/path glob patterns, permitted query keys, and optional asset hosts. Requests can only narrow these permissions. Wildcards belong in paths, not hostname suffix comparisons.
- `controls`: audited selectors, allowed fill/select/click operations, and parameter bindings. A read-only search button is allowed; account changes, purchases, deletions, and message submissions are not. Known prohibited objective verbs are rejected conservatively; actual authority comes from these controls and network rules.
- Parameter and record JSON schemas, with unknown fields rejected. Schemas are registered by `output_schema_id`, not supplied as arbitrary model output.
- Result containers, settled-result marker, empty-state marker, loading/error/login indicators, and **applied-result evidence for every parameter**. An input value alone is insufficient proof that a search/filter took effect.
- Mandatory checks that the request cannot remove. The example requires title substring match, maximum numeric price, and exact USD currency. Request checks add minimum/maximum row counts, field equality, numeric bounds, or substring conditions.

Add other sites by authoring configuration after inspecting the site's real semantics. The initial fixture is the only site configured out of the box. Do not approve broad control selectors such as every button on an unfamiliar website.

## Observations, actions, extraction, and verification

[browser/observe.js](browser/observe.js) is fixed application code. It reads visible DOM text, forms, labels, approximate accessibility roles/names, values, links, options, tables/lists/containers, and selected attributes. It excludes scripts, styles, navigation/footer noise, hidden content, password/secret-marked elements and their descendants. There is no model-generated JavaScript execution. Each observation has a timestamp, content hash, execution/run ID, and references valid only for that observation. The live DOM is checked again before action/extraction. Duplicate semantic controls and unnamed targets are rejected.

Tool names: `navigate`, `fill`, `select`, `click`, `wait_for`, `inspect_element`, `extract_records`, `report_success`, `report_failure`, `request_visual_fallback`. All argument fields are required in the strict model schema, with null/empty fields for unused arguments. Values must exactly equal a named request parameter or a configured approved literal. State-changing operations are followed by bounded settling, a new observation, and a recorded expected-change comparison.

Extraction reads **observed fields** from model-proposed element mappings within current configured result containers. Missing nullable fields remain null. Numeric conversion accepts unambiguous decimal text only; it does not infer currency, strip arbitrary labels, or guess locale formatting. Use a numeric-only price element for the configured numeric field. Duplicate containers are rejected; separate equal-looking rows are preserved. There is no silent deduplication by name or URL.

`report_success` only proposes completion. Verification rechecks the record schema, field provenance, current execution, extraction freshness, all visible record coverage, result/empty readiness, every applied parameter, configured/request conditions, and approved links. Any failed check returns `inconclusive`; a model verdict cannot override it. Coverage is limited to the final visible configured result set and does not prove exhaustive public-site search or interpret arbitrary natural-language conditions.

## Reports and failures

The sample run's report contains two `records`, each shaped like this (IDs/times are generated at runtime):

```json
{
  "data": {"title": "Studio headphones", "price": 129.0, "currency": "USD", "url": "http://127.0.0.1:8765/products/studio"},
  "source_observation_id": "current-observation-id",
  "container_ref": "e17",
  "field_sources": [{"field": "title", "element_ref": "e18", "attribute": "text"}],
  "retrieved_at": "2026-09-12T18:00:00Z"
}
```

This abbreviated shape shows one field source; actual records include sources for every field. The full report also includes `browser_backend`, `reasoning_backend`, `validation`, `observations`, `action_trace`, `parameter_origins`, `evidence_refs`, metrics, failures, limitations, final URL, and session disposition. Scripted reports identify `reasoning_backend: scripted_fixture`. Trace arguments contain only requested operations, semantic descriptions, parameter origins, expected changes, and extraction mappings; no raw DOM handles are serialized. A rejected call is recorded with its typed error and the recovery observation when available. Raw malformed model messages are not retained. Individual text fields are capped at 800 characters; truncated field sources are rejected and must be replaced with a smaller element mapping.

Malformed request example: changing `max_price` to the string `"150"` returns HTTP 422 with `error.code: INVALID_INPUT`. An unconfigured domain returns `DOMAIN_NOT_ALLOWED`. A missing Steel key returns a `failed` report with `SESSION_UNAVAILABLE` and `session_disposition: not_started`. A false success claim yields `inconclusive` with `VALIDATION_FAILED` and failed checks. A stopped run yields `cancelled` with `CANCELLED`.

Other typed errors cover unavailable/ambiguous/stale targets, action rejection, extraction failure, authentication, model/API timeout, budgets, no progress, and internal errors. Low-level provider errors are sanitized because their messages may contain connection credentials. Console logging contains operational execution IDs/stages/counters; the returned report holds detailed observations and trace evidence. Treat reports as browsing data and use non-sensitive sites/parameters.

Model `report_failure` accepts only `PRECONDITION_FAILED`, `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS`, `EXTRACTION_FAILED`, `AUTH_REQUIRED` or `UNSUPPORTED_OPERATION`. Other codes become `PRECONDITION_FAILED`; the model cannot manufacture runtime cancellation, budget exhaustion or internal errors. `CANCELLED` requires actual cancellation.

## Session ownership and visual handoff

`session: {"ownership":"worker"}` creates a session and always attempts release, including initialization failures, cancellation, and budget exhaustion. `session: {"ownership":"argus","session_ref":"opaque-id"}` attaches to a dedicated one-page Steel session and disconnects without releasing it. Set `close_on_finish:true` only when ARGUS explicitly transfers cleanup authority. Supply opaque IDs, never credential-bearing websocket URLs. Cleanup has up to 15 additional seconds after the execution budget and reports `cleanup_failed` honestly.

Use an **ARGUS-owned session for a continuing visual handoff**. The worker returns `needs_visual` with IDs, URL, observation reference, semantic target/candidates, previous steps, reason, remaining budgets, and visual question. It never guesses coordinates. Worker-owned sessions are released even on visual handoff, so that report alone cannot retain a live page. `visual_fallback_available` records caller capability; visual interpretation itself is not implemented here.

Thomas and Tianqi still need to converge this adapter and `workers/visual/` into one shared Steel toolbox, agree session lifecycle ownership and verify `needs_visual` compatibility at INT-1. This local handoff shape does not establish compatibility with the visual worker.

The network adapter blocks non-GET/HEAD requests and disallowed navigations, including every HTTP redirect hop. Ambient non-navigation POST/XHR/beacon traffic is aborted without failing the task. Prohibited navigation is terminal; writes intercepted during a worker `navigate`/`fill`/`select`/`click` are conservatively terminal. This action-window heuristic cannot distinguish coincident analytics from action effects; delayed writes outside the window are still aborted but not terminal. Result verification must still prove the requested read-only outcome. Allowed main-page redirects have a ten-hop cap. Asset/iframe redirects and websockets are blocked; disallowed passive assets are skipped. This is not an OS/network sandbox: only audited read-only sites are supported, since GET endpoints can have side effects.

## Tests and limitations

```powershell
.\.venv\Scripts\python.exe -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker -q
.\.venv\Scripts\python.exe -m ruff check --config browser_worker/ruff.toml browser_worker tests/browser_worker
.\.venv\Scripts\python.exe -m ruff format --check --config browser_worker/ruff.toml browser_worker tests/browser_worker
```

Tests marked `browser` drive a real local Chromium/Chrome browser. They use port 8765; leave it free and run sequentially. Add `-m "not browser"` to the scoped pytest command for policy, mocked OpenAI/Steel lifecycle and API checks without a browser. No suite calls paid providers. Pytest/Ruff configuration is module-scoped; the root packaging file does not change other workstreams' discovery or lint rules. The separate `.github/workflows/browser-worker.yml` job runs these commands on Linux; Ghost's workflow is unchanged.

Known limits: configured sites only; approximate DOM accessibility names; at most 220 observed elements and 12,000 visible-text characters; truncated observations cannot pass verification. Iframes, shadow DOM, canvas contents, screenshots, JSON-LD extraction, and arbitrary source-code interpretation are not implemented. `needs_visual` is a handoff, not an embedded VLM. Login/account actions, POST search endpoints, downloads, arbitrary code, and transactional submissions are unsupported. The Steel create/connect/observe/release sanity check passed at the recorded checkpoint; live GPT, hosted configured-site tasks and receiver integration remain unverified.

## File map and sources

| File | Responsibility |
| --- | --- |
| [main.py](main.py), [api.py](api.py) | FastAPI app, jobs, input errors, polling/cancellation |
| [runner.py](runner.py) | Worker state, loop, budgets, recovery, cleanup |
| [schemas.py](schemas.py) | Strict versioned input/action/evidence/report models |
| [config.py](config.py), [sites.json](sites.json) | Trusted site and runtime settings |
| [policy.py](policy.py) | Request, URL, action, and value-origin validation |
| [browser/adapter.py](browser/adapter.py), [browser/observe.js](browser/observe.js) | Steel/local lifecycle, DOM observations, guarded actions |
| [llm.py](llm.py), [prompts.py](prompts.py) | GPT-5.4 Responses client and strict function definitions |
| [verifier.py](verifier.py) | Observed-value extraction and deterministic verification |
| [demo/run.py](demo/run.py), [demo/catalog.html](demo/catalog.html) | Explicitly scripted local demo and controlled fixture |

API wiring was checked against official [GPT-5.4 documentation](https://developers.openai.com/api/docs/models/gpt-5.4), [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling), [Steel Playwright integration](https://docs.steel.dev/integrations/playwright), and [Playwright networking](https://playwright.dev/python/docs/network), plus the installed Steel SDK's actual method signatures. The model/strict-tool defaults follow those references; the worker's permission and verification rules are application-owned.
