# Decisions and open questions

Updated: 2026-09-12. Keep status explicit; a proposal is not an implementation commitment.

## Confirmed user direction

- **Phase (updated 2026-09-12, later the same day):** build component frameworks first, connect later. Each workstream develops its own skeleton, module README and evidence; integration waits for INT-1/INT-2 in [TEAM.md](TEAM.md). The earlier local ARGUS scaffold remains provisional and is not the agreed architecture.
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

| Question/proposal | Current position |
| --- | --- |
| Moderator implementation | Responsibilities agreed; separate model invocation versus controller role unresolved |
| Subtask boundaries and parallelism | Define from one concrete example before choosing a general graph/framework |
| Browser-agent framework and model | Unselected; research Steel compatibility, vision and action-trace access |
| Public website | Unselected; needs an access and workflow feasibility check |
| Vision strategy | AI recommendation: page structure when adequate, bounded visual fallback; experiment not run |
| Languages/dependencies | Python scaffold exists; React/TypeScript and a browser-agent library were earlier proposals; final stack not frozen |
| Input/output semantics | Fake defaults are USD, five results and substring query/max-price filtering; confirm for the real site |
| Contract | Version 0.1 describes the provisional narrow scaffold; moderator/subtask/visual target extensions need design work |
| Validation/qualification details | Agree evidence, changed-input coverage, empty-state check and controlled truth set |
| Schedule and remaining work | Four workstreams assigned in TEAM; deadlines and ownership of other deliverables remain open |
| Framework pull requests before integration | User direction: component skeletons merge to `main` through pull requests with status **Building**; **Ready to connect** requires the TEAM handoff checklist. AI recommendation, not decided: CI running offline checks and branch protection requiring one review from someone other than the author |

## Corrections that must survive a tool switch

The user first clarified (2026-09-12) that the generated ARGUS scaffold was premature. Later the same day the user directed that workstreams build their frameworks before connecting them. A later AI should treat workstream code as expected, review it for framework correctness and honest labeling, and not treat “not integrated” as a defect. It still must not connect components, mark a connection Integrated or claim end-to-end behavior without the INT checkpoints. Steel was subsequently selected, so older “browser provider unselected” wording is superseded for the provider choice. Agent framework and model remain open. The moderator proposal is more explicit than the old single-controller scaffold. The user later supplied the four workstream assignments in TEAM, superseding the earlier decision to postpone personnel allocation. These assignments do not prove implementation progress or authorize this AI to start coding automatically.

When a decision changes, update its entry with the date, rationale and affected contract/research links. Avoid maintaining another copy of runtime status here; use [CURRENT.md](CURRENT.md).
