# Decisions and open questions

Updated: 2026-09-13. Keep status explicit; a proposal is not an implementation commitment.

## Confirmed user direction

- **Ownership and merge rule (2026-09-13):** Abel owns and integrates all repository
  changes from this date. Changes reach `main` only through a reviewed pull request.
  The demo must never answer a request it did not understand: unsupported requests are
  rejected with an explicit message, and offline fixture runs are labeled as such in the UI.

- **End-to-end planning (2026-09-13):** the user requested the complete workflow and
  implementation plan connecting input, ARGUS decomposition, Steel-connected workers,
  Ghost and the final UI conclusion. The [plan](../hackathon/END-TO-END-PLAN.md) is a
  proposal grounded in the current source, not approval of every implementation
  choice or a claim of live integration. It identifies the existing Ghost SQLite
  storage as a proposed explicit exception to the older JSON-only decision; that
  exception and the worker-resolved ARGUS memory mode remain to be agreed at INT-1.

- **Worker–Ghost execution (2026-09-12):** the user authorized implementation and specified
  HTML/DOM first, then visual interpretation when DOM is insufficient (including canvas).
  The implementation uses one continuing Steel session, with Ghost outside its lifecycle.
  The integrated memory boundary is `0.2`; legacy `0.1` fixture clients stay supported.
  This does not establish teammate receiver or live cloud verification.

- **Phase (updated 2026-09-12):** the user explicitly requested the complete DOM browser worker implementation using a detailed runtime specification. This supersedes the planning-only restriction for HTML-1. Other workstreams remain at their reported state; the older synthetic scaffold remains provisional.
- **Product:** ARGUS coordinates deliberation/orchestration/verification; Ghost supplies reusable procedural memory.
- **Flow:** one user task becomes an overall request with subtasks; subagents execute; a moderator monitors, gathers, reasons over outputs and produces the final result.
- **Browser infrastructure:** use Steel for browser subagents.
- **Vision direction:** support visual interpretation in the browser worker when the workflow needs it; every agent does not need vision. Actual integration/model selection remains open.
- **People:** the user confirmed four current workstream assignments on 2026-09-12. [TEAM.md](TEAM.md) is the source of truth for names, tasks and handoffs; other areas remain unassigned.
- **Continuity:** store compact project context in the repository so another AI can continue without the full chat.
- **Ghost module location:** keep Ghost-specific code and documentation under the correctly spelled root `ghostapi/` folder.
- **Ghost demo:** show workflow execution as an interactive flowchart that can be dragged with a mouse or other pointer while work proceeds.
- **Backend language (2026-09-12):** Python. Thomas and Tianqi's Steel toolbox must be importable from ARGUS in-process; no HTTP layer between ARGUS and the toolbox.
- **Storage (2026-09-12):** JSON files on disk for runs, events, evidence references, skills and metrics. Screenshots stored as files and referenced by observation ID. No database for the MVP.
- **Execution shape (2026-09-12):** stages run sequentially; only subagents may run concurrently, and each subtask carries its dependencies and concurrency group at creation. A general task-graph engine is not needed.
- **Product scope (2026-09-12, supersedes the one-workflow MVP constraint for ARGUS):** the final product must navigate any site and any read-only task, not a closed registry of operations. The registry becomes the set of *qualified* operations Ghost has proven; every other request runs in explore mode under hard safety rules. The one-workflow constraint still governs what the demo must prove, not what ARGUS may accept.
- **Local VLM capacity and open-plan budget (Abel, 2026-09-12):** the hardware limitation is four concurrent local VLM executions. Keep at most four concurrent workers per MVP run because each may need VLM; the shared live toolbox must also enforce four slots across runs/callers. Separately, allow at most four total open-plan subtasks (including all subtasks of a mixed plan), depth three; one subtask may open ten results sequentially within its action/time budget. These are separate limits, not a four-result limit.
- **Ranking clarification (Abel, 2026-09-12):** ask what ambiguous rankings such as "best" mean and give concrete examples of possible answers. Preserve explicit criteria such as salary. Example: "What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience." Answer "Highest salary, remote only" supplies a ranking and a filter; suggestions are not defaults. Runtime interpretation/gating changes remain Phase E.
- **Public navigation policy (Abel, 2026-09-12):** public websites by default; exclude local/private network targets; allow public documentation, including login/payment-provider docs. Block executing login, payment and state-changing submissions, not merely discussing them. Ordinary read-only search/filter actions are permitted. Offline hostname/IP preflight is insufficient for live browsing: the shared toolbox must check DNS/connected destinations, redirects and other requests and enforce allowed actions before real open-world use.
- **Model access for ARGUS (2026-09-12):** ARGUS model calls (interpreter, open-world planner) go through OpenAI-compatible APIs, not the Anthropic SDK, because the team's workers already use OpenAI-style endpoints (GPT for the DOM worker, a local OpenAI-compatible server for UI-TARS) and the H100 box can serve models the same way. Implement one `argus/model_client.py` boundary with an OpenAI-compatible backend; tests keep injecting fakes. The Anthropic-SDK code written in phases A and 0b is to be replaced, not extended.
- **VLM hardware (2026-09-12):** four H100s, 320 GB total. At most four VLM instances run at once, hence the per-run cap of four workers. The four-subtask open-plan cap is a separate MVP budget.
- **Execution beyond read-only (Abel, 2026-09-12, direction; design not built):** the product should be able to execute tasks such as applying to jobs, not only read. Proposed shape, to be designed after INT-2: each subtask carries an action class (read_only, interactive, committing); the gate classifies instead of rejecting outright; a committing action pauses the run in a needs_confirmation state showing exactly what will be submitted, and proceeds only on the user's yes; the toolbox enforces the class at execution time; before/after screenshots and the sent request are stored as evidence; credentials never pass through ARGUS, the human logs in inside the live session viewer; Ghost never replays a committing skill without fresh confirmation. The hackathon demo path stays read-only.
- **Ghost caller boundary (2026-09-12):** Steel-powered subagents call Ghost API directly for workflow lookup, candidate saving, qualification reports, and reuse reporting. ARGUS task assignment is outside the Ghost implementation. Starting Ghost serves its workflow graph from the same FastAPI process.
- **Mission Control live sources (2026-09-13):** the bounded demo uses current public Staples product directories, the Toronto Wikivoyage guide, and Remotive software-development jobs through worker-owned Steel sessions. `ARGUS_RUNTIME=controlled` is an explicit offline fallback, never the default or a source of live claims. Site DOM changes are handled as validation failures. This does not broaden the worker into arbitrary-site extraction or establish Ghost replay/qualification or visual recovery.
- **Documentation sharing (2026-09-12):** user authorized commit and push of the shared planning/AI handoff docs. The provisional runtime remains a local experiment; publication of these docs does not imply a working shared backend.
- **ARGUS publication boundary (Abel, 2026-09-13; implemented locally, receiver confirmation pending):** the moderator never writes user-facing prose. Contract 0.5 makes synthesis an `AnswerSelection` of validated-record indices, structured field claims and typed notes with controller-checked subjects. The controller derives final records and renders every answer line. Known prose fields are discarded and logged by field path only. This replaces the evidence-reference-only rule, which could not prevent arbitrary prose from citing a real observation; Tianqi's `"is free and cures cancer"` case is the regression test. Thomas must confirm the adapter boundary before MOD-1 is connected.

## Existing MVP constraints

One read-only search/filter/extraction workflow, one public site and one controlled site with two UI versions. The controlled site needs its own learned skill. Avoid accounts, purchases, submissions, universal cross-site matching, large agent infrastructure and unbounded retries.

Required demonstration: unfamiliar task exploration; actual trace to candidate; fresh replay qualification; changed-input reuse with fresh results; independent rejection of incorrect output; one supported change repaired into a qualified new version; honest unsupported-change handling; visible execution path and measurements.

The retained provisional contract budgets at most one repair followed by at most one appropriate exploration fallback. Exact runtime budgets need to be agreed for the live integration. General canvas support is not an added MVP requirement.

## Proposals and open decisions

### HTML-1 local implementation choices — awaiting INT-1 agreement

Python/Pydantic, Playwright/Steel, GPT-5.4 Responses with medium reasoning and optional FastAPI transport are implemented for the DOM worker only. `SubtaskRequest` / `SubtaskReport` 0.2 is its local boundary, not a settled shared contract; 0.1 remains provisional. See [module usage](../../Agents/browser_worker/README.md). Stack adoption, message compatibility and receiver verification remain pending INT-1.

The PR #7 review reiterates no required HTTP layer between ARGUS and its toolbox: ARGUS calls `Worker.run` in-process, while FastAPI is optional for standalone clients. It also identifies Thomas and Tianqi as the shared Steel toolbox owners. Separate adapters currently exist in `Agents/visual/` and `Agents/browser_worker/`; convergence, shared session ownership and `needs_visual` compatibility require their coordination, not unilateral adoption of either adapter. No shared-toolbox integration is claimed.

| Question/proposal | Current position |
| --- | --- |
| Moderator implementation | User direction 2026-09-12: the moderator is a separate deliverable with its own owner, not Abel. The controller remains the single owner of run state. The local 0.5 callable boundary now returns structured `AnswerSelection`, never prose; Thomas's receiver confirmation remains pending before connection |
| Subtask boundaries and parallelism | Settled for registered operations (one subtask per intent, deterministic planner, built 2026-09-12). Open-world requests need the model-planned decomposition with dependent chains; design in [ARGUS.md](../hackathon/ARGUS.md) open-world section |
| Browser-agent framework and model | DOM worker uses direct async Python/Playwright and GPT-5.4; other reasoning/visual choices remain open |
| Public website | Unselected; needs an access and workflow feasibility check |
| Vision strategy | AI recommendation: page structure when adequate, bounded visual fallback; experiment not run |
| Languages/dependencies | Backend is Python (confirmed 2026-09-12). DOM worker locally uses FastAPI/Pydantic/OpenAI/Steel/Playwright, awaiting INT-1; agent loop, model and frontend remain open; React/TypeScript was an earlier frontend proposal |
| Input/output semantics | Fake defaults are USD, five results and substring query/max-price filtering; confirm for the real site |
| Contract | Historical 0.1 remains provisional; DOM worker 0.2 is implemented locally, awaiting INT-1 agreement and ARGUS/Ghost/visual receiver checks |
| Validation/qualification details | Agree evidence, changed-input coverage, empty-state check and controlled truth set |
| Schedule and remaining work | Four workstreams assigned in TEAM; deadlines and ownership of other deliverables remain open |
| Framework pull requests before integration | User decision 2026-09-13: every change to `main` goes through a pull request with one review from someone other than the author, and Abel owns/integrates all changes from that date. CI must be green before merge. Sting's direct push `7e28641` predates this decision and broke CI; fixed on `fix/main-stabilize`. Branch protection enforcing this is still an AI recommendation until enabled in GitHub |

## Corrections that must survive a tool switch



When a decision changes, update its entry with the date, rationale and affected contract/research links. Avoid maintaining another copy of runtime status here; use [CURRENT.md](CURRENT.md).
