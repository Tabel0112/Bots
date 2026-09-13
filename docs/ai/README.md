# AI context — start here

Purpose: allow a new AI session to continue ARGUS + Ghost API without replaying the conversation. This folder contains the current project context; it is not a new implementation plan.

## Read efficiently

1. Always read [CURRENT.md](CURRENT.md): phase, repository state, last checkpoint and next planning question.
2. Select the relevant topic below. Read the remaining topics only when the task crosses those boundaries.
3. Inspect the actual affected files before proposing code changes. Historical test results are evidence for their recorded checkpoint, not a claim about the current checkout.

| Task | Read next |
| --- | --- |
| Understand the product or moderator/subagent flow | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Plan the complete input-to-conclusion UI integration | [End-to-end integration plan](../hackathon/END-TO-END-PLAN.md); proposed build order based on the 2026-09-13 checkout |
| Find assignments, progress, blockers or integration handoffs | [TEAM.md](TEAM.md) |
| Decide scope or resolve conflicting assumptions | [DECISIONS.md](DECISIONS.md) |
| Research Steel, canvas or vision | [Research notes](../hackathon/RESEARCH.md) |
| Work on shared messages or component integration | [Provisional contract 0.1](../hackathon/CONTRACTS.md); inspect actual implementation files only if present in your checkout |
| Understand the historical synthetic scaffold | [CURRENT.md](CURRENT.md); the old experiment is distinct from the new browser worker |
| Run or change the DOM browser worker | [Worker README](../../Agents/browser_worker/README.md), [TEAM.md](TEAM.md) HTML-1; inspect `Agents/browser_worker/` and its tests |
| Connect workers to Ghost or run DOM-first visual fallback | [Connection guide](../../ghostapi/INTEGRATION.md); module READMEs and TEAM local connection update |
| Work on UI/API examples | JSON examples in [CONTRACTS.md](../hackathon/CONTRACTS.md); the experimental generated fixtures are not in this documentation commit |
| Plan evidence or the demonstration | [Evaluation checklist](../hackathon/EVALUATION.md) |
| Work on ARGUS (controller, interpreter, gate, planner) | [ARGUS design and moderator boundary](../hackathon/ARGUS.md), then [ARGUS implementation plan and prompts](../hackathon/ARGUS-IMPLEMENTATION.md); code and tests under `argus/` |

## Resolve conflicts

The user's latest instruction governs the requested work. CURRENT records the latest phase and checkpoint; DECISIONS records scope and decision status; ARCHITECTURE describes intended behavior; RESEARCH holds source-backed findings. Code establishes what exists. An older plan or scaffold choice does not settle an open product decision.

The duplicate `hackathon-team/` pack and obsolete person-by-person plans have been removed. The original tracked pack is recoverable from Git at `25f061c` if historical research is specifically needed. Current decisions, scope and setup evidence are retained here. No external conversation export is needed for ordinary continuation.

## Keep the handoff useful

- Update CURRENT after a meaningful checkpoint: outcome, actual validation, unresolved issue and next step. Replace stale status instead of appending a transcript.
- Update DECISIONS only when a choice is made, changed or explicitly reopened; distinguish user direction from an AI recommendation.
- Update TEAM when assignments, task status, blockers or handoffs change; record receiver verification before marking work Integrated. Do not duplicate its task board elsewhere.
- For coding changes, maintain the affected module's README and link it from the task handoff; the completion checklist is in [AGENTS.md](../../AGENTS.md).
- Update ARCHITECTURE when the agreed component flow changes; put experiment findings and citations in RESEARCH.
- Link to detailed contracts, fixtures and code rather than copying them here. Do not regenerate fixtures just to document progress.

In Claude Code, the root [CLAUDE.md](../../CLAUDE.md) imports the shared [AGENTS.md](../../AGENTS.md) entry point. Other tools can be directed to this page. This uses Claude Code's documented project-memory mechanism; it has not been smoke-tested in a fresh Claude session here. [Claude Code documentation](https://code.claude.com/docs/en/memory).
