# ARGUS controller — implementation plan

Status: plan for Abel's review, written 2026-09-12. Scope is the ARGUS base only: everything runs end to end against fakes. Connecting Thomas's toolbox and moderator and Sting's Ghost is a later phase and is not in these prompts.

Decisions this plan assumes (all recorded in [DECISIONS.md](../ai/DECISIONS.md) or [ARGUS.md](ARGUS.md)): Python backend, JSON storage, sequential stages with concurrency only between subagents, ARGUS owns browser sessions, the moderator is Thomas's separate deliverable called through three callables, natural-language interpretation is in the MVP.


> **Hardware limit:** at most 4 subagents may run at once. Our machine cannot handle more. `max_concurrency` must stay at or below 4, and the open-world `caps.max_subtasks` must respect it. Look through the docs and change this later if the hardware changes.

## Package layout

```text
argus/
  __init__.py
  __main__.py          python -m argus "<request text>"  (phase 2)
  contracts.py         dataclasses + JSON round-trip + error codes   (phase 0)
  interfaces.py        Toolbox, Moderator, Ghost protocols            (phase 0)
  registry.py          supported sites/operations/parameters          (phase 0)
  examples/            JSON fixtures: worker report, interpreted request, plan, decisions (phase 0)
  interpreter.py       text -> InterpretedRequest (model-backed)      (A)
  gate.py              accept / clarify / reject rules                (A)
  planner.py           InterpretedRequest -> Plan (deterministic)     (B)
  controller.py        state machine, budgets, dispatch, sessions, events (B)
  store.py             JSON storage for runs, events, reports, skills (C)
  fakes.py             FakeToolbox, StubModerator, FakeGhost           (D)
  tests/               one test module per workstream
  README.md            (phase 2)
```

## Phases and parallelism

| Phase | Work | Depends on | Parallel? | Model | Reasoning |
| --- | --- | --- | --- | --- | --- |
| 0 | Contracts, interfaces, registry, fixtures | Thomas's `workers/visual/examples/hn-top-story/report.json`, Sting's `ghostapi/demo/ghost_demo.py` names | No. Everything else imports it | Opus 5 | high |
| A | Interpreter + gate | Phase 0 | Yes, with B, C, D | Opus 5 | high |
| B | Planner + controller | Phase 0 | Yes, with A, C, D | Fable 5.1 (state machine, concurrency and session cleanup are where subtle bugs hide) | xhigh |
| C | JSON store | Phase 0 | Yes, with A, B, D | Opus 5 | medium |
| D | Fakes and fixture-driven behaviors | Phase 0 | Yes, with A, B, C | Opus 5 | medium |
| 2 | Integration: CLI, end-to-end tests, README, TEAM update | A, B, C, D | No | Opus 5 | high |
| 3 | Connections to real toolbox, moderator, Ghost | Teammates | Later | Not planned here | — |

Model choice: Sonnet is not used anywhere. Fable is reserved for the controller because that is the only workstream where a subtle mistake is expensive to find later; everywhere else the written spec already decides the design and Opus is sufficient. The runtime model inside the interpreter is separate and defaults to `claude-opus-5`.

Rules for running A to D concurrently in one working tree:

- Each workstream creates only the files listed in its prompt plus its own test module. Phase 0 files are read-only for them.
- If a workstream needs a contract change, it writes the proposed change into its handoff note instead of editing `contracts.py`.
- No workstream commits. Abel reviews and commits.
- Tests must not call the network. The interpreter's tests use an injected fake client.

## Phase 0 spec — contracts

`argus/contracts.py`. Plain dataclasses with `to_dict()` and `from_dict()`, schema version `"0.2-argus-draft"`. No third-party dependencies.

- `TypedError`: `code`, `message`, `retryable`, `step_id`, `evidence_refs`. Codes: `NO_MATCH`, `INVALID_INPUT`, `NEEDS_INPUT`, `TARGET_NOT_FOUND`, `TARGET_AMBIGUOUS`, `PRECONDITION_FAILED`, `NAVIGATION_TIMEOUT`, `AUTH_REQUIRED`, `EXTRACTION_FAILED`, `VALIDATION_FAILED`, `UNSUPPORTED_CHANGE`, `BUDGET_EXCEEDED`, `CANCELLED`, `MODEL_REFUSED`.
- `ParameterOrigin`: `value`, `source` (`text_span` | `default` | `structured`), `span` (start, end) or null, `confidence`.
- `Intent`: `site_id`, `operation`, `parameters: dict[str, ParameterOrigin]`, `confidence`.
- `InterpretedRequest`: `request_id`, `raw_text`, `intents: list[Intent]`, `missing_required: list[{intent_index, parameter, question}]`, `ambiguities: list[str]`, `model`, `interpreted_at`.
- `GateDecision`: `decision` (`accept` | `clarify` | `reject`), `rule_id`, `reason`, `questions: list[str]`.
- `Subtask`: `subtask_id`, `intent_index`, `site_id`, `operation`, `parameters` (plain values), `depends_on: list[subtask_id]`, `concurrency_group: str`, `success_conditions: list[str]`, `output_schema_id`, `preferred_tool` (`dom` | `vision`).
- `Plan`: `plan_id`, `request_id`, `subtasks`, `created_at`.
- `SubtaskInput`: `run_id`, `subtask`, `session_handle`, `budget: {max_actions, max_seconds}`, `mode` (`explore` | `reuse`), `bound_procedure` (nullable).
- `WorkerReport`: adopt Thomas's `report.json` fields verbatim: `schema_version`, `worker`, `worker_model`, `request_id`, `subtask_id`, `subtask`, `outcome`, `summary`, `findings`, `actions[]`, `evidence{}`, `metrics{}`, `failures[]`. Add `session_handle` (returned) and `typed_failures: list[TypedError]`. Keep `failures` as he defines it.
- `ModeratorDecision`: `stage` (`assess` | `reconcile` | `synthesize`), `decision`, `reason`, `evidence_refs`, `next_action` (nullable dict).
- `FinalAnswer`: `text`, `claims: list[{text, evidence_refs}]`, `records`, `failures: list[TypedError]`, `unverified: list[str]`.
- `Event`: `run_id`, `sequence`, `timestamp`, `type`, `stage`, `message`, `data`.
- `RunResult`: `run_id`, `status` (`succeeded` | `failed` | `cancelled` | `needs_input`), `interpreted`, `gate`, `plan`, `reports`, `validation`, `answer`, `metrics`, `error`.

`argus/interfaces.py`. `typing.Protocol` classes:

- `Toolbox`: `open_session(site_id) -> handle`, `close_session(handle)`, `run_subtask(SubtaskInput) -> WorkerReport`, `observe(handle) -> observation_ref`, `dom_interpret(handle, question) -> str`, `vision_interpret(handle, question) -> str`.
- `Moderator`: `assess_report(subtask, report, success_conditions) -> ModeratorDecision`, `reconcile(plan, reports) -> ModeratorDecision`, `synthesize(interpreted, records, validation, evidence, failures) -> FinalAnswer`. Optional `observe_progress(snapshot, event) -> ModeratorDecision`.
- `Ghost`: `match(subtask, skills) -> dict` (`decision`: `reuse` | `explore`, `reason`, `skill`), `validate(subtask, records, evidence) -> dict` (`status`: `passed` | `failed` | `inconclusive`, `checks`), `compile(report, subtask) -> dict | None`. Names chosen to line up with Sting's demo functions `validate_request`, `replay`, `verify`, `qualify`; the adapter in phase 3 maps them.

`argus/registry.py`: `SITES` and `OPERATIONS`. One controlled site `demo-catalog` and one operation `search_products` with parameters `query` (string, required), `max_price` (number, optional, minimum 0), `max_results` (integer, default 5), `currency` fixed `USD`. Structure allows adding a site later without code changes to the interpreter.

`argus/examples/`: `worker_report.json` copied from Thomas's example, `interpreted_request.json`, `plan.json`, `moderator_decision_accept.json`, `final_answer.json`, `gate_clarify.json`, `gate_reject.json`. Every fixture must round-trip through `from_dict` and `to_dict`.

## Phase 0 outcome (2026-09-12)

Delivered and reviewed: 13 files under `argus/`, 34 tests passing with `python3 -m unittest discover -s argus/tests -v`. No specced field was renamed. Facts the later prompts rely on:

- `ContractError(message, code=...)` carries `.typed_error`; use it for `MODEL_REFUSED`.
- `ParameterOrigin(value, source, confidence, span=None)`; spans normalise to tuples. `MissingParameter`, `Claim` and `Budget` are named dataclasses with the specced field names.
- `ModeratorDecision.decision` is checked per stage via `contracts.MODERATOR_DECISIONS`: assess → accept, verify, retry_other_path, fail; reconcile → merged, verify, fail; observe → continue, flag, stop_subtask. Stage 10 returns a `FinalAnswer`, not a decision.
- `interfaces.py` has `Toolbox`, `Moderator`, `ProgressObserver` (optional, checked with `isinstance`), `Ghost` and `Store`. `JsonStore` implements `Store`.
- All 13 of Thomas's `WorkerReport` fields are required; only `session_handle` and `typed_failures` default. `RunResult` requires only `run_id` and `status` so snapshots can be written mid-run.
- `WorkerReport.outcome` is not vocabulary-checked because Thomas owns it.
- Sting's demo prices in CAD while the registry fixes USD. Phase 3 concern, noted for the Ghost adapter.

## Phase 1 outcome (2026-09-12)

A, B, C and D delivered; 205 tests pass with `python3 -m unittest discover -s argus/tests -v` after the review edits below. No file outside `argus/` was touched by the agents. Review edits and facts prompt 2 relies on:

- `RUN_STATUSES` now includes `running`, so mid-run snapshots can be `RunResult`-shaped. The controller currently writes plain dicts for snapshots; converting it is optional.
- Concurrency group = mutual-exclusion class (at most one running subtask per group; distinct groups run together). ARGUS.md stage 5 was reconciled to match the code.
- The `anthropic` SDK and `pydantic` are not installed on this machine. `argus.interpreter` imports them lazily; a real `interpret()` call raises `PRECONDITION_FAILED` with an install message until `pip install anthropic`. Tests inject a fake client.
- The planner fills registry defaults; the interpreter never emits `source="default"`.
- `JsonStore.create_run` raises on an existing run ID; `save_snapshot` is the update path. The controller creates each run once under `run-<uuid12>`.
- `FakeToolbox(script=...)` accepts a sequence per subtask ID, consumed one call at a time. `StubModerator(retry_codes=("TARGET_NOT_FOUND",))` makes the stub return `retry_other_path` for those codes; default never retries.
- The scripted `"empty"` outcome has no records and no screenshot (thin-evidence path, drives `verify`). It is not an evidenced empty state.
- Run status is `succeeded` only if every subtask was accepted and validation passed; `inconclusive` validation fails with `VALIDATION_FAILED` retryable. A failed independent sibling makes the run `failed` while the answer keeps the accepted records.
- A `reconcile` decision of `verify` is recorded in `answer.unverified`, not executed, in this phase.
- Controller signature: `Controller(toolbox, moderator, ghost, store, max_actions=30, max_seconds=120, max_concurrency=2, *, interpret=None, gate=None, plan=None)`; `run(text_or_interpreted, request_id, run_id=None)`; `cancel(run_id)`.

## Prompts

Each prompt is self-contained. Paste it into a fresh subagent using the model and reasoning level in its heading. All paths are relative to `/Users/baiyangchen/Developer/Coding/Bots`.

### Prompt 0 — contracts, interfaces, registry, fixtures (Opus 5, high)

```text
You are implementing phase 0 of the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md fully, then docs/hackathon/ARGUS.md, docs/hackathon/CONTRACTS.md, and the real worker report at workers/visual/examples/hn-top-story/report.json. Also read ghostapi/demo/ghost_demo.py to see Sting's function names.

Create exactly these files: argus/__init__.py, argus/contracts.py, argus/interfaces.py, argus/registry.py, argus/examples/*.json, argus/tests/__init__.py, argus/tests/test_contracts.py. Follow the "Phase 0 spec — contracts" section of the plan for every field name; do not rename or add fields without noting it in your final report.

Requirements:
- Python 3.13 standard library only. Dataclasses with to_dict() and from_dict() that reject unknown fields and missing required fields with a ContractError.
- WorkerReport.from_dict must accept Thomas's report.json unchanged; session_handle and typed_failures default to None and [] when absent.
- Every fixture in argus/examples round-trips: from_dict(to_dict(x)) == x. Write a test that loads every fixture.
- registry.py exposes SITES, OPERATIONS, required_parameters(operation), defaults(operation), and validate_parameters(operation, params) -> list[str] of problems.
- Docstrings state what each message is for and which stage produces it.

Do not write the interpreter, planner, controller, store or fakes. Do not modify any file outside argus/. Do not commit.

Run: python3 -m unittest discover -s argus/tests -v. Report the exact command and output, the list of files created, and any field you had to change from the spec and why.
```

### Prompt A — interpreter and gate (Opus 5, high)

```text
You are implementing the interpreter and acceptance gate of the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md, docs/hackathon/ARGUS.md (stages 1 and 2), then argus/contracts.py, argus/registry.py and argus/examples/interpreted_request.json. Those phase 0 files are read-only for you.

Create exactly: argus/interpreter.py, argus/gate.py, argus/tests/test_interpreter.py, argus/tests/test_gate.py.

Interpreter:
- interpret(text, request_id, client=None, model=None) -> InterpretedRequest. Uses the official anthropic Python SDK (pip install anthropic). Default model is the environment variable ARGUS_MODEL, falling back to "claude-opus-5". Use client.messages.parse with pydantic output models mirroring the InterpretedRequest schema so the response is validated JSON; then convert to the dataclass. max_tokens 4096. Do not use assistant prefill and do not pass temperature.
- The system prompt lists the supported sites and operations from registry.py, their parameters and types, and instructs the model to: split compound requests into multiple intents; tie every parameter to the character span of the input it came from; never invent parameters; list missing required parameters as questions; list ambiguities instead of guessing.
- Check response.stop_reason. If it is "refusal", raise a ContractError carrying TypedError code MODEL_REFUSED. Never read content before checking stop_reason.
- Verify every returned span actually matches the raw text; if not, downgrade that parameter's source to "structured" with confidence 0 and add an ambiguity.
- Accept an injected client for tests. Tests must not call the network: build a fake client object whose messages.parse returns a canned object with parsed_output and stop_reason. Cover: single intent, compound request with two intents, missing required parameter, unsupported site, refusal.

Gate:
- gate(interpreted) -> GateDecision, pure rules, no model calls. Rule order and IDs: G1 no intents -> reject; G2 unknown site -> reject; G3 unsupported operation -> reject; G4 missing required parameter -> clarify with the interpreter's questions; G5 parameter type or scope invalid per registry.validate_parameters -> reject; G6 unknown parameter not in the operation -> reject (unknown filters are never dropped silently); G7 any ambiguity with confidence below 0.6 on a required parameter -> clarify; otherwise accept with rule_id "G0".
- Tests cover every rule with a fixture-shaped InterpretedRequest.

Update argus/examples/gate_clarify.json and gate_reject.json only if their content contradicts your rules, and say so in the report.


Phase 0 facts: read the "Phase 0 outcome" section of the plan first. Raise MODEL_REFUSED with ContractError(message, code="MODEL_REFUSED"). ParameterOrigin takes (value, source, confidence, span=None). MissingParameter is a dataclass with intent_index, parameter, question.

Do not modify contracts.py, registry.py, or any file outside the four you create. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, the output, files created, and any contract change you need from phase 0.
```

### Prompt B — planner and controller (Fable 5.1, xhigh)

```text
You are implementing the planner and controller of the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md, docs/hackathon/ARGUS.md (stages 3 to 11, the session lifecycle and the hard limits), then argus/contracts.py, argus/interfaces.py, argus/registry.py and the fixtures in argus/examples/. Those phase 0 files are read-only for you. Also read the untracked backend/argus/state.py if it exists; its transition table and single-terminal-result rules are worth porting, the rest of backend/ is not.

Create exactly: argus/planner.py, argus/controller.py, argus/tests/test_planner.py, argus/tests/test_controller.py.

Planner (deterministic, no model calls):
- plan(interpreted, plan_id) -> Plan. One Subtask per Intent. Subtasks with no shared parameters and no data dependency get distinct concurrency groups and empty depends_on, so they may run together. Success conditions come from a fixed table per operation in planner.py: for search_products they are "results present or explicit empty state", "every record has title, price, currency, url", "price within max_price when given", "query visibly applied". preferred_tool defaults to "dom".
- validate_plan(plan) raises ContractError on cycles, unknown operations, or duplicate IDs.

Controller:
- Controller(toolbox, moderator, ghost, store, max_actions=30, max_seconds=120, max_concurrency=2). Dependencies are injected through the Protocols in argus/interfaces.py; never import concrete fakes.
- run(text_or_interpreted, request_id) drives: interpret (call argus.interpreter.interpret if given text; that module may not exist yet in your working tree, so import it lazily inside the function and accept an already-built InterpretedRequest in tests) -> gate -> plan -> match per subtask via ghost.match -> dispatch -> intake -> reconcile (only if more than one subtask) -> validate via ghost.validate -> synthesize via moderator.synthesize -> publish.
- Stage state machine with legal transitions only, terminal states completed, failed, cancelled, needs_input. Exactly one terminal result, written together with the terminal event. Events have increasing sequence numbers per run and are appended to the store as they happen.
- Dispatch: subtasks whose depends_on are all accepted start together up to max_concurrency using concurrent.futures.ThreadPoolExecutor. For each subtask: handle = toolbox.open_session(site_id); build SubtaskInput; report = toolbox.run_subtask(input); the report's session_handle is kept by the controller. Sessions are closed in a finally block at run end, never by subagents.
- Intake: schema check, then moderator.assess_report. Execute the decision: accept; verify (one per subtask: toolbox.observe then toolbox.vision_interpret or dom_interpret with the success conditions, then assess again); retry_other_path (one per subtask: reissue with the other preferred_tool on the same session); fail (carry the typed failure). Enforce the caps in the controller, not in the moderator.
- Sibling failure: cancel dependents of a failed subtask, let independent siblings finish.
- Budgets: per-subtask max_actions from report.metrics.browser_action_count and elapsed wall clock; a breach ends the subtask with BUDGET_EXCEEDED. Whole-run max_seconds ends the run with BUDGET_EXCEEDED.
- Validation failure: the moderator cannot override it; the run fails with VALIDATION_FAILED unless the moderator's synthesize is still called to explain the failure honestly. Do not call ghost.compile if validation failed.
- After synthesize, rule check: every claim has at least one evidence ref, else fail with EXTRACTION_FAILED and a reason.
- Never let a stack trace or provider message into events or results; map unexpected exceptions to EXTRACTION_FAILED with the exception class name only.

Tests use small in-test fakes implementing the Protocols (do not import argus/fakes.py; it is being written in parallel). Cover: single-subtask success; two independent subtasks run concurrently (assert both sessions open before either closes); dependent subtask cancelled when its dependency fails; verify path executed once and capped; retry_other_path capped; validation failure cannot be overridden; budget breach; every session closed even when run_subtask raises; event sequence strictly increasing and one terminal event.


Phase 0 facts: read the "Phase 0 outcome" section of the plan first. ModeratorDecision.decision is validated per stage by contracts.MODERATOR_DECISIONS; use those exact strings. Type the injected store as interfaces.Store. Check for live monitoring with isinstance(moderator, interfaces.ProgressObserver). Budget is a dataclass (max_actions, max_seconds).

Do not modify contracts.py, interfaces.py, registry.py or any file outside the four you create. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output, files created, and any contract change you need from phase 0.
```

### Prompt C — JSON store (Opus 5, medium)

```text
You are implementing JSON storage for the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md and argus/contracts.py (read-only).

Create exactly: argus/store.py and argus/tests/test_store.py.

JsonStore(root_dir):
- Layout: <root>/runs/<run_id>/run.json (RunResult or in-progress snapshot), <root>/runs/<run_id>/events.jsonl (one Event per line, appended), <root>/runs/<run_id>/reports/<subtask_id>.json (WorkerReport), <root>/runs/<run_id>/evidence/ (files referenced by observation IDs, copied in by save_evidence_file), <root>/skills/<skill_id>/v<version>.json, <root>/index.json mapping request_id to run_id.
- Methods: create_run(run_id, request_id, snapshot), save_snapshot(run_id, snapshot), append_event(run_id, event), save_report(run_id, report), save_evidence_file(run_id, observation_id, source_path) -> stored path, save_skill(skill_dict), skills() -> list, run(run_id) -> dict, events(run_id) -> list, run_id_for_request(request_id).
- Writes are atomic: write to a temp file in the same directory then os.replace. A threading.Lock guards index.json and events.jsonl appends. Reads tolerate a missing run with a clear KeyError.
- Standard library only. Do not store secrets or session handles; strip a top-level "session_handle" key from any report before writing.


Phase 0 facts: implement interfaces.Store exactly; its method signatures are the spec. Add a test asserting isinstance(JsonStore(tmp), Store).

Tests use tempfile.TemporaryDirectory and cover: create then read, event ordering after concurrent appends from two threads, atomic overwrite, skill versions listed in order, session_handle stripped.

Do not modify any file outside the two you create. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output and files created.
```

### Prompt D — fakes (Opus 5, medium)

```text
You are implementing the fakes for the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md, docs/hackathon/ARGUS.md (report intake decision rules), argus/contracts.py, argus/interfaces.py and every fixture in argus/examples/ (all read-only). Also read ghostapi/demo/ghost_demo.py so FakeGhost's behavior resembles Sting's simulated demo.

Create exactly: argus/fakes.py and argus/tests/test_fakes.py.

- FakeToolbox(script=None): implements the Toolbox protocol. open_session returns "fake-session-N" and records it as open; close_session marks it closed and raises if called twice. run_subtask returns a WorkerReport built from argus/examples/worker_report.json with the subtask's IDs substituted and findings filled from a small in-memory catalog (five products with title, price in USD, url on https://demo-catalog.invalid) filtered by query substring and max_price. A script dict keyed by subtask_id can force outcomes: "target_not_found", "auth_required", "budget", "raise", "empty". observe returns "observation-fake-N"; dom_interpret and vision_interpret return a short string echoing the question. Every call is appended to self.calls for assertions.
- StubModerator: assess_report returns accept when outcome is "succeeded" and the report has at least one evidence screenshot, verify when succeeded but evidence is empty, fail otherwise with the report's first typed failure. reconcile returns a decision listing subtask IDs and no conflicts. synthesize returns a FinalAnswer whose claims are one per record, each citing the record's source observation, and lists failures verbatim.
- FakeGhost: match always returns explore with reason "no qualified skills in fake"; validate passes when every record has title, price, currency "USD" and url on the configured site and price <= max_price if given, fails otherwise with named checks; compile returns a candidate skill dict with status "candidate" and the subtask's operation, or None if the report has no actions.


Phase 0 facts: read the "Phase 0 outcome" section of the plan first. StubModerator decisions must use the strings in contracts.MODERATOR_DECISIONS for their stage; reconcile returns "merged" with next_action carrying findings, gaps and conflicts. Build WorkerReport via WorkerReport.from_dict on the fixture so all 13 required fields are present.

Tests cover each scripted outcome, the double-close guard, the validate pass and fail cases, and that StubModerator.synthesize never emits a claim without an evidence ref.

Do not modify any file outside the two you create. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output and files created.
```

### Prompt 2 — integration (Opus 5, high, after A to D are merged)

```text
You are integrating the ARGUS controller base in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read AGENTS.md, docs/ai/TEAM.md, docs/hackathon/ARGUS-IMPLEMENTATION.md, docs/hackathon/ARGUS.md, then every file under argus/.

Create: argus/__main__.py, argus/tests/test_end_to_end.py, argus/README.md. Edit docs/ai/TEAM.md (only the ARGUS-1 row and an ARGUS-1 update block) and docs/ai/CURRENT.md (only the "Latest work and next useful step" section).

- __main__.py: python -m argus "<request text>" [--fake] [--store DIR] [--request-id ID]. With --fake (default until a real toolbox exists) wire Controller with FakeToolbox, StubModerator, FakeGhost and JsonStore, and print the final RunResult as JSON. Without --fake, exit with a clear message that no real toolbox is connected yet. Interpretation calls the model unless --interpreted FILE supplies an InterpretedRequest JSON, so the end-to-end path can run offline.
- test_end_to_end.py: runs the controller with all fakes and an interpreted-request fixture for: a successful single-subtask search; a compound request with two concurrent subtasks; a clarify outcome; a scripted target_not_found leading to retry_other_path then success; a scripted auth_required leading to an honest failed result; a validation failure that is not overridden. Assert the JSON store contains run.json, events.jsonl and one report per subtask afterward.
- README.md per AGENTS.md: purpose, entry point, environment variable names (ANTHROPIC_API_KEY, ARGUS_MODEL), input/output/failure examples taken from real test output, run and test commands, dependencies (anthropic SDK version pinned as installed), known limitations (fakes only, moderator stub, no Steel, planner deterministic).
- Fix integration mismatches between workstreams in the module that is wrong, not by widening contracts.py; if a contract change is unavoidable, make it, bump schema_version, update fixtures, and list it in the report.
- TEAM update uses the compact block format in TEAM.md and records only checks you actually ran with their real output. CURRENT gets two or three sentences.

Phase 1 facts: read the "Phase 0 outcome" and "Phase 1 outcome" sections of the plan first. For the retry end-to-end case use FakeToolbox(script={"subtask-1": ["target_not_found", None]}) and StubModerator(retry_codes=("TARGET_NOT_FOUND",)). Do not pip install anything; the interpreter path is exercised only through --interpreted FILE or an injected fake client. In the README state plainly that anthropic and pydantic must be installed for live interpretation and that no real toolbox, moderator or Ghost is connected.

Do not commit. Run python3 -m unittest discover -s argus/tests -v and the CLI in --fake mode with an interpreted fixture, and report both commands with their output.
```

## Review gates for Abel

1. After phase 0: read `contracts.py` and the fixtures before starting A to D. This is the only point where field names are cheap to change.
2. After A to D: run the full test suite once; resolve any contract-change requests in the handoff notes; then launch prompt 2.
3. After phase 2: read `argus/README.md` and the TEAM block, then commit on a branch and open a pull request so Thomas and Sting can review the interfaces they will implement against.

## Out of scope until phase 3

Real Steel sessions, Thomas's moderator, Sting's Ghost adapter, the HTTP API and server-sent events from CONTRACTS, the dashboard, skill qualification and repair, the public site.

## Phase 1b — open-world navigation (planned 2026-09-12, not started)

User direction: the product must navigate any site and any read-only task. Design delta is the "Open-world navigation" section of [ARGUS.md](ARGUS.md). The closed-registry base from phases 0 to 2 stays; this phase widens it. Same working rules as phase 1: one agent per prompt, listed files only, no commits, tests offline.

| Phase | Work | Depends on | Parallel? | Model | Reasoning |
| --- | --- | --- | --- | --- | --- |
| 0b | Contract additions for open intents and chains | Phase 2 merged | No | Opus 5 | high |
| E | Interpreter tier two + safety gate | 0b | Yes, with F | Opus 5 | high |
| F | Model planner with chains, controller dependency data flow, generic validation in FakeGhost | 0b | Yes, with E | Fable 5.1 | xhigh |
| 2b | Integration: end-to-end open-world tests, CLI, README, TEAM, CURRENT | E, F | No | Opus 5 | high |

### Phase 0b spec — contract additions

Add to `argus/contracts.py`, keeping every existing field and fixture valid:

- `Intent` gains `kind` (`"registry"` | `"open"`, default `"registry"`), `target_domain` (nullable, e.g. `"news.ycombinator.com"`), `goal` (nullable plain-language goal), `criteria: list[Criterion]`, `expected_record_shape: list[str]` (field names the user expects, may be empty). `Criterion`: `text`, `kind` (`"rank"` | `"filter"` | `"limit"`), `parameter` (nullable), `span` (nullable), `confidence`.
- `Subtask` gains `inputs_from: dict[str, {"subtask_id", "field"}]` so a parameter can be filled from a completed dependency's findings, and `kind` (`"registry"` | `"open"`).
- `Plan` gains `planned_by` (`"deterministic"` | `"model"`) and `caps: {"max_subtasks", "max_depth"}`.
- New error codes: `DOMAIN_NOT_ALLOWED`, `ACTION_CLASS_NOT_ALLOWED`, `PLAN_TOO_LARGE`.
- `registry.py` gains `DOMAIN_POLICY`: `blocklist` (login pages, payment providers, anything the team lists) and `allow_any_other: bool`, and a helper `domain_allowed(domain) -> (bool, reason)`.
- New fixtures: `interpreted_request_open.json` (jobs example: target domain, goal, ten results, criterion "best" as rank with low confidence), `plan_open_chain.json` (search subtask then a dependent "open each result" subtask with `inputs_from`), `gate_open_accept.json`, `gate_open_reject_domain.json`.

### Prompt 0b — contract additions (Opus 5, high)

```text
You are extending the ARGUS contracts for open-world navigation in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md (the whole file, especially "Phase 0 outcome", "Phase 1 outcome" and "Phase 1b"), the "Open-world navigation" section of docs/hackathon/ARGUS.md, then argus/contracts.py, argus/registry.py, argus/examples/ and argus/tests/test_contracts.py.

Edit only: argus/contracts.py, argus/registry.py, argus/tests/test_contracts.py, and add the four new fixtures under argus/examples/ named in the "Phase 0b spec". Follow that spec for every field name. Every existing fixture must still load unchanged and every existing test must still pass; new fields default so old payloads are valid. Add round-trip tests for the new fixtures and unit tests for registry.domain_allowed.

Do not touch interpreter.py, gate.py, planner.py, controller.py, store.py, fakes.py or their tests. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output, files changed, and any spec field you changed and why.
```

### Prompt E — interpreter tier two and safety gate (Opus 5, high)

```text
You are widening the ARGUS interpreter and gate for open-world navigation in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md ("Phase 0 outcome", "Phase 1 outcome", "Phase 1b"), the "Open-world navigation" section of docs/hackathon/ARGUS.md, then argus/contracts.py, argus/registry.py, argus/interpreter.py, argus/gate.py and their tests. Contracts and registry are read-only for you.

Edit only: argus/interpreter.py, argus/gate.py, argus/tests/test_interpreter.py, argus/tests/test_gate.py.

Interpreter:
- Keep the registry tier exactly as it is. Add tier two: when the text does not map to a registry operation, the model returns an open Intent with kind "open", target_domain (from an explicit URL or site name in the text, else null), goal, parameters with spans, criteria (each with kind rank/filter/limit, span, confidence; "best", "cheapest", "top 10" become criteria, never silent), and expected_record_shape. The pydantic output model must express both tiers; keep additionalProperties false.
- The system prompt must say: prefer a registry operation when one fits; never invent a domain; if the user names no site and the task implies one, leave target_domain null and add an ambiguity; ranking words become criteria with confidence reflecting how specific they are.
- Same refusal handling, span verification and offline test pattern as before. Add tests: open intent with domain; open intent without domain producing an ambiguity; "best 10 jobs" producing a limit criterion 10 and a rank criterion "best" with confidence below 0.6; registry request still preferred over open when both could fit.

Gate:
- Keep G1 to G7 for kind "registry". For kind "open" apply S-rules in order: S1 target_domain null -> clarify asking for the site; S2 registry.domain_allowed false -> reject DOMAIN_NOT_ALLOWED; S3 goal empty -> clarify; S4 any criterion of kind rank with confidence below 0.6 -> clarify asking what "best" means; S5 the goal mentions logging in, buying, paying, submitting or posting -> reject ACTION_CLASS_NOT_ALLOWED (keyword list in gate.py, documented as a first cut); otherwise accept with rule_id "S0". Tests for every S-rule and for the fixtures gate_open_accept.json and gate_open_reject_domain.json.

Do not modify contracts.py, registry.py, planner.py, controller.py, fakes.py, store.py or any other file. Do not pip install anything. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output, files changed, and any contract change you need.
```

### Prompt F — model planner with chains, controller data flow, generic validation (Fable 5.1, xhigh)

```text
You are adding model-planned decomposition with dependent chains to the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read docs/hackathon/ARGUS-IMPLEMENTATION.md ("Phase 0 outcome", "Phase 1 outcome", "Phase 1b"), the "Open-world navigation" section of docs/hackathon/ARGUS.md, then argus/contracts.py, argus/interfaces.py, argus/registry.py, argus/planner.py, argus/controller.py, argus/fakes.py and their tests. Contracts, interfaces and registry are read-only for you.

Edit only: argus/planner.py, argus/controller.py, argus/fakes.py, argus/tests/test_planner.py, argus/tests/test_controller.py, argus/tests/test_fakes.py.

Planner:
- Keep the deterministic path for kind "registry" intents unchanged. Add plan_open(interpreted, plan_id, client=None, model=None) for kind "open" intents: one model call using the official anthropic Python SDK with client.messages.parse and a pydantic plan model (import anthropic lazily; model from ARGUS_MODEL, default "claude-opus-5"; check stop_reason before content; refusal -> ContractError MODEL_REFUSED). The model receives the intent and returns subtasks with depends_on, inputs_from, concurrency_group, success_conditions and preferred_tool. Then rule-check: acyclic, every inputs_from names an earlier subtask and a field in expected_record_shape or "findings", subtask count <= caps.max_subtasks (default 6), depth <= caps.max_depth (default 3), else ContractError PLAN_TOO_LARGE. plan() dispatches by intent kind; a mixed request produces one Plan with planned_by "model".
- Tests inject a fake client; cover a search-then-open-each chain, a cycle rejection, a too-large plan, and that registry intents never call the client.

Controller:
- When a subtask has inputs_from, fill those parameters from the dependency's accepted report findings before building SubtaskInput; if the field is missing, fail that subtask with PRECONDITION_FAILED and cancel its dependents. Findings from a dependency are also passed to the subagent in SubtaskInput.bound_procedure only if mode is reuse; otherwise as parameters only.
- Everything else (caps, sessions, budgets, terminal result) unchanged. Add tests: a two-step chain where step two's parameter comes from step one's findings; missing field fails honestly; the chain respects max_concurrency.

FakeGhost:
- validate becomes generic when subtask.kind is "open": every record cites an observation from this run, records are non-empty or the report has an explicit empty-state marker, no record url is off the target domain. Operation-specific checks remain for kind "registry". Tests for both.
- FakeToolbox: when the subtask is kind "open", return findings shaped by expected_record_shape from a tiny canned dataset keyed by target_domain (add one for a jobs board with title, company, url), so end-to-end tests can run offline.

Phase 1 facts still apply. Do not modify contracts.py, interfaces.py, registry.py, interpreter.py, gate.py, store.py or any other file. Do not pip install anything. Do not commit. Run python3 -m unittest discover -s argus/tests -v and report the command, output, files changed, and any contract change you need.
```

### Prompt 2b — open-world integration (Opus 5, high, after E and F)

```text
You are integrating open-world navigation into the ARGUS controller base in the repository at /Users/baiyangchen/Developer/Coding/Bots. Read AGENTS.md, docs/ai/TEAM.md, docs/ai/CURRENT.md, docs/hackathon/ARGUS-IMPLEMENTATION.md, docs/hackathon/ARGUS.md, then every file under argus/.

Edit: argus/__main__.py, argus/tests/test_end_to_end.py, argus/README.md, docs/ai/TEAM.md (ARGUS-1 row and a new update block), docs/ai/CURRENT.md ("Latest work and next useful step" only). Fix integration mismatches in the module that is wrong.

- End-to-end tests with fakes and an interpreted fixture: the "best 10 jobs" open request producing clarify on the rank criterion; the same request with "by salary" accepted, planned as a chain by an injected fake planner client, run through two dependent subtasks, validated generically, synthesized with the criterion applied; a blocked domain rejected; a plan over caps rejected; the registry path unchanged.
- README documents the two tiers, the S-rules, the caps, the domain policy, what is generic vs registry-specific validation, and that live interpretation and planning need anthropic and pydantic installed.
- TEAM and CURRENT record only checks actually run with real output.

Do not commit. Run python3 -m unittest discover -s argus/tests -v and the CLI in --fake mode with argus/examples/interpreted_request_open.json, and report both commands with their output.
```

### Things to settle with teammates before phase 3 (unchanged by 1b)

- Session ownership: Thomas's worker currently opens its own Steel session; ARGUS lends sessions. One side changes.
- Moderator: Thomas implements assess_report, reconcile, synthesize per ARGUS.md; synthesis now also applies user criteria.
- Ghost: adapter from Sting's demo names to match/validate/compile; CAD vs USD; qualification promoting open runs into the registry.
- Domain blocklist contents.
