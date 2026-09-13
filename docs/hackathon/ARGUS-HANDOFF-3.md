# ARGUS phase 3 handoff — connecting the real components

Written 2026-09-12 on `feat/argus-controller-base` after merging `origin/main` (Thomas's `moderator/` and the 72B visual-worker updates). Source of truth for what exists is [ARGUS-CONNECTION-AUDIT.md](ARGUS-CONNECTION-AUDIT.md); this file turns it into runnable prompts. All adapters live under a new package `argus/adapters/` so no teammate package is edited without its owner. Same working rules as phase 1b: one agent per prompt, listed files only, offline tests with fakes, no commits, no pip installs, no network. Live checks are separate manual commands run by Abel with real keys.

## What the merged `moderator/` module is, and is not

`moderator/moderator.py` is a **run harness**: it takes a spec of subtasks with start URLs, spawns `workers/visual` as subprocesses, tails their traces, kills them on budget, emits events, aggregates their reports and synthesizes a final answer with an OpenAI model (default `gpt-5.6-sol`, env `MODERATOR_MODEL`). Its README says so, and it is honest: validation stays `inconclusive` and the answer is labelled unverified.

It does **not** implement `argus.interfaces.Moderator`. There is no `assess_report`, no `reconcile` decision, and its `_synthesize` takes its own aggregate dict, not `(interpreted, records, validation, evidence, failures)`. It also owns dispatch, budgets and events, which in ARGUS belong to the controller. Running both would be the "two controllers" case the architecture doc warns about.

Decision to settle with Thomas before P3-MOD runs: the harness's dispatch, monitoring and event code is superseded by the ARGUS controller; its `_synthesize` prompt and the stall and budget logic are reused inside an adapter that implements the `Moderator` protocol. If Thomas prefers to keep the harness as a standalone demo, it must not be wired into ARGUS runs.

## Order

| Prompt | Connection | Depends on | Needs from a teammate first | Model |
| --- | --- | --- | --- | --- |
| P3-SESSION | Shared Steel session manager behind `open_session` and `close_session` | nothing | nothing (uses the same Steel calls both workers already make) | Opus 5, high |
| P3-DOM | Toolbox adapter over Tianqi's `browser_worker.Worker` | P3-SESSION | nothing blocking; naming questions absorbed by the adapter | Opus 5, high |
| P3-GHOST | Ghost adapter over `ghostapi/demo/ghost_demo.py` | nothing | Sting's CAD or USD decision (adapter must not convert) | Opus 5, high |
| P3-MOD | Moderator adapter reusing Thomas's synthesis, plus assess and reconcile | nothing | Thomas's agreement on the boundary above | Fable 5.1, xhigh |
| P3-VISUAL | Toolbox adapter over Thomas's `UITarsSubagent` | P3-SESSION, Thomas's session change | Thomas: `SteelBrowser` accepts an external session id and skips release when it does not own it | Opus 5, high |
| P3-INT2 | First connected run: DOM worker, Ghost adapter, moderator adapter, real model client | P3-SESSION, P3-DOM, P3-GHOST, P3-MOD | STEEL_API_KEY, OPENAI_API_KEY, a configured site | Opus 5, high |

P3-SESSION, P3-GHOST and P3-MOD have no dependency on each other and may run in parallel. P3-DOM waits for P3-SESSION. P3-VISUAL waits for Thomas.

### P3-SESSION — shared Steel session manager (Opus 5, high)

```text
You are adding a Steel session manager to the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-3.md (whole), docs/hackathon/ARGUS-CONNECTION-AUDIT.md, argus/interfaces.py (Toolbox), argus/controller.py (open_session, close_session, observe, dom_interpret, vision_interpret call sites), browser_worker/adapter.py (how it creates, attaches to and releases Steel sessions, and the one-page requirement) and workers/visual/browser_subagent/steel_browser.py (start and stop).

Create argus/adapters/__init__.py, argus/adapters/steel_sessions.py and argus/tests/test_steel_sessions.py. Edit nothing else.

SteelSessionManager(client=None, max_open=4):
- open(site_or_domain) -> handle string that is the Steel session id; records the site and a created timestamp; refuses a fifth concurrent session with ContractError code BUDGET_EXCEEDED (the four local VLM slots; document that this is per-process, not machine-wide).
- close(handle): releases the session with the same SDK call the DOM worker uses; idempotent; unknown handle raises ContractError.
- close_all(): used by the controller's finally path.
- observe(handle) -> observation id: takes a screenshot through the Steel session, saves it under a run-scoped evidence directory given at construction, and returns a stable id "observation-<n>" mapped to that file. Keep a dict from observation id to path.
- The Steel SDK is imported lazily inside the constructor when client is None; tests inject a fake client object exposing sessions.create(...) returning an object with .id, sessions.release(id), and whatever screenshot call you use. If the steel package is not installed, the import path raises ContractError PRECONDITION_FAILED naming STEEL_API_KEY and the package.
- Never log or persist the API key. The handle is the session id only.

Tests: open then close; double close is a no-op; unknown handle raises; fifth session refused; close_all releases every open session even if one release raises; observe returns increasing ids and records the path; the fake client records calls.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report files created, test count and time, whether the steel package is installed locally, anything unverified, git status --short, and confirm nothing was staged or committed.
```

### P3-DOM — toolbox adapter over Tianqi's DOM worker (Opus 5, high)

```text
You are connecting Tianqi's DOM browser worker to the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/. Do not edit anything under browser_worker/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-3.md, the DOM-worker sections of docs/hackathon/ARGUS-CONNECTION-AUDIT.md, argus/interfaces.py, argus/contracts.py (SubtaskInput, WorkerReport, TypedError, ERROR_CODES), argus/adapters/steel_sessions.py, browser_worker/README.md, browser_worker/schemas.py (SubtaskRequest, SubtaskReport, FailureCode, SessionSpec), browser_worker/runner.py (Worker.run), browser_worker/policy.py, browser_worker/sites.json and tests/browser_worker/ for the shapes they assert.

Create argus/adapters/dom_toolbox.py and argus/tests/test_dom_toolbox.py. Edit nothing else.

DomToolbox(sessions: SteelSessionManager, worker_factory=None, sites=None) implementing argus.interfaces.Toolbox:
- open_session and close_session delegate to the session manager. observe delegates too.
- run_subtask(SubtaskInput): build a browser_worker SubtaskRequest from it. objective from subtask.goal, else "<operation> with <parameters>"; operation name map search_products -> search_extract, documented as a table; site allowlists and url patterns from browser_worker's site config for that site_id; required_evidence per the worker's schema; success_conditions: translate the fixed registry conditions you can (max_results -> max_records, max_price -> field_lte price, currency -> field_equals) and drop the rest, recording each dropped one in the report's failures as a non-fatal note; budgets from SubtaskInput.budget with math.ceil and clamping to the worker's allowed range; session = {"ownership": "argus", "session_ref": session_handle, "close_on_finish": False}. mode "reuse" and bound_procedure have no receiver: raise ContractError PRECONDITION_FAILED saying reuse is not supported by this worker yet. kind "open" subtasks: raise PRECONDITION_FAILED saying the DOM worker only accepts configured sites and the search_extract operation.
- Run the worker with asyncio.run(worker.run(request)) from the controller's thread (no running loop there); if a loop is already running, run in a fresh thread.
- Convert SubtaskReport to WorkerReport: worker "dom"; worker_model = reasoning_backend; subtask = objective; findings = [record.data plus source_observation_id]; actions = action_trace dumped; evidence = {"screenshots": evidence_refs, "observations": observations dumped}; metrics with browser_action_count = metrics.actions plus elapsed_ms and model_call_count when present; failures = the worker's failures dumped; typed_failures via a code table: identical names pass through; BUDGET_EXHAUSTED -> BUDGET_EXCEEDED, ACTION_REJECTED -> ACTION_CLASS_NOT_ALLOWED, UNSUPPORTED_OPERATION -> UNSUPPORTED_CHANGE, SESSION_UNAVAILABLE and STALE_OBSERVATION and NO_PROGRESS and INTERNAL_ERROR -> EXTRACTION_FAILED, MODEL_ERROR and MODEL_TIMEOUT -> EXTRACTION_FAILED; outcome needs_visual -> outcome "failed" with typed failure TARGET_NOT_FOUND and the visual_handoff carried in failures so the moderator can choose retry_other_path; outcome inconclusive -> "failed" with VALIDATION_FAILED retryable True. Never copy the worker's own validation block into the report. Strip session_ref and session_disposition; set session_handle to the lent handle.
- dom_interpret(handle, question): not available in the worker; raise ContractError PRECONDITION_FAILED with a clear message. vision_interpret likewise.

Tests use a fake Worker whose run returns SubtaskReport objects built from the worker's own schemas (import them; no network, no Steel): success with two records; needs_visual; inconclusive; a failure with BUDGET_EXHAUSTED; a reuse-mode input rejected; an open-kind input rejected; the session spec asserted to be argus-owned with close_on_finish False; the operation name mapped; dropped success conditions recorded; no validation block in the WorkerReport; the resulting WorkerReport loads through WorkerReport.from_dict.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report files created, test count and time, every assumption about the worker's schema you had to make, anything unverified, git status --short, and confirm nothing was staged or committed.
```

### P3-GHOST — Ghost adapter over Sting's demo (Opus 5, high)

```text
You are connecting Sting's Ghost demo to the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/. Do not edit anything under ghostapi/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-3.md, the Ghost sections of docs/hackathon/ARGUS-CONNECTION-AUDIT.md, argus/interfaces.py (Ghost), argus/contracts.py, argus/controller.py (how match, validate with report_context, and compile results are consumed), argus/store.py (save_skill requirements), ghostapi/CONTRACTS.md, ghostapi/DEVELOPMENT.md, ghostapi/demo/ghost_demo.py and ghostapi/demo/test_ghost_demo.py.

Create argus/adapters/ghost_demo.py and argus/tests/test_ghost_demo_adapter.py. Edit nothing else.

GhostDemoAdapter(db_path=None) implementing argus.interfaces.Ghost. Import ghost_demo by adding ghostapi/demo to sys.path inside the module, documented; use a temporary SQLite file in tests.
- match(subtask, skills): run the demo's qualified-workflow lookup against the adapter's own SQLite connection (the same SQL run_task uses), never against run_task itself; return {"decision": "reuse" or "explore", "reason": ..., "skill": json-decoded definition or None}. If the ARGUS skills list contains a qualified skill for the same site and operation, prefer it and say so in the reason.
- validate(subtask, records, evidence, *, report_context=None): build the demo's evidence dict as {"applied_inputs": subtask.parameters, "empty_state": report_context["empty_state"] if given else False}; call verify(subtask.parameters, records, evidence_dict); map its {status, checks, scope} to the controller's shape, keeping checks as the name -> bool mapping; rewrite status to "inconclusive" when records are empty and no empty state was reported, and when verify raises for a shape reason (catch ValueError and KeyError only), returning the exception class name in a reason field. Never convert currency. Never override a failed status.
- compile(report, subtask): if the report has no actions return None; otherwise wrap simulated_discovery's definition in the envelope from ghostapi/CONTRACTS.md: skill_id "<site_id>.<operation>", version 1, status "candidate", site_id, operation, source_run_ids [report.request_id], plus the definition fields. Do not write to SQLite from compile. Never call run_task or qualify anywhere in the adapter; add a test that greps the module source for those names and fails if present.

Tests: match with an empty database returns explore; match with a qualified row inserted directly into the temp database returns reuse with the decoded skill; validate passes on fixture rows that the demo's catalog recognises and reports CAD in the reason of the failing currency check when records are USD (document this as the open owner decision, do not work around it); validate returns inconclusive on empty records without an empty state; compile returns an envelope the JsonStore accepts (save it into a temporary JsonStore in the test); the adapter satisfies interfaces.Ghost via isinstance.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report files created, test count and time, the exact currency behaviour observed, anything unverified, git status --short, and confirm nothing was staged or committed.
```

### P3-MOD — moderator adapter reusing Thomas's synthesis (Fable 5.1, xhigh)

```text
You are implementing the real moderator for the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots, reusing Thomas's merged synthesis prompt. Do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/. Do not edit anything under moderator/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-3.md (especially the section on what moderator/ is and is not), docs/hackathon/ARGUS.md (report intake decision rules, hard limits on the moderator, handoff callables), argus/interfaces.py (Moderator, ProgressObserver), argus/contracts.py (ModeratorDecision, MODERATOR_DECISIONS, FinalAnswer, Claim, WorkerReport), argus/model_client.py, argus/fakes.py (StubModerator as the behavioural baseline, FakeModelClient), argus/controller.py (how decisions are executed and capped), and moderator/moderator.py (_synthesize, _aggregate, _monitor_trace) plus moderator/README.md.

Create argus/adapters/moderator.py and argus/tests/test_moderator_adapter.py. Edit nothing else.

ModelModerator(client: ModelClient, max_tokens=1200) implementing interfaces.Moderator and interfaces.ProgressObserver:
- assess_report(subtask, report, success_conditions): first the deterministic rules from StubModerator (schema-valid report; failed outcome -> fail carrying the first typed failure; TARGET_NOT_FOUND or TARGET_AMBIGUOUS or EXTRACTION_FAILED on a first attempt -> retry_other_path; succeeded with no evidence -> verify). Only when the report succeeded with evidence, make one model call with a strict pydantic output {decision: accept|verify|fail, reason, unmet_conditions: list[str], verification_question: str|null} and the success conditions, records and evidence refs as input; map to a ModeratorDecision for stage "assess" with next_action carrying the question for verify. A refusal or invalid model result degrades to verify with a reason, never to accept.
- reconcile(plan, reports): deterministic merge by url as in StubModerator, then one model call only if two reports disagree on a shared url's fields, returning "merged" with next_action listing findings, gaps and conflicts each tagged resolve_from_evidence, verify or report_as_gap. Never drop a record without naming it in gaps.
- synthesize(interpreted, records, validation, evidence, failures): apply explicit criteria deterministically (filter, rank, limit, as StubModerator does), then one model call using Thomas's prompt from moderator/moderator.py adapted to receive the validated records and the validation status; the model returns strict JSON {text, claims: [{text, evidence_refs}], unverified: list[str]}; every claim must cite at least one evidence ref from the provided set or it is moved to unverified; when validation is not passed the text must end with the unverified line exactly as Thomas's prompt requires. On refusal or invalid output, fall back to a deterministic digest like Thomas's fallback and record the reason in unverified.
- observe_progress(snapshot, event): deterministic only. If the snapshot shows a subtask with no new action for longer than a stall threshold (default 120 s, taken from Thomas's harness), return "flag" with a verification suggestion; if a subtask's elapsed time exceeds its budget, return "stop_subtask" with the subtask_id; otherwise "continue". No model call.
- Every decision string must come from contracts.MODERATOR_DECISIONS. The adapter never changes budgets, never touches sessions, never writes results.

Tests use FakeModelClient (no network): each deterministic assess branch; model accept and model verify; refusal degrades to verify; reconcile with and without conflicts; synthesize applies criteria and cites evidence, moves uncited claims to unverified, and ends with the unverified line when validation is inconclusive; fallback on invalid model output; observe_progress flag, stop and continue; isinstance checks for both protocols; a test that runs the full Controller with this moderator, FakeToolbox, FakeGhost and a JsonStore on the registry fixture and on the salary chain fixture with FakePlannerClient, asserting the same outcomes the StubModerator end-to-end tests assert.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report files created, test count and time, which parts of Thomas's module were reused verbatim, anything unverified, git status --short, and confirm nothing was staged or committed.
```

### P3-VISUAL — toolbox adapter over Thomas's visual worker (Opus 5, high; blocked until Thomas's session change)

```text
You are connecting Thomas's visual worker to the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Precondition: workers/visual/browser_subagent/steel_browser.py accepts an external session id and skips release when it does not own the session; verify this by reading the file first and stop with a report if it is not true. Do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/. Do not edit anything under workers/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-3.md, the visual-worker sections of docs/hackathon/ARGUS-CONNECTION-AUDIT.md, argus/interfaces.py, argus/contracts.py, argus/adapters/steel_sessions.py, argus/adapters/dom_toolbox.py (mirror its structure), workers/visual/README.md, workers/visual/browser_subagent/uitars_agent.py, vlm_agent.py, steel_browser.py and worker_report.py.

Create argus/adapters/visual_toolbox.py and argus/tests/test_visual_toolbox.py. Edit nothing else.

VisualToolbox(sessions, agent_factory=None, vlm_client=None) implementing Toolbox:
- run_subtask: render one prompt from subtask.goal or operation plus parameters, the success conditions and expected_record_shape, asking for structured findings as JSON in the final message; start_url from registry SITES origin for registry subtasks or "https://<target_domain>/" for open ones; construct the agent with max_steps = budget.max_actions and the lent session id; run; build the report with build_worker_report(result, log_dir, prompt, request_id, subtask_id); then normalise: screenshot paths become observation ids registered with the session manager; strip session_id and session_replay_url from evidence into a private field that is not persisted; parse findings from the final message JSON when the backend returned None; map the four prose aborts to typed failures (exceeded max_steps -> BUDGET_EXCEEDED, model refused -> MODEL_REFUSED, browser session lost -> PRECONDITION_FAILED, unparseable outputs -> EXTRACTION_FAILED); ensure metrics has browser_action_count, elapsed_ms, model_call_count even for the Claude backend.
- Enforce budget.max_seconds with a watchdog thread that stops the agent and records BUDGET_EXCEEDED, since the worker has no wall-clock bound.
- vision_interpret(handle, question): one call to the configured VLM endpoint with the latest observation's screenshot and the question, returning the text; dom_interpret: raise PRECONDITION_FAILED. observe delegates to the session manager.

Tests with a fake agent and fake VLM client: a successful run with findings parsed from the final message; findings None preserved as empty list with an ambiguity in failures; each prose abort mapped; screenshot paths normalised; session id stripped from persisted evidence; watchdog fires; vision_interpret returns the fake's text; the report loads through WorkerReport.from_dict.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report files created, test count and time, what the precondition check found, anything unverified, git status --short, and confirm nothing was staged or committed.
```

### P3-INT2 — first connected run (Opus 5, high; run by Abel with keys)

```text
You are wiring the first connected ARGUS run in the repository at /Users/baiyangchen/Developer/Coding/Bots. Do not stage, commit or push. You may use the network only for the live commands listed at the end, and only when STEEL_API_KEY, OPENAI_API_KEY and ARGUS_MODEL are present in the environment; otherwise run everything offline and report the live commands as not run.

Read AGENTS.md, docs/ai/TEAM.md, docs/hackathon/ARGUS-HANDOFF-3.md, argus/__main__.py, argus/adapters/*.py and their tests, browser_worker/sites.json.

Edit only argus/__main__.py, argus/README.md, docs/ai/TEAM.md (INT-2 row and one update block), docs/ai/CURRENT.md (ARGUS paragraph).

- Add --live to the CLI: builds SteelSessionManager, DomToolbox, GhostDemoAdapter, ModelModerator over OpenAICompatibleClient, JsonStore; --fake remains the default. --live without the three environment variables exits 3 with a message naming them. Keep --interpreted and --plan-fixture working with --live.
- Offline: an end-to-end test that wires the real adapters with their fakes (fake Steel client, fake Worker, temp SQLite, FakeModelClient) through the Controller on the registry fixture and asserts a succeeded run with records, and a failed run with a typed failure when the fake worker returns AUTH_REQUIRED.
- Live (only if keys present): python3 -m argus --live --interpreted argus/examples/interpreted_request.json --store /tmp/argus-live-1 against the configured demo-catalog site. Record the exact result status, validation status, record count, session count and elapsed time. Do not retry on failure; report it.
- README: a "Live mode" section listing the environment names, what is real and what is still a fake or demo (Ghost demo validates only against its fixture catalog; visual worker not connected unless P3-VISUAL landed), and the currency caveat.
- TEAM: INT-2 row and block with only checks actually run.

Run python3 -m unittest discover -s argus/tests -v and git diff --check, then the live command if permitted. Report every command with observed output, files changed, anything unverified, git status --short, and confirm nothing was staged or committed.
```

## Questions to send now

The three owner question lists at the end of [ARGUS-CONNECTION-AUDIT.md](ARGUS-CONNECTION-AUDIT.md), plus one for Thomas about the moderator boundary described at the top of this file.
