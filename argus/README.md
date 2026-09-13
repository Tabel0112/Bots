# ARGUS controller

Takes one request in plain language, decides whether it may run, plans it into
subtasks, dispatches them to browser workers, judges and validates what comes
back, and publishes exactly one terminal result whose claims are structured
field references to controller-selected, validated records carrying observations
from that run. The controller owns run state, stage transitions,
budgets, browser sessions, the event stream, final rendering and the terminal
result; the moderator, the toolbox and Ghost are called through protocols and
only return decisions or structured selections.

Design: [docs/hackathon/ARGUS.md](../docs/hackathon/ARGUS.md) (stages, the
open-world delta and the controller/moderator boundary) and
[docs/hackathon/ARGUS-IMPLEMENTATION.md](../docs/hackathon/ARGUS-IMPLEMENTATION.md)
(phases and prompts).

**Status: Mission Control runs live by default.** It uses the real controller,
real Steel browser sessions, public Staples/Wikivoyage/Remotive pages, Ghost's
SQLite registry lookup, deterministic DOM extraction and the structured moderator.
Set `ARGUS_RUNTIME=controlled` only for credential-free tests and offline demos.
The general CLI still defaults to its older composition. In the connected
runtime, the bounded OW-1 path now suggests a public site for an open intent,
plans one independent DOM `open_search` per intent, and sends it to the browser
worker without requiring a hand-written site configuration. The older
model-planned open chains remain available outside that connected-runtime path.

## Mission Control demo

Run from the repository root with Python 3.11 or newer:

```bash
python -m argus.api
```

Install `argus/requirements.txt` first (FastAPI, Uvicorn, python-dotenv, httpx are
included and pinned to the Ghost API range). Open `http://127.0.0.1:4173`.

`ARGUS_RUNTIME` selects the runtime (ARGUS-3, see
[the plan](../docs/hackathon/ARGUS-3-PLAN.md)):

| Mode | What runs | Needs |
| --- | --- | --- |
| `connected` (default) | Real interpreter, gate and planner; `argus/adapters/dom_toolbox.py` over the DOM worker (which runs Sting's Ghost workflow: lookup, explore or replay, visual continuation, validation, candidate save); `argus/adapters/ghost_bridge.py`; `moderator.Moderator` | `OPENAI_API_KEY`, `ARGUS_MODEL`, `GHOST_API_URL` (health-checked), a loadable worker site config; `STEEL_API_KEY` when `WORKER_BROWSER=steel`, `WORKER_BROWSER_EXECUTABLE` when `local` |
| `controlled` | Offline fixture runtime with keyword routing; runs carry a "Fixture data" badge | nothing |
| `scrape` (was `live`) | Deprecated hand-written three-site scraper in `argus/live_runtime.py`; not the product path | `STEEL_API_KEY` |

The connected runtime refuses to start and names every missing piece; `/api/health`
repeats the mode and the problems and reports `open_world_search: true` in
connected mode. Sessions are worker-owned in this first
connected slice, so the controller's own observe/verify step is unavailable
(`PRECONDITION_FAILED`) until the ARGUS Steel session manager lands.

For an open request with no named domain, connected interpretation makes one
strict structured suggestion call per unresolved intent. ARGUS accepts only the
first syntactically valid candidate allowed by `registry.domain_allowed`, stores
it as `parameters.site_choice` with source `suggested`, and otherwise leaves the
request unresolved so gate rule S1 asks the user. Each accepted open intent is
planned as one independent `open_search` subtask (maximum four), with its query,
string/number parameters, criteria, and expected fields carried into the worker.
The worker enforces read-only controls and hostname-scoped navigation, and its
report persists both the generic checks that ran and explicit limitations for
site-specific checks that could not run.

Local startup for a connected run against the bundled catalog (three terminals):

```bash
GHOST_DATABASE_PATH=ghostapi/ghostapi.sqlite3 python -m uvicorn ghostapi.api.app:app --port 8766
```

```bash
python -m Agents.browser_worker.demo.run --serve-only
```

```bash
ARGUS_RUNTIME=connected GHOST_API_URL=http://127.0.0.1:8766 WORKER_BROWSER=local python -m argus.api
```

`OPENAI_API_KEY`, `ARGUS_MODEL`, `OPENAI_MODEL=gpt-5.4`, `WORKER_BROWSER_EXECUTABLE`
come from `.env`. The catalog server and the Ghost API both default to port 8765, so
Ghost is started on 8766 here. `ARGUS_STORE`
optionally selects the JSON run directory and `GHOST_DATABASE_PATH` selects the
Ghost registry; defaults are `argus-runs/` and `ghostapi/ghostapi.sqlite3`.
The API exposes run submission, history,
snapshots, cancellation, a persisted event log, and an SSE stream.

The submitted natural-language text selects the Shopping, Travel Plan, or Job Search
domain and supplies its parameters and criteria. Shopping recognizes headphones and
keyboards plus their price limits. Travel extracts a one-to-three-day Toronto plan
and supported interests. Job Search extracts remote, salary ranking, and a result
limit up to six. Routing requires both an intent and a supported subject: a request
that only mentions Toronto, or only "remote", or an unsupported product, is rejected
with HTTP 422 and the message "ARGUS could not map this request to a supported demo
task, so it will not guess". It is never rerouted to a different task.

Shopping creates one subtask per requested category and runs independent searches
concurrently. Travel and Job Search run research followed by a dependent detail
subtask. Each uses the controller stages, validation, structured moderator selection,
controller-rendered answers, and persistence.

The live sources are Staples product directories, the Toronto Wikivoyage guide,
and Remotive's software-development jobs. Pages can change, block automation, or
omit comparable prices/salaries; empty or unsupported evidence fails validation
instead of producing a conclusion. Unknown scenarios return HTTP 422 and unknown
runs return 404. Stop the server with Ctrl+C.

`ARGUS_RUNTIME=controlled python -m argus.api` runs the explicit offline fixture.
`/api/health` reports `runtime`, every run snapshot and list entry carries `runtime`
(derived from the persisted `interpreted.model`), and Mission Control labels the
sidebar and each fixture run accordingly.

Connected-mode API additions: `POST /api/runs/{id}/clarifications` with `{"answer"}`
starts a follow-up request for a run that ended `needs_input` (original text plus
`Clarification: <answer>`; the response carries `parent_run_id`);
`GET /api/runs/{id}/evidence/{observation_id}` serves only files stored under that
run's evidence directory, and each snapshot lists them as `evidence_files`.
Qualification is a bounded background job: `POST
/api/workflows/{skill_id}/versions/{version}/qualifications` accepts exactly three
distinct input sets that differ from the named exploration run, returns a job ID,
and `GET /api/workflows/qualifications/{job_id}` reports its result without provider
text. `GET /api/workflows` proxies the configured Ghost registry for Mission Control.
Unsupported or unsafe requests end as `needs_input` or a typed rejection from the
real gate, never as an answer to a different question.
The live worker currently uses deterministic read-only DOM extraction and opens a
fresh worker-owned Steel session per subtask. It does not yet invoke GPT/UI-TARS,
replay a matched Ghost procedure, save a fully parameterized Ghost candidate, or
serve screenshot image files; observation IDs, actions, decisions and handoffs are
still persisted and streamed to Mission Control.

## Two interpreter tiers

`interpreter.interpret(text, request_id, client=None, model=None)` asks for one
JSON document that expresses both tiers, and prefers the first:

1. **Registry tier.** The request maps onto a qualified `registry.OPERATIONS`
   entry: `site_id`, `operation`, parameters with the character span each value
   came from. `Intent.kind` is `"registry"` and phase 1/2 behaviour is unchanged.
2. **Open tier.** Nothing qualified fits, so the intent is `kind="open"` with
   `target_domain`, a plain-language `goal`, parameters with spans,
   `expected_record_shape` and the user's `criteria` as explicit
   `Criterion(text, kind, parameter, span, confidence)` entries where `kind` is
   `rank`, `filter` or `limit`. "best", "cheapest", "top 10" become criteria and
   are never silently dropped.

Grounding rules the module enforces whatever the model returns: every
`text_span` is verified against the raw text, and a span that does not match is
dropped to `source="structured"`, confidence `0`, plus an ambiguity; the same
verification applies to a criterion's span; `target_domain` is only what the
model returned (a hostname may be read out of an explicit URL with
`urllib.parse`, but a site *name* is never turned into a domain in code), and a
malformed hostname becomes `None` with an ambiguity. A model refusal is a status
detected before any content is read and becomes `MODEL_REFUSED`; a truncated or
unparseable answer becomes `EXTRACTION_FAILED`.

## Gate rules

Registry intents are judged by the catalog rules G1–G7, unchanged. Open intents
are judged by the S rules, applied in order, first hit decides. A mixed request
runs the catalog rules first.

| Rule | Decision | Fires when |
| --- | --- | --- |
| S5 (checked first) | reject | the wording asks ARGUS to perform a login, payment, purchase, booking or state-changing submission (`ACTION_CLASS_NOT_ALLOWED`); rejections run before any clarification |
| S1 | clarify | the open intent has no `target_domain`; the question asks which site to use |
| S2 | reject | `registry.domain_allowed` says no; the reason is the policy's own and the run fails with `DOMAIN_NOT_ALLOWED` |
| S3 | clarify | the goal is missing or blank, so there is nothing to look for or validate |
| S4 | clarify | a `rank` criterion is below confidence 0.6; the question is the approved one, with the user's own word substituted |
| S5 | reject | the wording asks for a login, payment, purchase, checkout, form submission or state-changing post; the run fails with `ACTION_CLASS_NOT_ALLOWED` |
| S0 | accept | nothing fired and the request has an open intent |

S4's question is exactly:

> What should 'best' mean? For example, highest salary, remote-only roles, or
> closest match to your experience.

The examples are offered to choose between; the gate never selects one as a
default, and the criterion is kept rather than dropped.

S5 is a **wording preflight, not an action classifier**: a small set of
verb-plus-object patterns with a documentation exemption, so "how do I log in to
X" is accepted as reading and "log in to X and download my invoices" is
rejected. It will miss paraphrases. Execution must refuse those actions
independently; the gate only keeps the obvious cases from opening a session.

## Caps

- **Concurrency: 1–4 workers per run** (`Controller(max_concurrency=2)` by
  default). Four is the local VLM ceiling, because any worker may need vision;
  `5` raises `ValueError: max_concurrency must be at most 4`. This is a per-run
  limit only.
- **Open or mixed plans: at most 4 total subtasks, dependency depth at most 3.**
  `Plan.caps` defaults to `{"max_subtasks": 4, "max_depth": 3}`; a plan that
  raises or malforms its own caps is rejected, and an over-cap plan fails the run
  with `PLAN_TOO_LARGE` before any session opens. Registry-only plans keep their
  previous total-work behaviour.
- One subtask may open ten results sequentially within its action and time
  budget; that is one `open_results` subtask, never one worker per result.

## Domain policy

`registry.DOMAIN_POLICY` plus `registry.domain_allowed(domain) -> (bool, reason)`
is an **offline hostname preflight**. It blocks dedicated login and checkout
hosts and their descendants (`accounts.google.com`, `login.microsoftonline.com`,
`checkout.stripe.com`), not whole providers, so public documentation on those
domains stays eligible. It rejects non-public suffixes (`localhost`, `internal`,
`corp`, `intranet`, `invalid`, `example`, `onion` and the rest of the list),
private, loopback and link-local IP literals, and ambiguous numeric spellings;
anything else is allowed. `example.com` and `jobs.example.com` are allowed — only
the bare `.example` TLD is blocked, which is why the fixtures work.

An eligible hostname is not proof that its resolved address is safe. See
"Pending live toolbox integration" below.

## Planning

`planner.plan` stays deterministic for registry intents: one subtask per intent,
registry defaults filled in, fixed `SUCCESS_CONDITIONS`, empty `depends_on`.

When any intent is open, `planner.plan_open` makes **one bounded model call for
the scheduling only**, through `argus.model_client.ModelClient`. The model
returns steps of `subtask_id`, `intent_index`, `operation` (`search`,
`open_results`, `extract`, `navigate`), `depends_on`, `inputs_from` bindings,
`concurrency_group`, `success_conditions` and `preferred_tool`, and nothing else:
`target_domain`, `goal`, `criteria`, `expected_record_shape`, parameters, output
schema and the caps are copied from the accepted request by code and re-checked
by `validate_plan` (no cycles, depth and size within the caps, bindings that name
declared, earlier dependencies and fields the source actually promises). Registry
intents in a mixed request are still planned deterministically and count toward
the same four-subtask budget.

**Dependency data transfer.** A subtask's `inputs_from` bindings are resolved
from the named dependencies' accepted reports just before dispatch, before its
session is opened. `field="findings"` passes the whole findings object; any other
field is collected from every record of a findings list, in order, or read from a
findings mapping. A missing field, or a dependency with no accepted report, fails
that subtask with `PRECONDITION_FAILED` and cancels its dependents. Resolved
values go into `subtask.parameters` unchanged.

## Validation: generic versus registry

Stage 9 calls `Ghost.validate`. Which checks run depends on `subtask.kind`.

| | Registry subtask | Open subtask |
| --- | --- | --- |
| Call shape | `validate(subtask, records, evidence)` plus, since ARGUS-3, the same `report_context` keyword when the Ghost accepts it | the same plus the keyword `report_context={"run_id", "subtask_id", "evidence", "empty_state"}`, where `evidence` is the report's evidence with any session handle removed |
| Checks (`FakeGhost`) | `records_are_objects`, `title_present`, `price_present`, `currency_usd`, `url_on_site`, `price_within_max` | `records_are_objects`, `records_cite_observations`, `results_present_or_empty_state`, `query_visibly_applied`, `urls_on_target_domain` |
| Completeness | judged by the operation's ground truth | reported as unverified: generic checks cannot tell whether every matching record was collected |

The moderator cannot override a failed validation: a `failed` or `inconclusive`
validation ends the run with `VALIDATION_FAILED` even when every report was
accepted.

Operation-specific checks are only legitimate when a qualified skill was used,
which is why the open path has none. `report_context` is a local ARGUS addition
to the `Ghost.validate` signature and **Sting has not seen it yet**.

## Provenance rules around the moderator

These are controller-side rule checks, not new moderator obligations. The
moderator is handed copies of the reports and records, so editing what it
received changes only its own answer.

- **Reconcile (stage 8) may reorder, drop and supersede, never add or alter.**
  Every record in the returned `findings` must equal, as JSON-normalised data,
  one of the accepted reports' records, and may appear no more often than it
  was supplied. A new record (for example one for `evil.invalid` citing
  `foreign.png`) or a copy with one field changed fails the run with
  `EXTRACTION_FAILED` naming it (`reconciled record 4 is not one of the accepted
  reports' records: title='Evil job', url='https://evil.invalid/x'`) before
  validation runs. `StubModerator`'s merge by `url` passes because the
  superseding record is itself an accepted record.
- **Synthesize (stage 10) is selection, not writing.** The moderator returns
  `AnswerSelection(record_indices, claims, notes)`: indices into the exact
  validated record list in output order, `Claim(record_index, fields)` entries
  that index the selected order, and typed `Note(kind, subject)` entries. It
  returns no record copies, evidence references or prose. The controller
  rejects duplicate or out-of-range record indices; claims that do not cover
  the selection exactly once and in order; missing, duplicate or provenance-only
  fields; records whose own `source_observation_id` is not evidence from this
  run; and note subjects outside the current request's closed set. It derives
  the selected records and renders every `FinalAnswer.lines` entry itself.
  Known legacy prose fields (`text`, `unverified`, nested claim `text`) are
  discarded before strict decoding, and an event records only their field
  paths. The regression case `"is free and cures cancer"` appears in neither
  the result, stored snapshot nor events.

## Request identity and report intake

A run has exactly one request identity. `Controller.run(request, request_id)`
raises `ContractError` (`INVALID_INPUT`) before creating a run when it is given
an `InterpretedRequest` (or its dict) whose `request_id` differs from the
argument; an interpreter or planner that returns another ID fails the run with
the same code. Every `SubtaskInput` carries `request_id` (new in
`0.5-argus-draft`, default `None` so older worker payloads load), and intake accepts a
`WorkerReport` only if its `request_id` is the run's, its `subtask_id` is the
one dispatched, and its `session_handle` is `None` or the lent handle. Anything
else fails the schema check with `EXTRACTION_FAILED`; a foreign handle is never
adopted for verification or cleanup, never closed and never written to an
event. After a run, the store index, `interpreted.request_id`,
`plan.request_id` and every `report.request_id` are the same value.

## The model client

`argus/model_client.py` is the only place in `argus` that touches a vendor SDK.
`ModelClient.parse_json(system, user, output_model, max_tokens) -> ModelResult`
returns one of four statuses — `ok`, `refusal`, `truncated`, `invalid` — and
`parsed` is only ever set for `ok`. `OpenAICompatibleClient` calls
`chat.completions.parse` with the pydantic model as `response_format`, so it
works against OpenAI and against any OpenAI-compatible endpoint (the team's local
server). Callers map the statuses: refusal → `MODEL_REFUSED`, truncated or
invalid → `EXTRACTION_FAILED`.

| Name | Read by | Effect |
| --- | --- | --- |
| `OPENAI_API_KEY` | the `openai` SDK itself, not this code | Credentials for interpretation, connected site suggestion, and model-planned open-world scheduling. Not needed with `--interpreted` (and `--plan-fixture`). |
| `OPENAI_BASE_URL` | the `openai` SDK itself | Points the same call at an OpenAI-compatible endpoint. |
| `ARGUS_MODEL` | `argus.model_client` | Names the model. **There is no default**: without it the run fails with `PRECONDITION_FAILED` rather than quietly calling a model the team did not choose. |

`openai` **2.14.0 is installed on this machine, but no live call has ever been
made from ARGUS**: no key was configured, every test injects a fake client, and
every documented run below is offline. The live path is therefore unverified.

Nothing secret is written to the store: a top-level `session_handle` is stripped
from every report, snapshot and skill before it reaches disk.

Every store read and write takes one re-entrant lock, so in-process readers of
`run.json` never race a writer's `os.replace`. `os.replace` is retried on
`PermissionError` only (up to 50 attempts, 20 ms apart), because on Windows a
reader that still holds the destination open makes the replacement fail; an
*external* reader such as a dashboard process can still trigger retries, and
after the last attempt the write fails loudly with the original
`PermissionError` and the temporary file is removed.

## Entry point

```bash
python3 -m argus "<request text>" [--fake | --no-fake] [--store DIR] \
    [--request-id ID] [--interpreted FILE] [--plan-fixture FILE]
```

Run it from the repository root. `--fake` is the default and wires
`FakeToolbox`, `StubModerator`, `FakeGhost` and `JsonStore`. The terminal
`RunResult` is printed as JSON on stdout; one progress line and the store
location go to stderr. Exit status: `0` succeeded, `1` any other terminal status
(`failed`, `cancelled`, `needs_input`), `2` a usage error, `3` a real toolbox was
asked for.

`--interpreted FILE` skips stage 1 by reading an `InterpretedRequest` from JSON,
so a whole run works offline. Without it, stage 1 calls the model.

`--request-id ID` applies to request text only. It is a usage error (exit `2`)
together with `--interpreted`: the file's own `request_id` is the run's single
request identity, and the controller refuses to run an interpreted request under
another ID.

`--plan-fixture FILE` is **explicit offline planner injection** and requires
`--interpreted`. It reads a `Plan` JSON, wraps it in
`argus.fakes.FakePlannerClient` and injects it through `Controller(plan=...)`.
The fake hands the planner only the plan's *scheduling* fields, converted into
the shape a model's answer arrives in; the request context and the caps still
come from ARGUS and are re-checked. It **never replaces a real model call**:
nothing substitutes it when no client is given, so without the flag an open
request still calls the planner model.

## Modules

| File | Stage | Purpose |
| --- | --- | --- |
| `contracts.py` | all | The messages between stages (`0.5-argus-draft`), including prose-free `AnswerSelection`, structured `Claim`/`Note`, strict JSON round-trips and typed error codes. |
| `interfaces.py` | all | `Toolbox`, `Moderator`, `ProgressObserver`, `Ghost` and `Store` protocols. |
| `model_client.py` | 1, 3 | The single model boundary: `ModelClient`, `ModelResult`, `OpenAICompatibleClient`. |
| `registry.py` | 1-3 | Supported sites, operations, parameters, defaults, parameter validation and the open-world `DOMAIN_POLICY`. |
| `interpreter.py` | 1 | Request text to `InterpretedRequest`, registry tier and open tier. |
| `site_suggestion.py` | 1 | One policy-checked model suggestion for each unresolved connected open intent; failure falls through to gate clarification. |
| `gate.py` | 2 | `accept` / `clarify` / `reject` by rules G1-G7 and S1-S5. |
| `planner.py` | 3 | `InterpretedRequest` to `Plan`: deterministic for registry, one model call for open scheduling, then `validate_plan`. |
| `controller.py` | 3-11 | State machine, match, dispatch, `inputs_from` transfer, intake, reconcile, validate, synthesize, publish. |
| `store.py` | all | `JsonStore`: runs, events, reports, evidence, skills on disk. |
| `fakes.py` | - | `FakeModelClient`, `FakePlannerClient`, `FakeToolbox`, `StubModerator`, `FakeGhost`. |
| `__main__.py` | - | The CLI above. |
| `examples/` | - | JSON fixtures: registry and open interpreted requests, the open plan chain, gate decisions, a worker report, a final answer. |

## Input, output and failures

All output below was copied from the commands as they actually ran on
2026-09-13; run IDs, timestamps and long sections are elided.

### Registry success

`argus/examples/interpreted_request.json` — one intent, `search_products` on
`demo-catalog`, `query="headphones"` and `max_price=150`.

```console
$ python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo
{
  "run_id": "run-fc4f7a2a1fab",
  "status": "succeeded",
  ...
  "answer": {
    "lines": [
      "2 selected records; validation passed.",
      "Record 1 — title: \"Studio headphones\"; price: 129.0; currency: \"USD\"; url: \"https://demo-catalog.invalid/products/0\"; retrieved_at: \"...\".",
      "Record 2 — title: \"Travel headphones\"; price: 79.0; currency: \"USD\"; url: \"https://demo-catalog.invalid/products/1\"; retrieved_at: \"...\"."
    ],
    "claims": [
      {"record_index": 0, "fields": ["title", "price", "currency", "url", "retrieved_at"]},
      {"record_index": 1, "fields": ["title", "price", "currency", "url", "retrieved_at"]}
    ],
    "records": [ ... two product records ... ],
    "failures": [],
    "notes": []
  },
  ...
}
```

Store after that run:

```text
/tmp/argus-demo/index.json
/tmp/argus-demo/runs/run-fc4f7a2a1fab/run.json
/tmp/argus-demo/runs/run-fc4f7a2a1fab/events.jsonl
/tmp/argus-demo/runs/run-fc4f7a2a1fab/reports/subtask-1.json
/tmp/argus-demo/runs/run-fc4f7a2a1fab/evidence/
/tmp/argus-demo/skills/demo-catalog.search-products/v1.json
```

### Open-world clarify: "the best 10 jobs"

```console
$ python3 -m argus --fake --interpreted argus/examples/interpreted_request_open.json --store /tmp/argus-open-a
{
  "run_id": "run-8a41640e10e9",
  "status": "needs_input",
  ...
  "gate": {
    "decision": "clarify",
    "rule_id": "S4",
    "reason": "The ranking 'best' is too vague to order results by (confidence 0.4), and it is kept rather than dropped, so it has to be asked about.",
    "questions": [
      "What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience."
    ]
  },
  "plan": null,
  "reports": [],
  "validation": null,
  "answer": {
    "lines": ["What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience."],
    "claims": [], "records": [], "failures": [],
    "notes": [{"kind": "clarification_required", "subject": "gate"}]
  },
  "metrics": { ..., "sessions_opened": 0, "subtasks": {} },
  "error": {"code": "NEEDS_INPUT", "message": "The ranking 'best' is too vague ...", "retryable": true, ...}
}
run run-8a41640e10e9: needs_input | NEEDS_INPUT: ... | stored in /tmp/argus-open-a/runs/run-8a41640e10e9
$ echo $?
1
```

Nothing executed: the store holds `run.json` and `events.jsonl` and no report.

### Open-world success: the clarified request as a chain

`interpreted_request_open_salary.json` ("highest salary" rank, "remote only"
filter, limit 10, target `jobs.example.com`) planned by
`plan_open_chain.json` through `FakePlannerClient`: `subtask-open-search`, then
`subtask-open-details` opening each result URL in order.

```console
$ python3 -m argus --fake --interpreted argus/examples/interpreted_request_open_salary.json \
    --plan-fixture argus/examples/plan_open_chain.json --store /tmp/argus-open-b
{
  "run_id": "run-cad73f46b1a6",
  "status": "succeeded",
  ...
  "answer": {
    "lines": [
      "4 selected records; validation passed.",
      "Record 1 — title: \"Staff Software Engineer\"; company: \"Fabrikam\"; url: \"https://jobs.example.com/jobs/3\"; salary: 210000; remote: true.",
      "... three more controller-rendered record lines ..."
    ],
    "claims": [
      {"record_index": 0, "fields": ["title", "company", "url", "salary", "remote"]},
      {"record_index": 1, "fields": ["title", "company", "url", "salary", "remote"]},
      {"record_index": 2, "fields": ["title", "company", "url", "salary", "remote"]},
      {"record_index": 3, "fields": ["title", "company", "url", "salary", "remote"]}
    ],
    "records": [ ... the same four, unique by url ... ],
    "failures": [], "notes": []
  },
  "metrics": {"elapsed_seconds": 0.081, "browser_action_count": 13, "moderator_calls": 4, "sessions_opened": 2,
              "subtasks": {"subtask-open-search": "accepted", "subtask-open-details": "accepted"}, ...},
  "error": null
}
$ echo $?
0
```

The six searched jobs become four answered records: reconciliation merges the
two reports by `url` (the detail record supersedes the search record for the
same job), then synthesis applies the criteria — `remote only`, then `highest
salary` descending, then the limit of ten. Each claim cites the observation of
the page the record was read from. The store holds one report per subtask
(`reports/subtask-open-search.json`, `reports/subtask-open-details.json`).

### Open-world reject: a private target

`/tmp/open-blocked.json` is `interpreted_request_open.json` with its
`target_domain` set to `127.0.0.1`; the fixtures on disk stay public.

```console
$ python3 -m argus --fake --interpreted /tmp/open-blocked.json --store /tmp/argus-open-c
{
  "status": "failed",
  "gate": {
    "decision": "reject",
    "rule_id": "S2",
    "reason": "Target domain '127.0.0.1' is blocked: non-public IP address (DOMAIN_NOT_ALLOWED). ARGUS browses public sites only.",
    "questions": []
  },
  "error": {
    "code": "DOMAIN_NOT_ALLOWED",
    "message": "S2: Target domain '127.0.0.1' is blocked: non-public IP address (DOMAIN_NOT_ALLOWED). ARGUS browses public sites only.",
    "retryable": false, "step_id": null, "evidence_refs": []
  }
}
```

`intranet.corp` fails the same way ("non-public hostname"). No session is
opened; `controller.GATE_REJECT_CODES` maps S2 to `DOMAIN_NOT_ALLOWED` and S5 to
`ACTION_CLASS_NOT_ALLOWED`, while every other rejection stays `INVALID_INPUT`.

### Other failures

- **A plan over the cap.** A five-step plan fails the run with `PLAN_TOO_LARGE`
  at stage 3; the toolbox is never called and no session is opened
  (`OpenPlanTooLargeTest`).
- **A sign-in wall.** `FakeToolbox(script={"subtask-1": "auth_required"})`:
  `AUTH_REQUIRED` is terminal, so there is no retry, the worker's typed failure
  is carried into the result unchanged, and the answer says so instead of
  inventing records.
- **A missing required parameter.** G4 clarifies before anything runs, exactly
  like S4 above.
- **Live interpretation with no model named.**

  ```console
  $ python3 -m argus "Find headphones under \$150 in the demo catalog"
  {
    "status": "failed",
    "error": {
      "code": "PRECONDITION_FAILED",
      "message": "no model was named for the call: pass model= or set $ARGUS_MODEL. There is no default model, so that a run never quietly calls one the team did not choose.",
      "retryable": false, "step_id": null, "evidence_refs": []
    },
    "interpreted": null, ...
  }
  ```

- **Asking for a real run.**

  ```console
  $ python3 -m argus --no-fake "find headphones"
  No real toolbox, moderator or Ghost is connected yet, so there is nothing to run without --fake.
  ...
  $ echo $?
  3
  ```

## Run and test

```bash
# the whole suite
python3 -m unittest discover -s argus/tests -v

# one offline registry run against the fakes
python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo

# the open-world clarify (exits 1) and the clarified chain (exits 0)
python3 -m argus --fake --interpreted argus/examples/interpreted_request_open.json --store /tmp/argus-open-a
python3 -m argus --fake --interpreted argus/examples/interpreted_request_open_salary.json \
    --plan-fixture argus/examples/plan_open_chain.json --store /tmp/argus-open-b
```

On 2026-09-12, after the review fixes (provenance, request identity, budget
cutoff, store retry), the suite ran **451 tests in 1.155s, OK** on Python 3.13.5
without `PYTHONUTF8` set (contracts, model client, interpreter and planner with
injected fake clients, gate, domain policy, controller, store, fakes and the
end-to-end runs in `tests/test_end_to_end.py`). No test touches the network.
The three CLI commands above ran with the exit statuses and output shown.

## Dependencies

- Python 3.12 or 3.13 (developed on 3.13.5; CI runs both). The controller,
  store, gate, contracts, registry and fakes are standard library only.
- `argus/requirements.txt` (`openai`, `pydantic`) is required to run the
  interpreter, the open-world planner and therefore the test suite: the output
  schemas are real pydantic models even when a fake model client is injected.
  Install with `python3 -m pip install -r argus/requirements.txt`. Verified with
  `openai` 2.14.0 and `pydantic` 2.12.5. The first live interpretation call was
  made on 2026-09-12 (`python3 -m argus.live_check`); no live planning call yet.

## Known limitations

- **Fakes only.** No Steel, no browser, no real page. `FakeToolbox` answers
  registry subtasks from a five-row catalog on `https://demo-catalog.invalid` and
  open subtasks from a seven-row job dataset keyed by `target_domain`
  (`jobs.example.com`); observation IDs name no file, so
  `runs/<run_id>/evidence/` stays empty.
- **The moderator is a stub.** `StubModerator` judges by rules (succeeded plus
  some evidence means accept), reconciles by merging records on `url`, and
  selects records/fields after applying the criteria they actually carry. An
  unapplied criterion becomes a typed `criterion_not_applied` note whose subject
  is the criterion's controller-generated ID. A run driven by it proves the
  controller's plumbing, not that a selection is useful.
- **Ghost is a fake.** `FakeGhost.match` always explores, so the reuse path and
  qualification are never exercised; validation passes vacuously when there are
  no records; `compile` writes a candidate skill whose `skill_id` is derived from
  site and operation, so two subtasks of one run can overwrite each other's
  `v1.json`.
- **Open-world completeness is never verified.** The generic checks cannot tell
  whether every matching record was found; that is stated in the validation
  report and carried into the answer.
- **S5 is wording, not behaviour** (see above), and `domain_allowed` is a
  hostname preflight, not an address check.
- `clarify` ends the run; there is no resume after the user answers.
- A `reconcile` decision of `verify` becomes a generic typed
  `reconciliation_unresolved` note; its model-written reason remains internal
  and is not rendered. Only intake verification runs, at most once per subtask.
- **A hung worker thread can outlive the run.** `max_seconds` bounds the run:
  when the deadline passes with workers still inside `run_subtask`, each running
  subtask is failed with `BUDGET_EXCEEDED`, its lent session is closed through
  the toolbox (the transport-level cutoff), the pool is shut down without
  waiting, and the failed result is written. A report that arrives afterwards
  is ignored and logged (`late_report_ignored`, kept in memory and on the
  `argus.controller` logger, never in `events.jsonl` after the terminal event
  and never stored as the run's report). The thread itself cannot be killed and
  is joined at interpreter exit, so toolbox adapters must honour
  `Budget.max_seconds` themselves for a clean stop.

## Open-world limitations

OW-1 is a bounded DOM-first read-only search, not unrestricted web browsing.
Offline tests cover suggestion policy, planning, contract translation, generic
browser actions, extraction provenance, verification and controller persistence.
No paid-model, public-site, Steel, DNS-rebinding or Ghost qualification check was
run for this slice. Remaining limits include:

- **Address enforcement at the transport.** DNS result checks, the connected
  destination and DNS rebinding. ARGUS has an offline hostname preflight and the
  worker restricts navigation/redirect hostnames and methods, but this is not an
  OS network sandbox.
- **Unconfigured-site semantics.** Only generic DOM roles are available. The
  worker proves the query fill, output schema, provenance, approved links,
  freshness and current run; it does not prove ranking quality, non-query filter
  application, exhaustive coverage, pagination or cross-page completeness.
- **Page technology.** Iframes, shadow DOM, canvas-only content and sites that
  require POST search, authentication, downloads or transactional submissions
  remain unsupported. The zero-result decision uses a bounded visible-text
  heuristic (`no results` or `0 results`).
- **Shared four-slot VLM arbitration across runs and callers.** The per-run
  `max_concurrency` cap does not stop several controllers from overloading the
  machine's four local VLM slots.
- **Live integration evidence.** Public pages can change or block automation;
  open-site Ghost compilation/qualification and receiver behaviour have not
  been verified against live services.

## For the teammates connecting to this

Implement the protocols in `interfaces.py` and nothing else changes:
`Toolbox` (Thomas, Tianqi), `Moderator` (Thomas), `Ghost` (Sting). The messages
they exchange are in `contracts.py` with one JSON fixture each in `examples/`.
`WorkerReport` is Thomas's `Agents/visual/examples/hn-top-story/report.json`
format verbatim, plus the optional `session_handle` and `typed_failures`.
