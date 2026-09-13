# Decisions and open questions

Updated: 2026-09-12. Keep status explicit; a proposal is not an implementation commitment.

## Confirmed user direction

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
- **Documentation sharing (2026-09-12):** user authorized commit and push of the shared planning/AI handoff docs. The provisional runtime remains a local experiment; publication of these docs does not imply a working shared backend.

## Existing MVP constraints

One read-only search/filter/extraction workflow, one public site and one controlled site with two UI versions. The controlled site needs its own learned skill. Avoid accounts, purchases, submissions, universal cross-site matching, large agent infrastructure and unbounded retries.

Required demonstration: unfamiliar task exploration; actual trace to candidate; fresh replay qualification; changed-input reuse with fresh results; independent rejection of incorrect output; one supported change repaired into a qualified new version; honest unsupported-change handling; visible execution path and measurements.

The retained provisional contract budgets at most one repair followed by at most one appropriate exploration fallback. Exact runtime budgets need to be agreed for the live integration. General canvas support is not an added MVP requirement.

## Proposals and open decisions

### HTML-1 local implementation choices — awaiting INT-1 agreement

Python/Pydantic, Playwright/Steel, GPT-5.4 Responses with medium reasoning and optional FastAPI transport are implemented for the DOM worker only. `SubtaskRequest` / `SubtaskReport` 0.2 is its local boundary, not a settled shared contract; 0.1 remains provisional. See [module usage](../../browser_worker/README.md). Stack adoption, message compatibility and receiver verification remain pending INT-1.

The PR #7 review reiterates no required HTTP layer between ARGUS and its toolbox: ARGUS calls `Worker.run` in-process, while FastAPI is optional for standalone clients. It also identifies Thomas and Tianqi as the shared Steel toolbox owners. Separate adapters currently exist in `workers/visual/` and `browser_worker/`; convergence, shared session ownership and `needs_visual` compatibility require their coordination, not unilateral adoption of either adapter. No shared-toolbox integration is claimed.

| Question/proposal | Current position |
| --- | --- |
| Moderator implementation | User direction 2026-09-12: the moderator is a separate deliverable with its own owner, not Abel. The controller remains the single owner of run state and calls the moderator through three callables; proposed boundary in [ARGUS.md](../hackathon/ARGUS.md), not yet accepted by the moderator owner |
| Subtask boundaries and parallelism | Settled for registered operations (one subtask per intent, deterministic planner, built 2026-09-12). Open-world requests need the model-planned decomposition with dependent chains; design in [ARGUS.md](../hackathon/ARGUS.md) open-world section |
| Browser-agent framework and model | DOM worker uses direct async Python/Playwright and GPT-5.4; other reasoning/visual choices remain open |
| Public website | Unselected; needs an access and workflow feasibility check |
| Vision strategy | AI recommendation: page structure when adequate, bounded visual fallback; experiment not run |
| Languages/dependencies | Backend is Python (confirmed 2026-09-12). DOM worker locally uses FastAPI/Pydantic/OpenAI/Steel/Playwright, awaiting INT-1; agent loop, model and frontend remain open; React/TypeScript was an earlier frontend proposal |
| Input/output semantics | Fake defaults are USD, five results and substring query/max-price filtering; confirm for the real site |
| Contract | Historical 0.1 remains provisional; DOM worker 0.2 is implemented locally, awaiting INT-1 agreement and ARGUS/Ghost/visual receiver checks |
| Validation/qualification details | Agree evidence, changed-input coverage, empty-state check and controlled truth set |
| Schedule and remaining work | Four workstreams assigned in TEAM; deadlines and ownership of other deliverables remain open |

## Corrections that must survive a tool switch

The user initially clarified the planning phase after premature code generation. The later explicit build request authorized the DOM-worker runtime specification and its tests/documentation, not every backlog item or Ghost qualification. Steel is the shared provider choice; GPT-5.4 and the Python browser stack are HTML-1 local choices pending INT-1 agreement. TEAM records assignments and each workstream's reported evidence independently.

When a decision changes, update its entry with the date, rationale and affected contract/research links. Avoid maintaining another copy of runtime status here; use [CURRENT.md](CURRENT.md).
