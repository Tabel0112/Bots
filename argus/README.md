# ARGUS controller

Takes one request in plain language, decides whether it may run, plans it into
subtasks, dispatches them to browser workers, judges and validates what comes
back, and publishes exactly one terminal result with every claim cited to an
observation from that run. The controller owns run state, stage transitions,
budgets, browser sessions, the event stream and the terminal result; the
moderator, the toolbox and Ghost are called through protocols and only return
decisions.

Design: [docs/hackathon/ARGUS.md](../docs/hackathon/ARGUS.md) (stages, the
open-world delta and the controller/moderator boundary) and
[docs/hackathon/ARGUS-IMPLEMENTATION.md](../docs/hackathon/ARGUS-IMPLEMENTATION.md)
(phases and prompts).

**Status: the controller is real; everything it calls out through is a fake.**
No Steel session, no live model call and no Ghost service is connected. Records,
screenshots, session handles and skills produced here are synthetic. Phase 1b
(open-world navigation) is implemented offline end to end: two interpreter
tiers, the S rules, model-planned chains through the model-client boundary,
dependency data transfer and generic validation.

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
| Call shape | `validate(subtask, records, evidence)` | the same plus the keyword `report_context={"run_id", "subtask_id", "evidence", "empty_state"}`, where `evidence` is the report's evidence with any session handle removed |
| Checks (`FakeGhost`) | `records_are_objects`, `title_present`, `price_present`, `currency_usd`, `url_on_site`, `price_within_max` | `records_are_objects`, `records_cite_observations`, `results_present_or_empty_state`, `query_visibly_applied`, `urls_on_target_domain` |
| Completeness | judged by the operation's ground truth | reported as unverified: generic checks cannot tell whether every matching record was collected |

The moderator cannot override a failed validation: a `failed` or `inconclusive`
validation ends the run with `VALIDATION_FAILED` even when every report was
accepted.

Operation-specific checks are only legitimate when a qualified skill was used,
which is why the open path has none. `report_context` is a local ARGUS addition
to the `Ghost.validate` signature and **Sting has not seen it yet**.

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
| `OPENAI_API_KEY` | the `openai` SDK itself, not this code | Credentials for interpretation and open-world planning. Not needed with `--interpreted` (and `--plan-fixture`). |
| `OPENAI_BASE_URL` | the `openai` SDK itself | Points the same call at an OpenAI-compatible endpoint. |
| `ARGUS_MODEL` | `argus.model_client` | Names the model. **There is no default**: without it the run fails with `PRECONDITION_FAILED` rather than quietly calling a model the team did not choose. |

`openai` **2.14.0 is installed on this machine, but no live call has ever been
made from ARGUS**: no key was configured, every test injects a fake client, and
every documented run below is offline. The live path is therefore unverified.

Nothing secret is written to the store: a top-level `session_handle` is stripped
from every report, snapshot and skill before it reaches disk.

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
| `contracts.py` | all | The messages between stages (`0.3-argus-draft`), with strict JSON round-trips and the typed error codes. |
| `interfaces.py` | all | `Toolbox`, `Moderator`, `ProgressObserver`, `Ghost` and `Store` protocols. |
| `model_client.py` | 1, 3 | The single model boundary: `ModelClient`, `ModelResult`, `OpenAICompatibleClient`. |
| `registry.py` | 1-3 | Supported sites, operations, parameters, defaults, parameter validation and the open-world `DOMAIN_POLICY`. |
| `interpreter.py` | 1 | Request text to `InterpretedRequest`, registry tier and open tier. |
| `gate.py` | 2 | `accept` / `clarify` / `reject` by rules G1-G7 and S1-S5. |
| `planner.py` | 3 | `InterpretedRequest` to `Plan`: deterministic for registry, one model call for open scheduling, then `validate_plan`. |
| `controller.py` | 3-11 | State machine, match, dispatch, `inputs_from` transfer, intake, reconcile, validate, synthesize, publish. |
| `store.py` | all | `JsonStore`: runs, events, reports, evidence, skills on disk. |
| `fakes.py` | - | `FakeModelClient`, `FakePlannerClient`, `FakeToolbox`, `StubModerator`, `FakeGhost`. |
| `__main__.py` | - | The CLI above. |
| `examples/` | - | JSON fixtures: registry and open interpreted requests, the open plan chain, gate decisions, a worker report, a final answer. |

## Input, output and failures

All output below was copied from the commands as they actually ran on
2026-09-12; long sections are elided with `...`.

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
    "text": "2 record(s) for 'Find headphones under $150 in the demo catalog', each cited to an observation from this run.",
    "claims": [
      {
        "text": "Studio headphones costs 129.0 USD at https://demo-catalog.invalid/products/0.",
        "evidence_refs": ["observation-000.png"]
      },
      {
        "text": "Travel headphones costs 79.0 USD at https://demo-catalog.invalid/products/1.",
        "evidence_refs": ["observation-000.png"]
      }
    ],
    "records": [ ... two product records ... ],
    "failures": [],
    "unverified": []
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
    "text": "What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience.",
    "claims": [], "records": [], "failures": [],
    "unverified": ["What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience."]
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
    "text": "4 record(s) for 'Find the highest salary 10 software engineering jobs, remote only, on jobs.example.com', each cited to an observation from this run.",
    "claims": [
      {"text": "Staff Software Engineer: company Fabrikam, url https://jobs.example.com/jobs/3, salary 210000, remote True.", "evidence_refs": ["observation-004.png"]},
      {"text": "Senior Software Engineer: company Northwind, url https://jobs.example.com/jobs/1, salary 185000, remote True.", "evidence_refs": ["observation-002.png"]},
      {"text": "Site Reliability Engineer: company Contoso, url https://jobs.example.com/jobs/6, salary 160000, remote True.", "evidence_refs": ["observation-007.png"]},
      {"text": "Frontend Software Engineer: company Adatum, url https://jobs.example.com/jobs/5, salary 132000, remote True.", "evidence_refs": ["observation-006.png"]}
    ],
    "records": [ ... the same four, unique by url ... ],
    "failures": [], "unverified": []
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

On 2026-09-12, after phase 1b integration, the suite ran **419 tests in 0.880s,
OK** on Python 3.13.5 (contracts, model client, interpreter and planner with
injected fake clients, gate, domain policy, controller, store, fakes and the
end-to-end runs in `tests/test_end_to_end.py`). No test touches the network.
The three CLI commands above ran with the exit statuses and output shown.

## Dependencies

- Python 3.13 (developed on 3.13.5). The controller, store, planner, gate,
  contracts, registry and fakes are standard library only.
- Live interpretation and live open-world planning need the `openai` package
  (which brings `pydantic`). `openai` **2.14.0** and `pydantic` 2.12.5 are
  installed here; `pydantic` is also what `planner._output_model` and the
  interpreter's output model need, so `--plan-fixture` and the injected fake
  clients still build a real schema. No live call has been made.

## Known limitations

- **Fakes only.** No Steel, no browser, no real page. `FakeToolbox` answers
  registry subtasks from a five-row catalog on `https://demo-catalog.invalid` and
  open subtasks from a seven-row job dataset keyed by `target_domain`
  (`jobs.example.com`); observation IDs name no file, so
  `runs/<run_id>/evidence/` stays empty.
- **The moderator is a stub.** `StubModerator` judges by rules (succeeded plus
  some evidence means accept), reconciles by merging records on `url`, and
  synthesises by applying the criteria the records actually carry — naming in
  `unverified` any criterion whose field is absent. A run driven by it proves the
  controller's plumbing, not that an answer is any good.
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
- A `reconcile` decision of `verify` is recorded in `answer.unverified`, not
  executed. Only intake verification runs, at most once per subtask.
- `SubtaskInput` carries `run_id` but no `request_id`, so the fake worker fills
  `WorkerReport.request_id` with the run ID. INT-1 should settle this.
- Budgets are enforced between calls: a subtask already inside `run_subtask`
  cannot be interrupted, so a run that breaches `max_seconds` waits for the
  in-flight worker and then discards its report.

## Pending live toolbox integration

These are required before any real open-world run and none of them exists here:

- **Address enforcement at the transport.** DNS result checks, the connected
  destination, redirect hops, DNS rebinding and every subsequent request. The
  offline `domain_allowed` preflight is the only part ARGUS has.
- **Execution-time action blocking** for login, payment and state-changing
  submissions, independent of the S5 wording preflight.
- **Shared four-slot VLM arbitration across runs and callers.** The per-run
  `max_concurrency` cap does not stop several controllers from overloading the
  machine's four local VLM slots.
- **Real Steel sessions** in place of `FakeToolbox` handles.
- **Thomas's moderator** replacing `StubModerator` behind the same three
  callables.
- **Sting's Ghost receiver verification**, including the `report_context`
  keyword now passed to `Ghost.validate` for open subtasks — an ARGUS-local
  addition Sting has not seen or agreed.

## For the teammates connecting to this

Implement the protocols in `interfaces.py` and nothing else changes:
`Toolbox` (Thomas, Tianqi), `Moderator` (Thomas), `Ghost` (Sting). The messages
they exchange are in `contracts.py` with one JSON fixture each in `examples/`.
`WorkerReport` is Thomas's `workers/visual/examples/hn-top-story/report.json`
format verbatim, plus the optional `session_handle` and `typed_failures`.
