# Browser worker testing runbook

This runbook shows how to exercise the ARGUS DOM browser worker from the repository root on Windows PowerShell. Start with the no-key test. That confirms the browser mechanics, action loop, extraction, verification, and cleanup before introducing OpenAI or Steel credentials.

## 1. Open PowerShell in the repository

```powershell
Set-Location "C:\TianqiUser\BattleOfSchools Hackathon\Bots"
```

Confirm that you are in the correct folder:

```powershell
Get-Location
Test-Path .\Agents\browser_worker\README.md
Test-Path .\.venv\Scripts\python.exe
```

Expected: both `Test-Path` commands print `True`.

Check Python and the installed package:

```powershell
.\.venv\Scripts\python.exe --version
.\.venv\Scripts\python.exe -c "import Agents.browser_worker; print(Agents.browser_worker.__version__)"
```

Expected: Python 3.11 or newer and browser-worker version `0.2.0`.

If `.venv` does not exist, create it and install the project:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r Agents\browser_worker\requirements-tested.txt
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
```

The current machine already has Chrome at:

```text
C:\Program Files\Google\Chrome\Application\chrome.exe
```

On another machine, either install Playwright Chromium:

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
```

or locate Chrome/Edge:

```powershell
Get-Item "C:\Program Files\Google\Chrome\Application\chrome.exe", `
  "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  -ErrorAction SilentlyContinue | Select-Object FullName
```

On macOS/Linux create the environment with `python3 -m venv .venv`, use `.venv/bin/python` for all Python commands, and run `.venv/bin/python -m playwright install chromium` before browser tests. Linux CI installs Chromium with `--with-deps`. Tests use installed Chromium when no configured or common Windows/macOS Chrome/Edge executable is found.

## 2. Fastest test: real browser, no API keys

Run the controlled catalog test:

```powershell
.\.venv\Scripts\python.exe -m Agents.browser_worker.demo.run `
  --executable "C:\Program Files\Google\Chrome\Application\chrome.exe"
```

This starts a local catalog on port 8765, launches real headless Chrome, fills the search and maximum-price fields, clicks Search, extracts the visible products, verifies the evidence, prints the report, and cleans everything up.

This test uses a deterministic scripted decision fixture so it does not consume OpenAI or Steel credits. Look for these values in the output:

```text
reasoning: scripted_fixture
browser_backend: local
reasoning_backend: scripted_fixture
outcome: succeeded
summary: Verified 2 records from the observed results.
actions: 4
model_calls: 5
session_disposition: released
```

The two expected products are:

```text
Studio headphones — 129 USD
Travel headphones — 79 USD
```

If port 8765 is busy, find the owning process:

```powershell
Get-NetTCPConnection -LocalPort 8765 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress, LocalPort, State, OwningProcess
```

Stop only a process you recognize. The demo normally stops its own server even when the worker fails.

## 3. Run the automated checks

Run the complete suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker -q
```

Expected current result:

```text
74 passed
```

The suite includes real local-browser tests and mocked OpenAI/Steel boundary tests. It never calls paid providers.

Run only the checks that do not launch a browser:

```powershell
.\.venv\Scripts\python.exe -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker -q -m "not browser"
```

Run only real-browser checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker -q -m browser
```

Run code-quality and dependency checks:

```powershell
.\.venv\Scripts\python.exe -m ruff check --config Agents/browser_worker/ruff.toml Agents/browser_worker tests\browser_worker
.\.venv\Scripts\python.exe -m ruff format --check --config Agents/browser_worker/ruff.toml Agents/browser_worker tests\browser_worker
.\.venv\Scripts\python.exe -m pip check
```

Expected: Ruff reports that all checks pass, all files are formatted, and pip reports no broken requirements.

## 4. Test GPT-5.4 with the local browser

This is the most useful next test because it replaces the scripted decision fixture with the real model while keeping the website local and predictable.

Create the environment file if it does not exist:

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
notepad .env
```

Set these values in `.env`:

```dotenv
OPENAI_API_KEY=your-openai-api-key
OPENAI_MODEL=gpt-5.4
OPENAI_REASONING_EFFORT=medium
OPENAI_MAX_OUTPUT_TOKENS=4096
MODEL_TIMEOUT_SECONDS=45
BROWSER_TIMEOUT_SECONDS=10
WORKER_BROWSER=local
WORKER_BROWSER_EXECUTABLE=C:\Program Files\Google\Chrome\Application\chrome.exe
```

Leave `STEEL_API_KEY` empty for this test. `.env` is ignored by Git. Do not put credentials into `Agents/browser_worker/examples/subtask.json` or a command-line argument.

Run the live-model demo:

```powershell
.\.venv\Scripts\python.exe -m Agents.browser_worker.demo.run --live-model
```

The command starts the controlled site, makes real GPT-5.4 Responses API calls, drives local Chrome, prints the structured report, and stops the site/browser.

A successful report should contain:

```text
reasoning: gpt-5.4
browser_backend: local
reasoning_backend: gpt-5.4
outcome: succeeded
session_disposition: released
```

The number of actions/model calls can differ from the scripted test because GPT chooses the steps. Check these report sections:

- `records`: Studio headphones and Travel headphones with prices, currency, URL, observation ID, container reference, and field sources.
- `validation`: every `passed` value should be `true`.
- `action_trace`: each attempted operation, its before/after observations, outcome, and expected-page-change result.
- `parameter_origins`: the query and maximum price should originate from request parameters.
- `metrics`: model calls, browser actions, retries, elapsed time, and token usage.
- `failures`: should be empty for a clean run.

If the model proposes completion without valid extraction evidence, the worker should return `inconclusive` instead of accepting success. That demonstrates the independent verifier working as intended.

## 5. Test through the FastAPI interface

This simulates the interface ARGUS will call. It needs three PowerShell terminals for a GPT-backed test.

Before starting, ensure `.env` still has `WORKER_BROWSER=local` and a valid `OPENAI_API_KEY`.

### Terminal 1: start the controlled catalog

```powershell
Set-Location "C:\TianqiUser\BattleOfSchools Hackathon\Bots"
.\.venv\Scripts\python.exe -m Agents.browser_worker.demo.run --serve-only
```

Expected:

```text
Controlled catalog: http://127.0.0.1:8765 (Ctrl+C to stop)
```

### Terminal 2: start the browser-worker API

```powershell
Set-Location "C:\TianqiUser\BattleOfSchools Hackathon\Bots"
.\.venv\Scripts\python.exe -m uvicorn Agents.browser_worker.main:app `
  --host 127.0.0.1 --port 8000
```

Keep this terminal open. The API loads `.env` at startup, so restart it after changing environment values.

### Terminal 3: check health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | Format-List
```

For the GPT + local-browser setup, expect:

```text
status              : ok
schema_version      : 0.2
browser             : local
model               : gpt-5.4
sites               : {demo-catalog}
openai_configured   : True
```

`steel_configured` can be `False` in local mode.

### Submit and poll an asynchronous subtask

```powershell
$subtaskBody = Get-Content -Raw .\Agents\browser_worker\examples\subtask.json
$job = Invoke-RestMethod http://127.0.0.1:8000/api/subtasks `
  -Method Post -ContentType "application/json" -Body $subtaskBody
$job | Format-List
```

Poll until the report is ready:

```powershell
do {
  Start-Sleep -Milliseconds 500
  $status = Invoke-RestMethod "http://127.0.0.1:8000/api/subtasks/$($job.job_id)"
  Write-Host "Status: $($status.status)"
} until ($null -ne $status.report)

$status.report | ConvertTo-Json -Depth 30
```

Display a compact result:

```powershell
$status.report | Select-Object outcome, summary, browser_backend, reasoning_backend, final_url, session_disposition
$status.report.records | ForEach-Object { $_.data } | Format-Table title, price, currency, url
$status.report.validation | Format-Table check_id, passed, detail
```

### Run synchronously

The synchronous route waits for the final report:

```powershell
$subtaskBody = Get-Content -Raw .\Agents\browser_worker\examples\subtask.json
$report = Invoke-RestMethod http://127.0.0.1:8000/api/subtasks/execute `
  -Method Post -ContentType "application/json" -Body $subtaskBody
$report | ConvertTo-Json -Depth 30
```

The API remembers request IDs while the process is running. Reusing the same JSON returns the existing job/report. To force a separate run, restart the API or copy the example and change `request_id`, `run_id`, and `subtask_id` together.

### Cancel a running task

Start another asynchronous task with fresh IDs, then run:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/subtasks/$($job.job_id)/cancel" -Method Post
```

Poll the task again. A cancellation that interrupts active work should eventually produce:

```text
outcome: cancelled
failure code: CANCELLED
```

Cleanup still runs after cancellation.

### Inspect schemas and interactive API documentation

Open the generated API documentation in a browser:

```text
http://127.0.0.1:8000/docs
```

Or fetch the JSON schemas in PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/schemas/subtask-request |
  ConvertTo-Json -Depth 30

Invoke-RestMethod http://127.0.0.1:8000/api/schemas/subtask-report |
  ConvertTo-Json -Depth 30
```

When finished, press `Ctrl+C` in Terminals 1 and 2.

## 6. Optional API bearer token

For local development on `127.0.0.1`, the token is optional. To test authentication, add a random value to `.env`:

```dotenv
WORKER_API_TOKEN=replace-with-a-long-random-value
```

Restart Uvicorn, then make requests with the header:

```powershell
$headers = @{ Authorization = "Bearer replace-with-a-long-random-value" }
Invoke-RestMethod http://127.0.0.1:8000/health -Headers $headers
```

Without the header, the API returns HTTP 401. Avoid binding the API to a public interface without authentication and a proper deployment boundary.

## 7. Test the real Steel connection

This test requires:

- `OPENAI_API_KEY` for GPT-5.4.
- `STEEL_API_KEY` for the cloud browser.
- A publicly reachable read-only test site.
- A trusted worker site configuration for that exact site's controls, paths, parameters, result schema, and verification markers.

First, test only the Steel session lifecycle and browser connection. This command prompts for the key without echoing it and does not make an OpenAI call:

```powershell
.\.venv\Scripts\python.exe -m Agents.browser_worker.demo.steel_sanity
```

It creates a worker-owned Steel session, connects with Playwright, opens `https://example.com/`, observes the title and DOM, and verifies that cleanup reports `released`. The key is not saved to `.env`, the command line, or the result.

Expected result:

```text
session_created: true
connected: true
observed: true
title: Example Domain
final_url: https://example.com/
session_disposition: released
outcome: succeeded
error: null
```

The included catalog uses `127.0.0.1:8765`. A Steel cloud session cannot reach that address on your computer, so simply changing `WORKER_BROWSER` to `steel` is insufficient.

After making the catalog reachable at a public HTTPS address, copy the site configuration:

```powershell
Copy-Item .\Agents\browser_worker\sites.json .\Agents\browser_worker\sites.steel.json
notepad .\Agents\browser_worker\sites.steel.json
```

Update these fields in the copied configuration to the real deployment:

- `start_url`
- `allowed_domains`
- `allowed_url_patterns`
- Any selectors or verification markers changed by the hosted page

Copy the sample request and update its matching URL/domain fields and IDs:

```powershell
Copy-Item .\Agents\browser_worker\examples\subtask.json .\Agents\browser_worker\examples\subtask.steel.json
notepad .\Agents\browser_worker\examples\subtask.steel.json
```

Update `.env`:

```dotenv
OPENAI_API_KEY=your-openai-api-key
STEEL_API_KEY=your-steel-api-key
OPENAI_MODEL=gpt-5.4
OPENAI_REASONING_EFFORT=medium
WORKER_BROWSER=steel
WORKER_SITES_FILE=Agents/browser_worker/sites.steel.json
```

Start the API:

```powershell
.\.venv\Scripts\python.exe -m uvicorn Agents.browser_worker.main:app `
  --host 127.0.0.1 --port 8000
```

Check that Steel mode and both key-presence flags are reported:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health | Format-List
```

Submit the Steel request synchronously:

```powershell
$steelBody = Get-Content -Raw .\Agents\browser_worker\examples\subtask.steel.json
$steelReport = Invoke-RestMethod http://127.0.0.1:8000/api/subtasks/execute `
  -Method Post -ContentType "application/json" -Body $steelBody
$steelReport | ConvertTo-Json -Depth 30
```

Confirm all of the following before calling this a successful live integration:

- `browser_backend` is `steel`.
- `reasoning_backend` is `gpt-5.4` or the pinned GPT-5.4 snapshot.
- `outcome` is `succeeded`.
- Every validation check passed.
- Records contain current observation and field-source references.
- `session_disposition` is `released` for a worker-owned session.
- OpenAI token counts are present in `metrics`.

If the session is owned by ARGUS, use an opaque `session_ref`; the report should say `retained` unless `close_on_finish` explicitly transfers cleanup authority.

## 8. Change the sample search

The included demo site's accepted maximum prices are `50`, `100`, `150`, and `200`. Create a new request file:

```powershell
$request = Get-Content -Raw .\Agents\browser_worker\examples\subtask.json | ConvertFrom-Json
$suffix = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
$request.request_id = "request-$suffix"
$request.run_id = "run-$suffix"
$request.subtask_id = "catalog-search-$suffix"
$request.parameters.query = "keyboard"
$request.parameters.max_price = 100
$request.objective = "Search the catalog for keyboard products costing at most 100 USD."
$request | ConvertTo-Json -Depth 20 | Set-Content .\Agents\browser_worker\examples\subtask.custom.json
```

Submit it:

```powershell
$customBody = Get-Content -Raw .\Agents\browser_worker\examples\subtask.custom.json
$customReport = Invoke-RestMethod http://127.0.0.1:8000/api/subtasks/execute `
  -Method Post -ContentType "application/json" -Body $customBody
$customReport.records | ForEach-Object { $_.data } | Format-Table
```

Expected result: Mechanical keyboard at 89 USD.

To test a verified empty result, use a query such as `unobtainium`. A correct empty result can still have `outcome: succeeded` because the page's configured empty-state marker proves there are no visible matches.

## 9. Common failures

| Symptom or code | Meaning and next check |
| --- | --- |
| Python or `.venv` path not found | Run the commands from the repository root; create/install `.venv` if needed |
| Browser executable does not exist | Install Playwright Chromium or set `WORKER_BROWSER_EXECUTABLE` to Chrome/Edge |
| Port 8765 or 8000 already in use | Stop the earlier demo/API process or identify the owner with `Get-NetTCPConnection` |
| `MODEL_ERROR` | Check `OPENAI_API_KEY`, model access, network access, and model-call details in the report |
| `MODEL_TIMEOUT` | Increase `MODEL_TIMEOUT_SECONDS` within the worker's allowed range or retry after checking connectivity |
| `SESSION_UNAVAILABLE` | In Steel mode, check the key, account access, session availability, and reachable configured URL |
| `DOMAIN_NOT_ALLOWED` | Request URL/domain/path does not fit both the trusted site config and request allowlist |
| `ACTION_REJECTED` | The proposed control/action/value is not permitted by configuration or did not match its origin |
| `TARGET_NOT_FOUND` / `STALE_OBSERVATION` | The page changed or a current observation does not contain the requested element; bounded recovery should re-observe |
| `EXTRACTION_FAILED` | Field mappings are incomplete, ambiguous, outside the result container, truncated, or fail the record schema |
| `inconclusive` with `VALIDATION_FAILED` | The model proposed success, but current evidence did not prove all deterministic checks |
| `needs_visual` | DOM structure was insufficient; the report is ready for the separate visual worker handoff |
| `BUDGET_EXHAUSTED` / `NO_PROGRESS` | The worker reached a configured limit or repeated actions without new page evidence |
| `cleanup_failed` | The session owner should verify/release the Steel session manually |

For a compact failure view:

```powershell
$report.failures | Format-Table code, message, retryable
$report.validation | Where-Object { -not $_.passed } | Format-Table check_id, detail
```

## 10. What each test proves

- The no-key demo proves the local browser adapter, observation references, policy checks, execution loop, extraction, verifier, report, and cleanup.
- The automated suite proves the tested scenarios, including error recovery and mocked SDK boundaries. It does not prove a real provider connection.
- The GPT + local-browser test proves the real model can use the strict operation schema against a controlled page.
- The FastAPI test proves the transport ARGUS will call, including submit/poll/cancel behavior.
- The Steel + GPT test proves the actual cloud-browser/model connection only when it uses real credentials and a reachable configured site.
- ARGUS, Ghost, and visual-worker integration require their respective receivers to consume the resulting contract and evidence successfully.

Do not report Steel, GPT, ARGUS, Ghost, or visual integration as complete based only on the scripted demo or mocked tests.
