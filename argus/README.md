# ARGUS controller

Takes one request in plain language, decides whether it may run, plans it into
subtasks, dispatches them to browser workers, judges and validates what comes
back, and publishes exactly one terminal result with every claim cited to an
observation from that run. The controller owns run state, stage transitions,
budgets, browser sessions, the event stream and the terminal result; the
moderator, the toolbox and Ghost are called through protocols and only return
decisions.

Design: [docs/hackathon/ARGUS.md](../docs/hackathon/ARGUS.md) (stages and the
controller/moderator boundary) and
[docs/hackathon/ARGUS-IMPLEMENTATION.md](../docs/hackathon/ARGUS-IMPLEMENTATION.md)
(phases and prompts).

**Status: the controller is real; everything it calls out through is a fake.**
No Steel session, no moderator model call and no Ghost service is connected.
Records, screenshots, session handles and skills produced here are synthetic.

## Entry point

```bash
python3 -m argus "<request text>" [--fake | --no-fake] [--store DIR] \
    [--request-id ID] [--interpreted FILE]
```

Run it from the repository root. `--fake` is the default and wires
`FakeToolbox`, `StubModerator`, `FakeGhost` and `JsonStore`. The terminal
`RunResult` is printed as JSON on stdout; one progress line and the store
location go to stderr. Exit status: `0` succeeded, `1` any other terminal status
(`failed`, `cancelled`, `needs_input`), `2` a usage error, `3` a real toolbox was
asked for.

`--interpreted FILE` skips stage 1 by reading an `InterpretedRequest` from JSON,
so a whole run works offline. Without it, stage 1 calls the model.

## Environment variables

| Name | Used by | Effect |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | `argus.interpreter` via the `anthropic` SDK | Credentials for the interpretation call. Not needed with `--interpreted`. |
| `ARGUS_MODEL` | `argus.interpreter` | Overrides the interpretation model; default `claude-opus-5`. |

No other variable is read, and nothing secret is written to the store: a
top-level `session_handle` is stripped from every report, snapshot and skill
before it reaches disk.

## Modules

| File | Stage | Purpose |
| --- | --- | --- |
| `contracts.py` | all | The messages between stages, with strict JSON round-trips and the typed error codes. |
| `interfaces.py` | all | `Toolbox`, `Moderator`, `ProgressObserver`, `Ghost` and `Store` protocols. |
| `registry.py` | 1-3 | Supported sites, operations, parameters, defaults and parameter validation. |
| `interpreter.py` | 1 | Request text to `InterpretedRequest` (model-backed). |
| `gate.py` | 2 | `accept` / `clarify` / `reject` by rules G1-G7. |
| `planner.py` | 3 | `InterpretedRequest` to `Plan`, deterministic. |
| `controller.py` | 3-11 | State machine, match, dispatch, intake, reconcile, validate, synthesize, publish. |
| `store.py` | all | `JsonStore`: runs, events, reports, evidence, skills on disk. |
| `fakes.py` | - | `FakeToolbox`, `StubModerator`, `FakeGhost`. |
| `__main__.py` | - | The CLI above. |
| `examples/` | - | One JSON fixture per message; the shared handoff samples. |

## Input, output and failures

All output below is copied from the commands as they actually ran on
2026-09-12; long sections are elided with `...`.

### Input

`argus/examples/interpreted_request.json` — one intent, `search_products` on
`demo-catalog`, `query="headphones"` and `max_price=150`, each tied to the
characters of `"Find headphones under $150 in the demo catalog"` it came from.

### Success

```console
$ python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo
{
  "run_id": "run-a8be48448167",
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
  "metrics": {
    "elapsed_seconds": 0.005,
    "browser_action_count": 4,
    "moderator_calls": 2,
    "sessions_opened": 1,
    "subtasks": {"subtask-1": "accepted"},
    "max_actions": 30,
    "max_seconds": 120.0
  },
  "error": null
}
run run-a8be48448167: succeeded | stored in /tmp/argus-demo/runs/run-a8be48448167
```

Store after that run:

```text
/tmp/argus-demo/index.json
/tmp/argus-demo/runs/run-a8be48448167/run.json
/tmp/argus-demo/runs/run-a8be48448167/events.jsonl
/tmp/argus-demo/runs/run-a8be48448167/reports/subtask-1.json
/tmp/argus-demo/runs/run-a8be48448167/evidence/
/tmp/argus-demo/skills/demo-catalog.search-products/v1.json
```

### Failure: the worker hits a sign-in wall

From `AuthRequiredTest` (`FakeToolbox(script={"subtask-1": "auth_required"})`).
`AUTH_REQUIRED` is terminal, so there is no retry; the worker's typed failure is
carried into the result unchanged and the answer says so instead of inventing
records.

```json
{
  "status": "failed",
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "the catalog asked for a sign-in before showing results",
    "retryable": false,
    "step_id": "step-002",
    "evidence_refs": ["observation-000.png"]
  },
  "answer": {
    "text": "No cited record for 'Find headphones under $150 in the demo catalog'. 1 subtask failure(s) are reported unchanged.",
    "claims": [],
    "records": [],
    "failures": [{"code": "AUTH_REQUIRED", "message": "the catalog asked for a sign-in before showing results", "retryable": false, "step_id": "step-002", "evidence_refs": ["observation-000.png"]}],
    "unverified": [
      "Validation status is 'inconclusive', not 'passed'.",
      "AUTH_REQUIRED: the catalog asked for a sign-in before showing results"
    ]
  }
}
```

### Failure: the request is not runnable (clarify)

From `ClarifyTest`, a request with no `query`. The gate stops the run before any
session is opened; status is `needs_input` and the questions are the answer.

```json
{
  "status": "needs_input",
  "gate": {
    "decision": "clarify",
    "rule_id": "G4",
    "reason": "The request is missing required parameter 'query' for search_products.",
    "questions": ["What product should I search the demo catalog for?"]
  },
  "error": {"code": "NEEDS_INPUT", "message": "The request is missing required parameter 'query' for search_products.", "retryable": true, "step_id": null, "evidence_refs": []},
  "plan": null
}
```

### Failure: live interpretation without the SDK

```console
$ python3 -m argus "Find headphones under \$150 in the demo catalog"
{
  "status": "failed",
  "error": {
    "code": "PRECONDITION_FAILED",
    "message": "interpretation needs the anthropic SDK: pip install anthropic",
    "retryable": false, "step_id": null, "evidence_refs": []
  },
  "interpreted": null, ...
}
```

### Failure: asking for a real run

```console
$ python3 -m argus --no-fake "find headphones"
No real toolbox, moderator or Ghost is connected yet, so there is nothing to run without --fake.
  toolbox   Thomas and Tianqi (Steel sessions, dom_interpret, vision_interpret)
  moderator Thomas (assess_report, reconcile, synthesize)
  ghost     Sting (match, validate, compile)
Connecting them is phase 3 of docs/hackathon/ARGUS-IMPLEMENTATION.md.
Run the same request against the fakes with --fake (the default).
$ echo $?
3
```

## Run and test

```bash
# one offline run against the fakes
python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo

# the whole suite
python3 -m unittest discover -s argus/tests -v
```

On 2026-09-12 the suite ran **230 tests, OK**, on Python 3.13.5 (contracts,
interpreter with an injected fake client, gate, planner, controller, store,
fakes, and the end-to-end runs in `tests/test_end_to_end.py`). No test touches
the network.

## Dependencies

- Python 3.13 (developed on 3.13.5). The controller, store, planner, gate,
  contracts and fakes are standard library only.
- Live interpretation additionally needs the `anthropic` SDK and `pydantic`.
  **`anthropic` is not installed on this machine** (`pydantic` 2.12.5 is), so no
  version is pinned here yet and the live path has never been executed. It fails
  as the honest typed failure shown above; every test and the documented CLI run
  use `--interpreted` or an injected fake client instead.

## Known limitations

- **Fakes only.** No Steel, no browser, no real page. `FakeToolbox` answers from
  a five-row in-memory catalog on `https://demo-catalog.invalid`; observation IDs
  name no file, so `runs/<run_id>/evidence/` stays empty.
- **The moderator is a stub.** `StubModerator` judges by rules (succeeded plus
  some evidence means accept), not by reading the page or the records. A run
  driven by it proves the controller's plumbing, not that an answer is any good.
  Thomas's `assess_report` / `reconcile` / `synthesize` replace it unchanged in
  signature.
- **Ghost is a fake.** `FakeGhost.match` always explores, so the reuse path and
  qualification are never exercised; `validate` checks the fixture's own ground
  truth, and passes vacuously when there are no records; `compile` writes a
  candidate skill whose `skill_id` is derived from site and operation, so two
  subtasks of one run overwrite each other's `v1.json`.
- **The planner is deterministic** and has no dependent chains: it emits one
  subtask per intent with empty `depends_on`. The controller honours
  `depends_on` (and cancels dependents of a failed subtask), but nothing
  produces it yet.
- **One site, one operation.** `registry.py` knows `demo-catalog` and
  `search_products`. Anything else is rejected by the gate, by design.
- `clarify` ends the run; there is no resume after the user answers, and a new
  request is needed.
- A `reconcile` decision of `verify` is recorded in `answer.unverified`, not
  executed. Only intake verification runs, at most once per subtask.
- `SubtaskInput` carries `run_id` but no `request_id`, so the fake worker fills
  `WorkerReport.request_id` with the run ID. A real worker should be handed the
  request ID when that contract is settled at INT-1.
- Budgets are enforced between calls: a subtask already inside `run_subtask`
  cannot be interrupted, so a run that breaches `max_seconds` waits for the
  in-flight worker and then discards its report.

## For the teammates connecting to this

Implement the protocols in `interfaces.py` and nothing else changes:
`Toolbox` (Thomas, Tianqi), `Moderator` (Thomas), `Ghost` (Sting). The messages
they exchange are in `contracts.py` with one JSON fixture each in `examples/`.
`WorkerReport` is Thomas's `workers/visual/examples/hn-top-story/report.json`
format verbatim, plus the optional `session_handle` and `typed_failures`.
