# Team tasks and project tracking

Updated: 2026-09-13. Four teammates cover the assignments below. **From 2026-09-13 Abel owns and integrates all repository changes; every change to `main` goes through a pull request reviewed by someone other than its author** (user decision, see [DECISIONS.md](DECISIONS.md)). Sting's direct push `7e28641` predates this rule. ARGUS PR #10 is merged; CI on `main` was red after `7e28641` and is fixed on `fix/main-stabilize`.

| Person | Assigned workstream | Main responsibility | Next useful deliverable |
| --- | --- | --- | --- |
| Sting | Ghost API | Reusable procedures, matching, binding, qualification and skill lifecycle | Example trace → candidate → qualification → changed-input replay walkthrough |
| Thomas | VLM / subagent visual interpretation | Interpret screenshots, identify visual targets and verify visual outcomes | One Steel screenshot-to-action example with evidence and failure handling |
| Tianqi | HTML/codebase interpretation for subagents | Interpret available page structure/code, identify semantic targets and extract structured information | One HTML/DOM-to-action/result example with evidence and failure handling |
| Abel | ARGUS controller | Task interpretation and language parsing, acceptance gate, decomposition, dispatch, run state, budgets and session ownership | One complete task plan with worker inputs/outputs and the controller-to-moderator boundary |
| Thomas | Moderator | Report sufficiency, reconciliation across subtasks, final synthesis | Implement the three moderator callables proposed in [ARGUS.md](../hackathon/ARGUS.md) |

All four are working on their assigned areas according to the user. Reported implementation evidence is recorded below; unreported checks remain pending. See [CURRENT.md](CURRENT.md) for the current checkout.

## Progress board

Update this board in place. Detailed responsibilities are below; this table tracks the next deliverable and what is ready to connect. Dates remain unset until the team agrees them.

| ID | Deliverable | Responsible | Status | Dependency / next step | Evidence / branch |
| --- | --- | --- | --- | --- | --- |
| INT-1 | Shared example and interface agreement | All four; Abel coordinates ARGUS inputs/outputs | Planned | Agree the two-category example, contract translation, worker-owned Ghost calls and configuration in the [end-to-end plan](../hackathon/END-TO-END-PLAN.md) | 2026-09-13 source audit and proposed plan; receiver agreement pending |
| GHOST-1 | Ghost lifecycle and callable boundary | Sting | Building | Run the new DOM-first connection with live Steel/UI-TARS and verify receiver handoff | `ghostapi/` on `feat/ghost-api-interactive-demo`; local browser/HTTP lifecycle verified; see connection update |
| VLM-1 | Visual worker example and evidence report | Thomas | Building | Record an interaction-step example (click/type/scroll with semantic targets) that Ghost can compile; align report shape at INT-1 | `Agents/visual/` on main (merged as eb3095b); see update below |
| HTML-1 | HTML/code worker example and evidence report | Tianqi | Building | INT-1 agreement, live GPT/configured Steel task, then receiver checks | `Agents/browser_worker/`, tests; `codex/browser-worker-steel` rebased on `b4c9e76`; handoff below |
| ARGUS-1 | Task plan, routing and controller boundary | Abel | Building (re-review requested) | Tianqi's remaining finding showed that evidence-bound model prose could still make unsupported claims. Contract 0.5 removes moderator prose: the moderator selects validated records and fields, and the controller renders the answer. Wait for CI, then request Tianqi's re-review | PR #10; implementation commit `388764e`; 453 offline tests, Ruff and format checks pass |
| ARGUS-3 | Phase 3 connections: session manager, DOM adapter, Ghost adapter, moderator adapter, visual adapter, first connected run | Sting (assigned by Abel 2026-09-12) | Planned | Follow the proposed [end-to-end plan](../hackathon/END-TO-END-PLAN.md): bridge the existing worker/Ghost connection and structured moderator; older Phase 3 demo-adapter prompts need revision | Runtime adapters not started; 2026-09-13 plan below |
| MOD-1 | Moderator callables: assess, reconcile, synthesize | Thomas (module) / Sting (adapter, under ARGUS-3) | Building | `moderator/` merged (PR #8) is a run harness, not an `argus.interfaces.Moderator`; P3-MOD may reuse its selection logic but must return `AnswerSelection`, never publish its prose or dispatch work. Thomas to confirm the structured boundary | `moderator/` on main at `eaed4c7`; harness check on Hacker News reported values wrong vs screenshots (see its README) |
| INT-2 | First connected request with verified result | Sting runs it; Abel reviews | Planned | ARGUS-3 P3-SESSION + P3-DOM + P3-GHOST + P3-MOD, then P3-INT2 with STEEL_API_KEY, OPENAI_API_KEY, ARGUS_MODEL. Read-only decision workflow is finished at this point; execution and boundary handoff (DECISIONS) come after | Not run |
| INT-3 | Learning, qualification, reuse and repair connections | All four | Planned | INT-2, GHOST-1, fresh worker replays and controlled-site cases | Not run |
| DEMO-1 | Dashboard, controlled-site truth set and demo readiness | Tianqi (per Abel's report 2026-09-12; not yet confirmed by Tianqi in this doc) | Building | Controlled site needs two UI versions and a known result set for INT-3 repair and validation; dashboard consumes the ARGUS event stream (`events.jsonl`, `RunResult`) and must label fake runs as samples; use the evaluation checklist | Local, unreported |

### Main stabilization after `7e28641` — 2026-09-13 (Abel)

```text
Task ID / date / status: STAB-1 / 2026-09-13 / Building (pull request from fix/main-stabilize)
Artifact, branch/revision or local files: argus/requirements.txt, argus/demo_runtime.py, argus/api/service.py, argus/tests/test_demo_runtime.py, argus/live_runtime.py (lint only), frontend/dist/{index.html,app.js,styles.css}, docs/ai/{CURRENT,TEAM,DECISIONS}.md, argus/README.md, frontend/README.md
Module README / usage instructions: argus/README.md (Mission Control demo), frontend/README.md
Entry point + environment names: python -m argus.api; ARGUS_RUNTIME (live default, controlled = offline fixture), STEEL_API_KEY (live only), ARGUS_STORE, GHOST_DATABASE_PATH
Contract version + input/output/failure examples: unchanged 0.5 controller contracts. New: /api/health.runtime and each run's runtime field ("controlled"/"live", persisted via interpreted.model); unsupported requests such as "give me the best condo in toronto" return HTTP 422 "will not guess" instead of a rerouted Toronto itinerary
Checks run + result: unittest discover argus/tests 458 OK (Python 3.13.5); ruff check/format --check argus clean; node --check app.js; git diff --check; browser: controlled label shown, condo request rejected inline, shopping example completed with Fixture data badge. Live Steel mode not run by Abel.
Open issue / needed from / next action: CI on main is red until this merges. Tianqi: the scripted prototype dashboard was replaced by 7e28641 — decide whether anything is restored. Sting: ARGUS-3 adapters (DOM worker via Worker.run, structured moderator, Ghost candidate save) replace the hand-rolled live scraper. FastAPI pins still differ between ghostapi (<0.129) and Agents/browser_worker (==0.141.1); separate CI jobs hide this.
Receiver + connection check/result: none newly Integrated; this task changes labeling, routing and CI only.
```

### DEMO-1 connected controlled runtime - 2026-09-13

- Artifacts: `argus/demo_runtime.py`, `argus/api/`, controller worker-action
  events, `frontend/dist/`, module READMEs and `argus/tests/test_demo_runtime.py`.
- Behavior: Shopping uses two concurrent subagents; Travel and Job Search use
  research-to-details dependencies. The UI streams and inspects ARGUS, Ghost,
  subagent, worker-action and moderator events and reopens persisted conclusions.
- Boundary: real controller/contracts/store with controlled worker records and
  deterministic Ghost/moderator adapters. Live provider and receiver checks remain.
- Checks actually run: all three direct controller scenarios succeeded with passed
  validation (Shopping 2 selected records/41 events; Travel 6 itinerary stops/47
  events; Job Search 3 selected records/47 events). The natural-language demo/API
  suite passed 3 tests in 2.309s; the final full ARGUS suite passed 456 tests in
  3.375s. JavaScript
  syntax, Python compileall and whitespace passed. The running API returned healthy
  and the page returned HTTP 200. Computer-use browser QA was unavailable because
  this environment exposed no browser surface; responsive visual inspection remains.
- Run: `/Users/ddxsuperman/miniconda3/bin/python3.13 -m argus.api`, then open
  `http://127.0.0.1:4173`. Server was left running at this checkpoint.
- Natural-language follow-up: scenario buttons now load examples only. The backend
  classifies request text and extracts supported product/price, trip length/interests,
  and job filter/rank/limit semantics. A live HTTP request for "top 2 remote software
  engineering jobs by highest salary" classified as jobs, planned search -> details,
  passed validation and published exactly two ordered records. Unsupported domains
  and shopping categories return HTTP 422 instead of running a preset.

### End-to-end integration planning - 2026-09-13

- Tasks: INT-1, ARGUS-3, INT-2/3 and DEMO-1; planning only, assignments unchanged.
- Artifact: [end-to-end plan](../hackathon/END-TO-END-PLAN.md), CURRENT/DECISIONS and
  context entry link; `feat/ghost-api-interactive-demo`, inspected HEAD `35f6cb1`
  with existing staged work preserved. Runtime usage remains in the linked module
  READMEs; the plan labels proposed files and interfaces as unimplemented.
- Findings/checks: read controller/interfaces/contracts, worker policy/config/report
  and Ghost connection, service routes, moderator/frontend usage and UI source.
  ARGUS uses fake wiring; the UI uses scripted ticks; the live worker supports
  configured `search_extract`, not all open-world plan operations. No runtime tests,
  live provider calls or receiver integration checks were run for this plan. All 67
  local links in the six affected documents resolve; documentation whitespace checks
  passed, including the new plan file.
- Next: agree mapping, worker-resolved memory mode, validation ownership, model/site
  configuration and JSON/SQLite storage exception; implement one complete UI run,
  then parallel subtasks, learning/reuse, visual/repair and broader task support.
- Receivers: Abel for controller contracts, Thomas/Tianqi for worker/moderator and
  evidence, Sting for Ghost lifecycle; no connection newly marked Integrated.

### GHOST-1 update — 2026-09-12

```text
Task ID / date / status: GHOST-1 / 2026-09-12 / Building
Artifact, branch/revision or local files: ghostapi/api/, ghostapi/client.py, Agents/browser_worker/ghost*.py, visual adapter and related tests/docs on feat/ghost-api-interactive-demo at base 60a45ec; uncommitted
Module README / usage instructions: ghostapi/README.md; detailed connection guide ghostapi/INTEGRATION.md
Entry point + environment names: python -m ghostapi.api; python -m Agents.browser_worker.ghost_cli; GHOST_API_URL, GHOST_DATABASE_PATH; worker/provider settings in module READMEs
Contract version + input/output/failure examples: integrated 0.2, legacy 0.1 preserved; lookup/exploration/candidate/qualification/reuse; DOM-first same-session visual fallback; typed failure and inconclusive visual outputs
Checks run + result: final rerun passed 94 worker/connection tests (38.24s), 4 FastAPI tests, 12 demo Python tests, 5 JavaScript tests and visual parser check on Python 3.13.13/macOS; local Chrome + separate Ghost HTTP process verified exploration, three fresh qualification cases and changed-input reuse (zero replay model calls); scoped Ruff, compileall, whitespace and 115 local documentation links passed; refreshed editable install verified worker/Ghost/visual imports and graph asset outside the repo
Open issue / needed from / next action: explicit `--live-steel --request` path now guards hosted read-only tasks and emits sanitized phase events; live Steel/OpenAI/UI-TARS credentials, model endpoint and hosted site are still needed for the cloud receiver check; the DOM CLI has no browser viewport URL/screenshots yet; general canvas validation, auth, artifact retention, distributed locks, quarantine/repair and ARGUS/moderator integration remain unfinished
Receiver + connection check/result: Abel/Thomas/Tianqi connection check pending
```

### Worker–Ghost connection — 2026-09-12

Follow-up credential check: real Steel create/connect/open example.com/observe/release
passed in 4.353s using `Agents.browser_worker.demo.steel_sanity.run`. The live-model local
catalog attempt failed configuration validation before a model call: user-selected
`gpt-5.6-luna` / `low` is outside the current Settings allowlist. `.env` was not changed;
next step is agreement on model settings or separately authorized model support.
This is Steel-only evidence, not a live combined worker/Ghost or visual receiver check.

The user authorized this shared implementation, then required DOM-first routing with
visual fallback when DOM cannot handle the page. The local work touches GHOST-1, HTML-1
and VLM-1 without claiming a teammate's receiver check. The [connection guide](../../ghostapi/INTEGRATION.md)
and [DOM](../../Agents/browser_worker/README.md)/[visual](../../Agents/visual/README.md) READMEs
describe actual setup, inputs, outputs, failures, dependencies and limitations.

Tests consume real Chrome reports through the Ghost API and SQLite, plus simulate the
Steel/UI-TARS handoff to check same-session routing, single release, cancellation, input
origins and screenshot normalization. The standalone HTTP demo confirms candidate →
qualified → changed-input reuse. These are local connection checks; INT-2 still needs
ARGUS/moderator, and none of the team connections is marked Integrated. Next: configure
the live providers/site, have Thomas/Tianqi verify continuation, then Sting verify the
resulting candidate/qualification and Abel consume the final report.

### VLM-1 update — 2026-09-12

```text
Task ID / date / status: VLM-1 / 2026-09-12 / Building
Artifact, branch/revision or local files: Agents/visual/ on main (merged as eb3095b)
Module README / usage instructions: Agents/visual/README.md
Entry point + environment names: python -m browser_subagent "<subtask>" --url <start>; STEEL_API_KEY (required), UITARS_BASE_URL (default http://127.0.0.1:8080/v1), ANTHROPIC_API_KEY (claude backend only)
Contract version + input/output/failure examples: report.json is 0.1-provisional, aligned with CONTRACTS v0.1 ActionRecord/RunResult/metrics concepts. Real-browser observation-only example: Agents/visual/examples/hn-top-story/ (navigate -> one model call -> finished(); no interaction steps, semantic_target/findings null, report.json hand-edited to relocate evidence paths). Failure handling proven live: a mid-run Steel session timeout produced typed action failures and an honest failed outcome (fast-abort now added); unparseable model output aborts after 3 attempts.
Checks run + result: test_parser.py passed (offline, no steel-sdk needed); smoke_test.py passed (live Steel: navigate, 1280x800 screenshot, click/scroll/key, DOM element under cursor, network capture); 3-point vision grounding calibration on UI-TARS-1.5-7B Q4 (2/3 within 11px, coords in original pixel space — rescale removed accordingly); live mousemove probe measured Steel action space = full-window screenshot space at constant (4,87)px offset from page viewport — element_at/semantic_target now corrected by a per-session measured offset; end-to-end run succeeded on news.ycombinator.com — correct title + points verified against the run's own screenshot, 1 model call, 20s, session replay on Steel dashboard.
Open issue / needed from / next action: record an interaction-step example (click/type/scroll with corrected semantic targets) that Ghost can compile; claude backend untested. Driver model settled: UI-TARS-72B on a GPU node (set UITARS_BASE_URL, tunnel if remote) - measured zoom_rate 12/12 vs the 7B's 2/12 and 83% vs 58% correct on exact-value reads, so the 7B is a laptop fallback only.
Receiver + connection check/result: Abel (consume examples/hn-top-story/report.json as the worker-report sample), Sting (needs the upcoming interaction-step trace for compilation) — connection checks pending
```

### DEMO-1 UX prototype update — 2026-09-12

```text
Task ID / date / status: DEMO-1 / 2026-09-12 / Building
Artifact, branch/revision or local files: frontend/dist/{index.html,styles.css,app.js}, frontend/README.md; local UX review update on codex/browser-worker-steel; Sites source d651d058e1f677c99e6ee3e5519716ef84bd21c7; owner-private Sites version 4 at https://argus-mission-control.sunyihan666.chatgpt.site
Module README / usage instructions: frontend/README.md
Entry point + environment names: frontend/dist/index.html; no environment variables
Contract version + input/output/failure examples: simulated dashboard data only; no runtime contract connected
Checks run + result: JavaScript syntax and whitespace passed; browser checks passed for the proof-decision pass route (12 events), failed proof routing into visual recovery and fresh DOM verification (16 events), browser-unavailable retry (13 events), five results, responsive horizontal/vertical graph modes, and 390/820/1100/1440px body bounds. Prior synchronized history/evidence, before/after, pause/resume/cancel, run reopening, proof-link, Paper/Midnight, and keyboard-tab checks remain applicable. Browser console error log empty. Owner-private Sites version 4 deployment succeeded.
Open issue / needed from / next action: assign a dashboard owner; connect the prototype to agreed ARGUS run events and real evidence without changing its explicit demo/live distinction
Receiver + connection check/result: no runtime receiver check has run
```

### HTML-1 update — 2026-09-12, PR #7 review

```text
Task ID / date / status: HTML-1 / 2026-09-12 / Building
Artifact, branch/revision or local files: Agents/browser_worker/, tests/browser_worker/, pyproject.toml and .github/workflows/browser-worker.yml on codex/browser-worker-steel, rebased onto main b4c9e76
Module README / usage instructions: Agents/browser_worker/README.md and Agents/browser_worker/TESTING.md
Entry point + environment names: Worker.run (ARGUS in-process); optional uvicorn Agents.browser_worker.main:app; OPENAI_API_KEY, STEEL_API_KEY, WORKER_BROWSER, WORKER_BROWSER_EXECUTABLE, WORKER_SITES_FILE, WORKER_API_TOKEN (full settings in module README)
Contract version + input/output/failure examples: local 0.2 boundary pending INT-1 agreement; examples/subtask.json -> independently checked records/report, typed failed/inconclusive/needs_visual outcomes; no receiver compatibility assumed
Changes: preserve merged Ghost/visual docs, scope DOM choices, isolate pytest/Ruff, add Linux CI, abort ambient writes without failing the run, restrict model failure codes, document body-cache compatibility and portable browser setup
Checks run + result: 74 DOM tests passed (Python 3.12.14/Windows, 37.72s); Ruff lint/format and whitespace checks passed. Existing Ghost Python suite: 12 passed. Ghost/visual source and Ghost CI unchanged against main. Dedicated Linux CI added; remote result pending. Prior live Steel create/connect/observe/release sanity passed (Example Domain, 5.671s); not rerun for review. No live GPT/configured-site task verified.
Open issue / needed from / next action: agree boundary and shared toolbox at INT-1; Thomas/Tianqi converge adapters and ownership, then test needs_visual compatibility; configure hosted read-only site and run real GPT task
Receiver + connection check/result: Abel (ARGUS), Sting (Ghost traces), Thomas (moderator/visual) pending; HTML-1 is not Integrated
```

### ARGUS-1 contract 0.5 review correction — 2026-09-13

```text
Task ID / date / status: ARGUS-1 / 2026-09-13 / Building (review correction pushed; CI and re-review pending)
Artifact, branch/revision or local files: feat/argus-controller-base; implementation commit 388764e on PR #10 plus this documentation checkpoint.
Module README / usage instructions: argus/README.md
Entry point + environment names: python3 -m argus "<request text>" [--fake | --no-fake] [--store DIR] [--request-id ID] [--interpreted FILE] [--plan-fixture FILE]; OPENAI_API_KEY, OPENAI_BASE_URL and ARGUS_MODEL apply only to live model use.
Contract version + input/output/failure examples: 0.5-argus-draft. Moderator.synthesize returns AnswerSelection(record_indices, Claim(record_index, fields), Note(kind, subject)); FinalAnswer contains only controller-rendered lines plus controller-derived records, structured claims, failures and notes. argus/examples/answer_selection.json and final_answer.json show the boundary. Invalid indices, fields or note subjects fail closed; moderator prose is discarded and only discarded field paths are logged.
Changes: removed moderator-authored user-facing prose; bound every claim to selected validated-record fields; require selected records to retain evidence from the current run; render lines deterministically in the controller; added the exact hostile phrase regression; cleared the ARGUS Ruff baseline and made the workflow lint job blocking. Reconciliation remains record selection only.
Checks run + result: python3 -m unittest discover -s argus/tests -v — 453 tests in 1.249s, OK, Python 3.13.5. ruff check argus, ruff format --check argus and git diff --check passed. Offline registry and salary-chain CLI runs succeeded; vague open request ended needs_input at S4 with no session. No live model, browser, Steel, VLM, DNS or Ghost check was run.
Open issue / needed from / next action: wait for the four-platform PR matrix and blocking lint, then reply to Tianqi with the hostile-prose and contract test names. Run a live interpretation/toolbox check when credentials and the connected adapters are available.
Receiver + connection check/result: Thomas/Sting must implement the structured AnswerSelection boundary; Tianqi re-review pending; nothing is Integrated.
```

### ARGUS-1 phase 1b update — 2026-09-12 (open-world navigation, offline)

```text
Task ID / date / status: ARGUS-1 / 2026-09-12 / Ready to connect (open-world navigation built and checked offline; no live toolbox, model or Ghost)
Artifact, branch/revision or local files: feat/argus-controller-base at 7641f87 plus uncommitted changes. Phase 1b (prompts E0, E, F, 2b) covers argus/model_client.py, interpreter.py, gate.py, planner.py, controller.py, fakes.py, __main__.py, their test modules, tests/test_end_to_end.py and the open-world fixtures in argus/examples/. This 2b step changed argus/__main__.py, argus/fakes.py, argus/controller.py, argus/gate.py (docstring), argus/tests/test_end_to_end.py, argus/tests/test_fakes.py and argus/README.md. Nothing is staged or committed.
Module README / usage instructions: argus/README.md
Entry point + environment names: python3 -m argus "<request text>" [--fake | --no-fake] [--store DIR] [--request-id ID] [--interpreted FILE] [--plan-fixture FILE]; OPENAI_API_KEY and OPENAI_BASE_URL are read by the openai SDK itself and ARGUS_MODEL names the model with no default. --interpreted and --plan-fixture need none of them.
Contract version + input/output/failure examples: 0.3-argus-draft, unchanged by this step. Input argus/examples/interpreted_request_open_salary.json planned by argus/examples/plan_open_chain.json through FakePlannerClient -> succeeded with 4 records, unique by url, remote-only, salary descending, each claim citing the observation of the page it was read from, one stored report per subtask. Failures exercised end to end: interpreted_request_open.json -> needs_input on S4 with the approved question and no session opened; target_domain 127.0.0.1 and intranet.corp -> failed DOMAIN_NOT_ALLOWED before any session; a five-step plan -> failed PLAN_TOO_LARGE with the toolbox never called. All evidence is synthetic: no browser, no model call, no Steel.
Changes: fixed StubModerator.reconcile, which concatenated every accepted report so each job in a search-then-open_results chain was answered twice; it now merges by record url with the later, more detailed record superseding the earlier one and no-url records untouched. Controller now carries the gate's own rejection codes (S2 -> DOMAIN_NOT_ALLOWED, S5 -> ACTION_CLASS_NOT_ALLOWED) instead of flattening them to INVALID_INPUT; every other rejection is unchanged. CLI --plan-fixture documented and hardened: it requires --interpreted, wraps a Plan JSON in argus.fakes.FakePlannerClient, injects it through Controller(plan=...), reports unreadable fixtures as usage errors, and is never substituted for a real model call. Stale ANTHROPIC_API_KEY docstring in __main__.py replaced by the three OpenAI-compatible names. Exit codes unchanged.
Checks run + result: python3 -m unittest discover -s argus/tests -v -- 419 tests in 0.880s, OK, Python 3.13.5 (397 before this step; 22 new reconcile, open-world end-to-end and CLI tests). git diff --check -- clean. python3 -m argus --fake --interpreted argus/examples/interpreted_request_open.json --store /tmp/argus-open-a -- needs_input, gate S4, exit 1, store held run.json and events.jsonl and no report. python3 -m argus --fake --interpreted argus/examples/interpreted_request_open_salary.json --plan-fixture argus/examples/plan_open_chain.json --store /tmp/argus-open-b -- succeeded, exit 0, 4 records unique by url (jobs/3 210000, jobs/1 185000, jobs/6 160000, jobs/5 132000, all remote), validation passed, one report per subtask. python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo -- unchanged registry result: succeeded, 2 cited claims, validation passed. No live browser, model, VLM, DNS or Ghost check was run.
Open issue / needed from / next action: openai 2.14.0 is installed locally but no live call has ever been made, so interpretation and open planning against a real endpoint are unverified. Ghost.validate gained an ARGUS-local report_context keyword for open subtasks that Sting has not seen or agreed. The live toolbox still owes DNS/connection/redirect/rebinding and request enforcement, execution-time blocking of login/payment/submission actions, and four-slot VLM arbitration across runs and callers; per-run concurrency is not a machine-wide limit. Next action for Abel: review argus/README.md and this block, then commit the branch.
Receiver + connection check/result: Thomas (Moderator against argus/interfaces.py, replacing StubModerator including its reconcile merge rule; Toolbox with Tianqi), Sting (Ghost match/validate/compile plus the new report_context keyword) -- connection checks pending, nothing is Integrated.
```

### ARGUS-1 Phase 0b review correction — 2026-09-12

```text
Task ID / date / status: ARGUS-1 / 2026-09-12 / Building (open-world contracts built; E/F/2b pending)
Artifact, branch/revision or local files: feat/argus-controller-base at 7641f87 + uncommitted changes. argus/contracts.py, registry.py, controller.py, planner.py (budget recheck only), tests/test_contracts.py, tests/test_controller.py, new tests/test_domain_policy.py; four new open-world fixtures in argus/examples/. Relevant ARGUS design/implementation and AI handoff docs updated.
Module README / usage instructions: argus/README.md, Open-world contract checkpoint (Phase 0b)
Contract version + examples: 0.3-argus-draft; original payloads load unchanged via defaults. Subtask additionally carries target_domain/goal/criteria/expected_record_shape for downstream consumers. Caps and binding shapes are checked. interpreted_request_open.json has ambiguous best; plan_open_chain.json illustrates a separate clarified salary-ranked request.
Changes: enforce 1–4 integer worker concurrency per run; bound open/mixed plans to four total subtasks and forbid raised/malformed caps; add strict criterion/context/input-binding checks; public docs allowed, private/local targets rejected offline. User confirmed local VLM capacity is the hardware reason, total plan size is a separate MVP budget, ten results may be opened sequentially by one subtask, and clarification questions should give example answers.
Checks run + result: python3 -m unittest discover -s argus/tests -v — 261 tests in 0.751s, OK. Focused contract/concurrency and domain tests passed during implementation. No live browser/VLM/DNS/model checks; CLI exercised by existing offline suite only.
Open issue / next action: E implements interpretation/gate and helpful ranking clarifications. F implements model plans, accepted-context copying/checking, dependent findings transfer and open graph/depth validation; then 2b integration. Live toolbox must enforce four local VLM slots across runs/callers plus DNS/connection/redirect/request and action controls. Per-run concurrency is not a machine-wide limiter. Existing Ghost validate boundary carries only evidence refs; explicit empty-state evidence representation still needs settling in F/INT-1.
Receiver + connection check/result: Abel review pending; Thomas/Tianqi/Sting live connections unverified, nothing Integrated. No staging or commits.
```

### ARGUS-1 base update — 2026-09-12 (historical phases 0–2)

```text
Task ID / date / status: ARGUS-1 / 2026-09-12 / Ready to connect
Artifact, branch/revision or local files: argus/ — 11 package modules, 7 JSON fixtures in argus/examples/ and 9 test modules in argus/tests/, committed on branch feat/argus-controller-base from main 12aba7d. Phases 0-2 of docs/hackathon/ARGUS-IMPLEMENTATION.md; phase 2 added argus/__main__.py, argus/tests/test_end_to_end.py and argus/README.md.
Module README / usage instructions: argus/README.md
Entry point + environment names: python3 -m argus "<request text>" [--fake | --no-fake] [--store DIR] [--request-id ID] [--interpreted FILE]; ANTHROPIC_API_KEY and ARGUS_MODEL (interpretation only; --interpreted needs neither)
Contract version + input/output/failure examples: contracts SCHEMA_VERSION 0.2-argus-draft, unchanged by this phase. Input argus/examples/interpreted_request.json; output the RunResult printed by the CLI run below (2 records, 2 claims each citing observation-000.png, validation passed); failures exercised end to end: AUTH_REQUIRED carried unchanged into a failed run, G4 clarify ending needs_input with the question and no session opened, VALIDATION_FAILED that the moderator's accept does not override, PRECONDITION_FAILED when live interpretation is attempted without the anthropic SDK. All evidence is synthetic: no browser, no model call, no Steel.
Checks run + result: python3 -m unittest discover -s argus/tests -v — 230 tests, OK, Python 3.13.5 (was 205 before this phase; 25 new end-to-end and CLI tests). python3 -m argus --fake --interpreted argus/examples/interpreted_request.json --store /tmp/argus-demo — status succeeded, exit 0, and the store held index.json, runs/<run_id>/run.json, events.jsonl, reports/subtask-1.json and skills/demo-catalog.search-products/v1.json. python3 -m argus --no-fake "find headphones" — exit 3 with the "no real toolbox is connected" message. python3 -m argus "Find headphones under $150 in the demo catalog" — failed with PRECONDITION_FAILED (anthropic not installed here), as intended rather than crashing.
Open issue / needed from / next action: two integration fixes made in this phase, both inside argus/ and with no contract change — StubModerator now counts the controller's own verification as evidence (previously a thin-evidence report was verified, assessed again, and failed on the verification cap), and the controller no longer flattens a mapping-shaped Ghost checks payload into bare names. Needed: Thomas's Moderator (and Toolbox with Tianqi) and Sting's Ghost implemented against argus/interfaces.py; SubtaskInput carries run_id but no request_id, which INT-1 should settle. Next action for Abel: review argus/README.md and this block, then commit on a branch and open a pull request (review gate 3 of the plan).
Receiver + connection check/result: Thomas (implement Moderator against argus/interfaces.py and argus/examples/moderator_decision_accept.json; replace StubModerator), Sting (implement Ghost match/validate/compile; replace FakeGhost), Thomas/Tianqi (Toolbox) — connection checks pending, nothing is Integrated
```

Status meanings: **Planned** = identified; **Researching** = approach under investigation; **Building** = implementation reported; **Ready to connect** = handoff checklist complete; **Integrated** = receiver has run the connection check; **Blocked** = named missing dependency; **Needs owner** = not assigned. Unreported checks remain pending. Record a blocker as “missing input — needed from whom — work that can continue.”

## INT-1 — agree before connecting components

### Mission Control live slice — 2026-09-13

Status: **Building**. Local files on `feat/ghost-api-interactive-demo` add
`argus/live_runtime.py`, `argus/api/`, and the event-driven `frontend/dist/` UI.
Run `python -m argus.api` with `STEEL_API_KEY`; see
[ARGUS usage](../../argus/README.md) and [UI usage](../../frontend/README.md).
Natural-language Shopping, Toronto Travel, and remote Job Search plans now open real
Staples, Wikivoyage, and Remotive pages in Steel and stream ARGUS/subagent/Ghost/action/
moderator events. Live Shopping and Job Search passed validation; Travel extracted
six section-aware records per pass and selected four distinct stops. `python -m unittest
discover -s argus/tests -p 'test_*.py'` passed 456 tests in 3.597s; compileall,
JavaScript syntax, and whitespace checks passed earlier in the same worktree.
Remaining: Ghost replay/candidate qualification, visual fallback, screenshot files,
and arbitrary-site support. No receiver has marked the combined connection Integrated.

Read-only connection audit of all three packages against `argus/interfaces.py` (2026-09-12): [ARGUS-CONNECTION-AUDIT.md](../hackathon/ARGUS-CONNECTION-AUDIT.md). Phase 3 adapter prompts and order: [ARGUS-HANDOFF-3.md](../hackathon/ARGUS-HANDOFF-3.md). Note: the merged `moderator/` module is a run harness (dispatch, monitoring, aggregation, synthesis) and does not implement `argus.interfaces.Moderator`. Its prose synthesis cannot be published; P3-MOD must parse or replace it with the contract 0.5 `AnswerSelection` of validated records, fields and fixed notes. Two items need owner changes, not adapters: the visual worker cannot accept an ARGUS-owned session, and Ghost validates in CAD while ARGUS and the DOM worker fix USD. No moderator adapter exists yet.

Use the [provisional contract](../hackathon/CONTRACTS.md) as a reference. Its existing fake types do not yet settle moderator/subtask or visual-target messages.

- [ ] One concrete request, interpreted parameters, expected records, success conditions and supported site/workflow scope.
- [ ] Callable entry points and sample success/failure messages between ARGUS, the two interpretation paths and Ghost; agree request/run/subtask IDs and contract version.
- [ ] Shared evidence/action format: current observation references, parameter origins, semantic/visual targets and failure reasons. Decide how the two interpretation paths hand over within a browser session.
- [ ] Browser-session lifecycle responsibility, time/action limits, retry/fallback limits, and one owner for each shared code/config file before concurrent edits.

Record the agreed shapes in CONTRACTS and versioned examples when approved. Keep sample messages clearly labeled. Everyone can research and prototype against those examples without waiting for every component to finish. Record architectural choices in [DECISIONS.md](DECISIONS.md), rather than inventing separate contracts in each workstream.

HTML-1's implemented local 0.2 boundary is an INT-1 proposal, not a competing shared contract. ARGUS uses `Worker.run` in-process; FastAPI is optional transport. Thomas and Tianqi must converge the currently separate DOM/visual adapters into the shared Steel toolbox, agree session ownership and verify `needs_visual` compatibility before claiming integration. Coordination and receiver verification remain pending.

## Handoff — ready to connect

Before changing a task to **Ready to connect**, its producer supplies:

- [ ] Artifact location and exact branch/revision, or exact local files if still uncommitted.
- [ ] Module `README.md` updated with actual setup, usage, inputs/outputs/failures, dependencies, run/test commands and limitations; link it in the handoff.
- [ ] One entry point/run command, required environment-variable names, and relevant dependency versions; no secrets.
- [ ] Contract version plus one representative input, output and failure example. State whether evidence is synthetic or from a real browser.
- [ ] Focused checks actually run and their outcomes, known limitations, and the receiving teammate's next connection check.

Use this compact update beside the relevant task, replacing stale details:

```text
Task ID / date / status:
Artifact, branch/revision or local files:
Module README / usage instructions:
Entry point + environment names:
Contract version + input/output/failure examples:
Checks run + result:
Open issue / needed from / next action:
Receiver + connection check/result:
```

The receiver marks **Integrated** only after consuming the artifact successfully and recording the tested revision/result. Component completion alone does not establish that the connection works.

## Connection checkpoints

1. **INT-2: first complete path.** ARGUS sends the agreed request to the first ready browser worker; the worker returns records, trace and evidence; Ghost checks the output; the moderator returns one evidence-backed result. Also demonstrate a typed failure reaching the final result honestly. Start as soon as this slice is ready.
2. **INT-2: second interpretation path.** Connect the other worker through the same agreed boundary. Verify routing or handover and compatible evidence. A canvas test is needed only if canvas support is in the selected workflow.
3. **INT-3: lifecycle.** Pass a real successful trace to Ghost, retain candidate status until separate fresh qualification replays pass, then bind changed inputs and verify fresh results. Task success and qualification stay distinct.
4. **INT-3: recovery.** On the controlled site, observe the old version's failure, attempt bounded repair, qualify the new version, and demonstrate honest unsupported-change handling.
5. **DEMO-1: full product.** Once assigned, connect the dashboard and verify the [evaluation scenarios](../hackathon/EVALUATION.md), measured costs, clean-start instructions and backup recording. The UI must distinguish samples from real runs.

Abel integrates branches for each checkpoint (user decision 2026-09-13). Producers prepare their scoped changes, and the integrator records the combined revision. This board does not authorize staging, commits, pushes or releases. Shared-file changes must be coordinated so teammates do not overwrite each other.

## Sting — Ghost API

- Define skill inputs, supported operation, steps, checks and evidence.
- Define how successful worker action traces become parameterized candidates, rejecting ambiguous value origins.
- Specify strict binding/matching and fresh-session qualification with changed inputs and an evidenced empty result.
- Define versioning, quarantine and bounded repair acceptance; preserve the original checks and previous versions.

Handoff: tell Thomas and Tianqi what action/observation evidence compilation requires. Give Abel match decisions, candidate/qualification outcomes and failure reasons. Use the [provisional contract](../hackathon/CONTRACTS.md) as a starting reference, with changes discussed explicitly.

### Sting — phase 3 connections (ARGUS-3, assigned 2026-09-12)

Owns connecting the real components to the ARGUS controller through `argus/interfaces.py`, using the prompts in [ARGUS-HANDOFF-3.md](../hackathon/ARGUS-HANDOFF-3.md) and the gaps in [ARGUS-CONNECTION-AUDIT.md](../hackathon/ARGUS-CONNECTION-AUDIT.md). Rules: adapters live under `argus/adapters/`; no edits to `Agents/visual/`, `Agents/browser_worker/` or `moderator/` without their owner; offline tests with fakes; Abel reviews and commits. Decide CAD vs USD before P3-GHOST. Ask Thomas for the visual worker's session-injection change and for agreement on the moderator boundary. Target: INT-2, the first connected read-only run with a validated answer.

## Thomas — VLM / visual interpretation

- Research the vision-capable browser worker's integration with Steel screenshots and browser actions.
- Identify visual controls/content, propose actions, and verify effects from fresh observations.
- Report ambiguous targets, unreadable content and unsupported states explicitly.
- Record visual target meaning and screenshot/action evidence for Ghost; avoid treating old click coordinates as permanent locators.

Handoff: provide an example worker report to Abel and a trace/evidence example to Sting. Coordinate with Tianqi on when page structure is sufficient and when visual interpretation is needed. The [canvas feasibility experiment](../hackathon/RESEARCH.md) is a suggested first check, not a completed task or an expansion to general canvas support.

## Tianqi — HTML/codebase interpretation

Current implementation and review evidence is in the HTML-1 update above; see [module usage](../../Agents/browser_worker/README.md) and [testing runbook](../../Agents/browser_worker/TESTING.md).

- Define which HTML/DOM and available code sources the subagent consumes and how it interprets them.
- Identify semantic controls, parameter fields, navigation context and result records.
- Propose browser actions and extract structured outputs with source evidence and explicit limitations.
- Detect insufficient page structure and provide context for Thomas's visual path.

Handoff: provide an example worker report to Abel and a trace/evidence example to Sting. Coordinate semantic target descriptions and output fields with Thomas. Clarify whether “codebase interpretation” means rendered HTML/DOM, page-delivered scripts, or a repository explicitly available to the team; do not assume access to a public website's private source repository.

## Abel — ARGUS controller

- Interpret one user task, including natural language, into an overall request with parameters tied to their text origins; gate it as accept, clarify or reject.
- Decompose into subtasks with dependencies and concurrency groups; route to HTML/DOM interpretation, visual interpretation or qualified Ghost procedures.
- Own run state, stage transitions, budgets, event stream, browser-session lifecycle and the single terminal result.
- Call the moderator at fixed stages and execute its decisions; agree shared worker reports and failure/fallback behavior with the other workstreams.

Handoff: provide one worked task example showing each worker's input, expected output, evidence and failure report, plus the controller-to-moderator boundary. Draft in [ARGUS.md](../hackathon/ARGUS.md).

## Thomas — moderator

Assigned by the user on 2026-09-12. Whether Thomas also keeps the VLM-1 visual interpretation deliverable has not been confirmed; the row above is unchanged until it is.

- Implement `assess_report`, `reconcile` and `synthesize` as proposed in [ARGUS.md](../hackathon/ARGUS.md), returning decisions from fixed sets.
- Do not hold run state, sessions or budgets; the controller executes every decision.
- Agree the `ModeratorDecision` shape and the retry/verification caps with Abel during INT-1.

## Shared boundaries and remaining ownership

Use the same read-only search/filter/extraction example for all four deliverables. Agree one compatible worker-report shape containing subtask identity, outcome, findings, source evidence, observed actions/parameter origins, available measurements and failures. This is a planning requirement, not a new frozen schema.

Thomas and Tianqi build the connection between subagents and Steel as one shared toolbox exposing browser actions, HTML/DOM interpretation and visual interpretation; sessions are opened, lent and closed by the ARGUS controller. Per the PR #7 review their separate local adapters (`Agents/visual/`, `Agents/browser_worker/`) still need convergence; agree the shared adapter boundary, lifecycle ownership and file ownership before simultaneous edits. Sting consumes both kinds of evidence; Abel coordinates execution with Thomas's moderator.

Dashboard, controlled-site construction, presentation/backup recording and release work have not been assigned explicitly. Allocate those separately; do not infer ownership from the removed A–D plans. Thomas/Tianqi still need to agree the concrete shared Steel session-lifecycle implementation.

## Updating this document

Keep assignments, task status, blockers and connection evidence here. Update your row after a meaningful checkpoint or before handing work to someone else. Keep entries concise and link larger artifacts. Update CURRENT only for a project-wide change or completed integration; keep architectural choices in [DECISIONS.md](DECISIONS.md). An AI records only the work/evidence it actually received or produced and does not mark other people's tasks complete by inference.
