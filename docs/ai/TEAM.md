# Team tasks and project tracking

Updated: 2026-09-12. Four teammates cover the assignments below. HTML-1 has a local implementation handoff; the merged GHOST-1 and VLM-1 status/evidence is preserved. The PR #7 review clarifies the Abel/controller and Thomas/moderator split below; this is not new implementation evidence.

| Person | Assigned workstream | Main responsibility | Next useful deliverable |
| --- | --- | --- | --- |
| Sting | Ghost API | Reusable procedures, matching, binding, qualification and skill lifecycle | Example trace → candidate → qualification → changed-input replay walkthrough |
| Thomas | VLM / subagent visual interpretation | Interpret screenshots, identify visual targets and verify visual outcomes | One Steel screenshot-to-action example with evidence and failure handling |
| Tianqi | HTML/codebase interpretation for subagents | Interpret available page structure/code, identify semantic targets and extract structured information | One HTML/DOM-to-action/result example with evidence and failure handling |
| Abel | ARGUS controller | Task interpretation, decomposition, routing and worker coordination | One complete task plan with worker inputs/outputs |
| Thomas | Moderator | Monitor progress, aggregate evidence, reason over outputs and synthesize the final result | Moderator walkthrough against the shared worker report |

All four are working on their assigned areas according to the user. Reported implementation evidence is recorded below; unreported checks remain pending. See [CURRENT.md](CURRENT.md) for the current checkout.

## Progress board

Update this board in place. Detailed responsibilities are below; this table tracks the next deliverable and what is ready to connect. Dates remain unset until the team agrees them.

| ID | Deliverable | Responsible | Status | Dependency / next step | Evidence / branch |
| --- | --- | --- | --- | --- | --- |
| INT-1 | Shared example and interface agreement | All four; Abel coordinates ARGUS inputs/outputs | Planned | Walk through one request and agree the checklist below | Not reported |
| GHOST-1 | Ghost lifecycle and callable boundary | Sting | Building | Connect the fixture boundary to a real worker trace; local demo remains simulated | `ghostapi/` on `feat/ghost-api-interactive-demo`; 12 Python and 5 JavaScript tests passed |
| VLM-1 | Visual worker example and evidence report | Thomas | Ready to connect | Abel/Sting run the connection check against `workers/visual/examples/hn-top-story/`; align report shape at INT-1 | `workers/visual/`; see update below |
| HTML-1 | HTML/code worker example and evidence report | Tianqi | Building | INT-1 agreement, live GPT/configured Steel task, then receiver checks | `browser_worker/`, tests; `codex/browser-worker-steel` rebased on `b4c9e76`; handoff below |
| ARGUS-1 | Task plan, routing and moderator walkthrough | Abel | Researching | Define worker inputs/outputs using INT-1; use samples while workers develop | Not reported |
| INT-2 | First connected request with verified result | All four | Planned | INT-1, ARGUS-1, one callable worker and Ghost validation; connect early | Not run |
| INT-3 | Learning, qualification, reuse and repair connections | All four | Planned | INT-2, GHOST-1, fresh worker replays and controlled-site cases | Not run |
| DEMO-1 | Dashboard, controlled-site truth set and demo readiness | Unassigned | Building | Interactive dashboard prototype exists; assign an owner and connect it to agreed runtime events and the controlled site | `frontend/dist/`; owner-private Sites v4 deployed with decision-oriented flow chart; simulated data only |

### GHOST-1 update — 2026-09-12

```text
Task ID / date / status: GHOST-1 / 2026-09-12 / Building
Artifact, branch/revision or local files: ghostapi/ on feat/ghost-api-interactive-demo
Module README / usage instructions: ghostapi/README.md and ghostapi/DEVELOPMENT.md
Entry point + environment names: python3 ghostapi/demo/live_demo.py; no environment variables for the fixture demo
Contract version + input/output/failure examples: simplified 0.1 fixture; demo-catalog search task, structured result, typed unsupported-discovery failure
Checks run + result: 12 Python tests passed; 5 JavaScript pointer/flowchart tests passed; Python coverage 72%; flowchart line coverage 97.18% and branch coverage 91.30%
Open issue / needed from / next action: discovery and replay are simulated; consume a real normalized browser trace and execute through the agreed worker boundary
Receiver + connection check/result: Abel/Thomas/Tianqi connection check pending
```

### VLM-1 update — 2026-09-12

```text
Task ID / date / status: VLM-1 / 2026-09-12 / Ready to connect
Artifact, branch/revision or local files: workers/visual/ on main
Module README / usage instructions: workers/visual/README.md
Entry point + environment names: python -m browser_subagent "<subtask>" --url <start>; STEEL_API_KEY (required), UITARS_BASE_URL (default http://127.0.0.1:8080/v1), ANTHROPIC_API_KEY (claude backend only)
Contract version + input/output/failure examples: report.json is 0.1-provisional, aligned with CONTRACTS v0.1 ActionRecord/RunResult/metrics concepts. Real-browser example: workers/visual/examples/hn-top-story/ (report.json + observation-000.png). Failure handling proven live: a mid-run Steel session timeout produced typed action failures and an honest failed outcome (fast-abort now added); unparseable model output aborts after 3 attempts.
Checks run + result: test_parser.py passed; smoke_test.py passed (live Steel: navigate, 1280x800 screenshot, click/scroll/key, DOM element under cursor, network capture); 3-point vision grounding calibration on UI-TARS-1.5-7B Q4 (2/3 within 11px, coords in original pixel space); end-to-end run succeeded on news.ycombinator.com — correct title + points verified against the run's own screenshot, 1 model call, 20s, session replay on Steel dashboard.
Open issue / needed from / next action: local UI-TARS answers can drift to Chinese without an explicit English instruction (now added); Q4 grounding is ~10-40px on sparse synthetic images — F16 on the 36GB Mac (set UITARS_BASE_URL) is the upgrade path if precision limits real tasks. Next: multi-step task on the controlled site once it exists.
Receiver + connection check/result: Abel (consume examples/hn-top-story/report.json as the worker-report sample), Sting (same run's actions/evidence as compilation input) — connection checks pending
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
Artifact, branch/revision or local files: browser_worker/, tests/browser_worker/, pyproject.toml and .github/workflows/browser-worker.yml on codex/browser-worker-steel, rebased onto main b4c9e76
Module README / usage instructions: browser_worker/README.md and browser_worker/TESTING.md
Entry point + environment names: Worker.run (ARGUS in-process); optional uvicorn browser_worker.main:app; OPENAI_API_KEY, STEEL_API_KEY, WORKER_BROWSER, WORKER_BROWSER_EXECUTABLE, WORKER_SITES_FILE, WORKER_API_TOKEN (full settings in module README)
Contract version + input/output/failure examples: local 0.2 boundary pending INT-1 agreement; examples/subtask.json -> independently checked records/report, typed failed/inconclusive/needs_visual outcomes; no receiver compatibility assumed
Changes: preserve merged Ghost/visual docs, scope DOM choices, isolate pytest/Ruff, add Linux CI, abort ambient writes without failing the run, restrict model failure codes, document body-cache compatibility and portable browser setup
Checks run + result: 74 DOM tests passed (Python 3.12.14/Windows, 37.72s); Ruff lint/format and whitespace checks passed. Existing Ghost Python suite: 12 passed. Ghost/visual source and Ghost CI unchanged against main. Dedicated Linux CI added; remote result pending. Prior live Steel create/connect/observe/release sanity passed (Example Domain, 5.671s); not rerun for review. No live GPT/configured-site task verified.
Open issue / needed from / next action: agree boundary and shared toolbox at INT-1; Thomas/Tianqi converge adapters and ownership, then test needs_visual compatibility; configure hosted read-only site and run real GPT task
Receiver + connection check/result: Abel (ARGUS), Sting (Ghost traces), Thomas (moderator/visual) pending; HTML-1 is not Integrated
```

Status meanings: **Planned** = identified; **Researching** = approach under investigation; **Building** = implementation reported; **Ready to connect** = handoff checklist complete; **Integrated** = receiver has run the connection check; **Blocked** = named missing dependency; **Needs owner** = not assigned. Unreported checks remain pending. Record a blocker as “missing input — needed from whom — work that can continue.”

## INT-1 — agree before connecting components

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

Choose one person to integrate branches for each checkpoint; that Git/release role is not assigned yet. Producers prepare their scoped changes, and the integrator records the combined revision. This board does not authorize staging, commits, pushes or releases. Shared-file changes must be coordinated so teammates do not overwrite each other.

## Sting — Ghost API

- Define skill inputs, supported operation, steps, checks and evidence.
- Define how successful worker action traces become parameterized candidates, rejecting ambiguous value origins.
- Specify strict binding/matching and fresh-session qualification with changed inputs and an evidenced empty result.
- Define versioning, quarantine and bounded repair acceptance; preserve the original checks and previous versions.

Handoff: tell Thomas and Tianqi what action/observation evidence compilation requires. Give Abel match decisions, candidate/qualification outcomes and failure reasons. Use the [provisional contract](../hackathon/CONTRACTS.md) as a starting reference, with changes discussed explicitly.

## Thomas — VLM / visual interpretation

- Research the vision-capable browser worker's integration with Steel screenshots and browser actions.
- Identify visual controls/content, propose actions, and verify effects from fresh observations.
- Report ambiguous targets, unreadable content and unsupported states explicitly.
- Record visual target meaning and screenshot/action evidence for Ghost; avoid treating old click coordinates as permanent locators.

Handoff: provide an example worker report to Abel and a trace/evidence example to Sting. Coordinate with Tianqi on when page structure is sufficient and when visual interpretation is needed. The [canvas feasibility experiment](../hackathon/RESEARCH.md) is a suggested first check, not a completed task or an expansion to general canvas support.

## Tianqi — HTML/codebase interpretation

Current implementation and review evidence is in the HTML-1 update above; see [module usage](../../browser_worker/README.md) and [testing runbook](../../browser_worker/TESTING.md).

- Define which HTML/DOM and available code sources the subagent consumes and how it interprets them.
- Identify semantic controls, parameter fields, navigation context and result records.
- Propose browser actions and extract structured outputs with source evidence and explicit limitations.
- Detect insufficient page structure and provide context for Thomas's visual path.

Handoff: provide an example worker report to Abel and a trace/evidence example to Sting. Coordinate semantic target descriptions and output fields with Thomas. Clarify whether “codebase interpretation” means rendered HTML/DOM, page-delivered scripts, or a repository explicitly available to the team; do not assume access to a public website's private source repository.

## Abel — ARGUS controller

- Interpret one user task into an overall request, subtasks, dependencies and success conditions.
- Define routing to HTML/code interpretation, visual interpretation and qualified Ghost procedures.
- Coordinate with Thomas's moderator on monitoring, evidence aggregation and final synthesis.
- Agree shared worker reports, run state, budgets and failure/fallback behavior with the other workstreams.

Handoff: provide one worked task example showing each worker's input, expected output, evidence and failure report. Agree the controller/moderator boundary with Thomas at INT-1.

## Thomas — Moderator

Monitor subtask progress, gather worker evidence, handle gaps/conflicts and synthesize the final result. Coordinate inputs/outputs with Abel's controller; the PR review identifies this ownership, not a verified connection. Moderator implementation and receiver checks are not reported here.

## Shared boundaries and remaining ownership

Use the same read-only search/filter/extraction example for all four deliverables. Agree one compatible worker-report shape containing subtask identity, outcome, findings, source evidence, observed actions/parameter origins, available measurements and failures. This is a planning requirement, not a new frozen schema.

Thomas and Tianqi cover two interpretation paths and, per PR #7 review, one shared Steel toolbox. The separate local adapters need convergence; agree the shared adapter boundary, lifecycle ownership and file ownership before simultaneous edits. Sting consumes both kinds of evidence; Abel coordinates execution with Thomas's moderator.

Dashboard, controlled-site construction, presentation/backup recording and release work have not been assigned explicitly. Allocate those separately; do not infer ownership from the removed A–D plans. Thomas/Tianqi still need to agree the concrete shared Steel session-lifecycle implementation.

## Updating this document

Keep assignments, task status, blockers and connection evidence here. Update your row after a meaningful checkpoint or before handing work to someone else. Keep entries concise and link larger artifacts. Update CURRENT only for a project-wide change or completed integration; keep architectural choices in [DECISIONS.md](DECISIONS.md). An AI records only the work/evidence it actually received or produced and does not mark other people's tasks complete by inference.
