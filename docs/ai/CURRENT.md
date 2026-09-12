# Current checkpoint

Updated: 2026-09-12. Phase: **planning and research**. Four workstream assignments are confirmed in [TEAM.md](TEAM.md).

## What the next AI needs to know

We are building ARGUS + Ghost API for the Battle of the Schools Web Agents hackathon. ARGUS plans work and coordinates reasoning/verification; Ghost turns successful browser interactions into reusable, parameterized, qualified capabilities. Browser subagents will use Steel. See [architecture](ARCHITECTURE.md) for the intended moderator flow and [decisions](DECISIONS.md) for confirmed versus open choices.

The shared repository checkpoint contains documentation, AI entry points and planning placeholders. A previous request produced a local synthetic backend, but the user clarified that implementation was premature. That experiment is not included in this documentation commit. Do not assume that its files exist in a teammate's checkout or treat its fixed controller/schema/defaults as the agreed final architecture.

## Local experiment — not part of this shared checkpoint

These paths remain only in the original working checkout unless separately published:

| Paths | Experimental behavior/status |
| --- | --- |
| `backend/contracts/` | Python 0.1 types/protocols and nine synthetic fixtures; no general moderator/subtask contract |
| `backend/browser/fake.py`, `backend/ghost/fake.py` | Synthetic actions, sample candidate compilation/binding and validation; no Steel/model calls |
| `backend/argus/` | Sequential fake-run controller, ordered events, terminal state and cooperative budgets |
| `backend/api/`, `backend/storage/` | Local API, polling/SSE snapshots, process-local storage |
| `tests/integration/`, `scripts/`, `.env.example` | Experimental tests, fixture/link utilities and unused environment-variable names |

The local-only run command is `python3 -m backend.api.server`; its tests use `python3 -m unittest discover -v`. Both require the unpublished files. The fake API binds localhost and stores data only in memory. Do not use these as clean-clone setup instructions.

No live exploration/compilation, fresh qualification, real reuse, repair/fallback, vision execution, moderator reasoning, natural-language decomposition, dashboard or durable storage has been demonstrated here. Constructed reuse/repair fixtures are UI examples, not execution evidence. Team members may have additional work; its revisions and results have not been reported in this context.

## Repository and validation checkpoint

Documentation prepared from `main` at `25f061c` (`created team setup`), remote `Tabel0112/Bots`. The user authorized committing and pushing the documentation. The commit replaces the duplicate tracked `hackathon-team/` pack with the current docs; the originals remain recoverable from Git at `25f061c`. The synthetic implementation is intentionally left untracked. Recheck local Git status and remote history before edits rather than assuming this dated snapshot is current.

Prior local experiment validation on 2026-09-12: **24 tests passed** on Python 3.13.5, including localhost HTTP behavior. That suite is not part of this documentation release and was not rerun for it. Publication validation: **70 local Markdown links resolve** in an exported staged snapshot; the Claude import resolves, the unpublished backend is absent, and the staged whitespace check passes. No live Steel test or fresh-Claude startup test has run.

## Latest work and next useful step

Prepared the planning docs, team progress board, root AI entry points and coding-task completion rule for sharing. Coding agents must maintain module READMEs, record changes and actual checks in TEAM, and update project context when appropriate. No runtime implementation changes are part of this publication.

Next planning checkpoint is INT-1 in TEAM: walk through one concrete request and agree component inputs, outputs, evidence, failures and shared session responsibility. Each person can continue research with common samples; connect a working slice when its components are ready. Public-site choice, agent framework/model, remaining ownership and precise experiments are open.

## Ghost API local module

`ghostapi/` now contains a simulated workflow registry and interactive flowchart viewer. It demonstrates exact workflow lookup, no-match discovery, SQLite candidate/version/run storage, parameter binding, replay, validation, qualification, and concurrent UI observation while queued work executes. The chart supports pointer dragging, zoom, history navigation, live following, and evidence inspection. The browser/catalog and discovery procedure remain fixtures; this is not evidence of Steel integration or automatic trace compilation.

Focused validation at this checkpoint: 12 Python tests passed and 5 JavaScript tests passed. Coverage reports measured 72% Python coverage and 97.18% line / 91.30% branch coverage for the flowchart module. Root CI and Codecov configuration cover the module. The feature changes are maintained on `feat/ghost-api-interactive-demo`.
