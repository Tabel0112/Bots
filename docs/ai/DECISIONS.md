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
| Moderator implementation | Responsibilities agreed; separate model invocation versus controller role unresolved |
| Subtask boundaries and parallelism | Define from one concrete example before choosing a general graph/framework |
| Browser-agent framework and model | DOM worker uses direct async Python/Playwright and GPT-5.4; other reasoning/visual choices remain open |
| Public website | Unselected; needs an access and workflow feasibility check |
| Vision strategy | AI recommendation: page structure when adequate, bounded visual fallback; experiment not run |
| Languages/dependencies | Python/FastAPI/Pydantic/OpenAI/Steel/Playwright are local DOM choices awaiting INT-1; project-wide stack stays open |
| Input/output semantics | Fake defaults are USD, five results and substring query/max-price filtering; confirm for the real site |
| Contract | Historical 0.1 remains provisional; DOM worker 0.2 is implemented locally, awaiting INT-1 agreement and ARGUS/Ghost/visual receiver checks |
| Validation/qualification details | Agree evidence, changed-input coverage, empty-state check and controlled truth set |
| Schedule and remaining work | Four workstreams assigned in TEAM; deadlines and ownership of other deliverables remain open |

## Corrections that must survive a tool switch

The user initially clarified the planning phase after premature code generation. The later explicit build request authorized the DOM-worker runtime specification and its tests/documentation, not every backlog item or Ghost qualification. Steel is the shared provider choice; GPT-5.4 and the Python browser stack are HTML-1 local choices pending INT-1 agreement. TEAM records assignments and each workstream's reported evidence independently.

When a decision changes, update its entry with the date, rationale and affected contract/research links. Avoid maintaining another copy of runtime status here; use [CURRENT.md](CURRENT.md).
