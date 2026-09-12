# Team tasks and project tracking

Updated: 2026-09-12. The four workstream assignments below were confirmed by the user. The task breakdown translates them into concrete planning/research deliverables; it does not claim implementation or tests are complete.

| Person | Assigned workstream | Main responsibility | Next useful deliverable |
| --- | --- | --- | --- |
| Sting | Ghost API | Reusable procedures, matching, binding, qualification and skill lifecycle | Example trace → candidate → qualification → changed-input replay walkthrough |
| Thomas | VLM / subagent visual interpretation | Interpret screenshots, identify visual targets and verify visual outcomes | One Steel screenshot-to-action example with evidence and failure handling |
| Tianqi | HTML/codebase interpretation for subagents | Interpret available page structure/code, identify semantic targets and extract structured information | One HTML/DOM-to-action/result example with evidence and failure handling |
| Abel | ARGUS | Task interpretation, decomposition, worker coordination, moderator behavior and final synthesis | One complete task plan with worker inputs/outputs and moderator decisions |

All four are working on their assigned areas according to the user. Implementation progress, branches, deadlines and validation evidence have not yet been reported. See [CURRENT.md](CURRENT.md) for what is known to exist in this checkout.

## Progress board

Update this board in place. Detailed responsibilities are below; this table tracks the next deliverable and what is ready to connect. Dates remain unset until the team agrees them.

| ID | Deliverable | Responsible | Status | Dependency / next step | Evidence / branch |
| --- | --- | --- | --- | --- | --- |
| INT-1 | Shared example and interface agreement | All four; Abel coordinates ARGUS inputs/outputs | Planned | Walk through one request and agree the checklist below | Not reported |
| GHOST-1 | Ghost lifecycle and callable boundary | Sting | Researching | Draft using shared samples; real compilation needs worker traces | Not reported |
| VLM-1 | Visual worker example and evidence report | Thomas | Ready to connect | Abel/Sting run the connection check against `workers/visual/examples/hn-top-story/`; align report shape at INT-1 | `workers/visual/`; see update below |
| HTML-1 | HTML/code worker example and evidence report | Tianqi | Researching | Define available code sources; prove structure → action/result; align with INT-1 | Not reported |
| ARGUS-1 | Task plan, routing and moderator walkthrough | Abel | Researching | Define worker inputs/outputs using INT-1; use samples while workers develop | Not reported |
| INT-2 | First connected request with verified result | All four | Planned | INT-1, ARGUS-1, one callable worker and Ghost validation; connect early | Not run |
| INT-3 | Learning, qualification, reuse and repair connections | All four | Planned | INT-2, GHOST-1, fresh worker replays and controlled-site cases | Not run |
| DEMO-1 | Dashboard, controlled-site truth set and demo readiness | Unassigned | Needs owner | Allocate remaining deliverables; use evaluation checklist | Not run |

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

Status meanings: **Planned** = identified; **Researching** = approach under investigation; **Building** = implementation reported; **Ready to connect** = handoff checklist complete; **Integrated** = receiver has run the connection check; **Blocked** = named missing dependency; **Needs owner** = not assigned. The four Researching states reflect the user's report, not independently verified progress. Record a blocker as “missing input — needed from whom — work that can continue.”

## INT-1 — agree before connecting components

Use the [provisional contract](../hackathon/CONTRACTS.md) as a reference. Its existing fake types do not yet settle moderator/subtask or visual-target messages.

- [ ] One concrete request, interpreted parameters, expected records, success conditions and supported site/workflow scope.
- [ ] Callable entry points and sample success/failure messages between ARGUS, the two interpretation paths and Ghost; agree request/run/subtask IDs and contract version.
- [ ] Shared evidence/action format: current observation references, parameter origins, semantic/visual targets and failure reasons. Decide how the two interpretation paths hand over within a browser session.
- [ ] Browser-session lifecycle responsibility, time/action limits, retry/fallback limits, and one owner for each shared code/config file before concurrent edits.

Record the agreed shapes in CONTRACTS and versioned examples when approved. Keep sample messages clearly labeled. Everyone can research and prototype against those examples without waiting for every component to finish. Record architectural choices in [DECISIONS.md](DECISIONS.md), rather than inventing separate contracts in each workstream.

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

- Define which HTML/DOM and available code sources the subagent consumes and how it interprets them.
- Identify semantic controls, parameter fields, navigation context and result records.
- Propose browser actions and extract structured outputs with source evidence and explicit limitations.
- Detect insufficient page structure and provide context for Thomas's visual path.

Handoff: provide an example worker report to Abel and a trace/evidence example to Sting. Coordinate semantic target descriptions and output fields with Thomas. Clarify whether “codebase interpretation” means rendered HTML/DOM, page-delivered scripts, or a repository explicitly available to the team; do not assume access to a public website's private source repository.

## Abel — ARGUS

- Interpret one user task into an overall request, subtasks, dependencies and success conditions.
- Define routing to HTML/code interpretation, visual interpretation and qualified Ghost procedures.
- Define moderator monitoring, evidence aggregation, handling of gaps/conflicts and final synthesis.
- Agree shared worker reports, run state, budgets and failure/fallback behavior with the other workstreams.

Handoff: provide one worked task example showing each worker's input, expected output, evidence and failure report. Coordinate the moderator's implementation boundary; its separate-model versus controller-role decision remains open.

## Shared boundaries and remaining ownership

Use the same read-only search/filter/extraction example for all four deliverables. Agree one compatible worker-report shape containing subtask identity, outcome, findings, source evidence, observed actions/parameter origins, available measurements and failures. This is a planning requirement, not a new frozen schema.

Thomas and Tianqi cover two interpretation paths that may share a browser worker/session adapter. Their assignments do not require separate runtime agents or competing browser backends. Agree the shared adapter boundary and file ownership before simultaneous edits. Sting consumes both kinds of evidence; Abel coordinates their use and final verification.

Dashboard, controlled-site construction, presentation/backup recording, release work and shared Steel session-lifecycle implementation have not been assigned explicitly. Allocate those separately; do not infer ownership from the removed A–D plans.

## Updating this document

Keep assignments, task status, blockers and connection evidence here. Update your row after a meaningful checkpoint or before handing work to someone else. Keep entries concise and link larger artifacts. Update CURRENT only for a project-wide change or completed integration; keep architectural choices in [DECISIONS.md](DECISIONS.md). An AI records only the work/evidence it actually received or produced and does not mark other people's tasks complete by inference.
