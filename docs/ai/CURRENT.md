# Current checkpoint

Updated: 2026-09-12 (evening). Phase: **building component frameworks; integration deferred**. Four workstream assignments are confirmed in [TEAM.md](TEAM.md). Each workstream builds its own skeleton, README and evidence now; connecting components waits for the INT checkpoints in TEAM.

## What the next AI needs to know

We are building ARGUS + Ghost API for the Battle of the Schools Web Agents hackathon. ARGUS plans work and coordinates reasoning/verification; Ghost turns successful browser interactions into reusable, parameterized, qualified capabilities. Browser subagents will use Steel. See [architecture](ARCHITECTURE.md) for the intended moderator flow and [decisions](DECISIONS.md) for confirmed versus open choices.

The shared repository checkpoint contains documentation, AI entry points, planning placeholders and, since 2026-09-12 (evening), two component skeletons: the VLM-1 visual worker under `workers/visual/` ([module README](../../workers/visual/README.md), PR #1, `eb3095b`) and the Ghost API fixture demo under `ghostapi/` ([module README](../../ghostapi/README.md), PR #3, `b4c9e76`). A previous request produced a local synthetic backend, but the user clarified that implementation was premature. That experiment is not included in this documentation commit. Do not assume that its files exist in a teammate's checkout or treat its fixed controller/schema/defaults as the agreed final architecture.

Later on 2026-09-12 the user directed that workstreams build their component frameworks independently before anything is connected. Workstream code arriving in pull requests is therefore expected; review it for framework correctness and honest labeling, not for integration readiness. Do not connect components, mark a connection Integrated or claim end-to-end behavior until the INT checkpoints in [TEAM.md](TEAM.md) have actually run.

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

Documentation prepared from `main` at `25f061c` (`created team setup`), remote `Tabel0112/Bots`. Remote state on 2026-09-12 (evening): `origin/main` at `b4c9e76`. Pull request #1 (`vlm1-visual-worker-v2`, Thomas) merged at 18:37 UTC as `eb3095b`, merged by Sting; its history: pushed directly to `main` (`7811270`, `fca9b12`), reverted (`81f5172`, `368b67d`), then re-landed through the PR. Pull request #2 (`feat/ghost-api-demo`, Sting) was opened at 18:36 UTC and closed unmerged. Pull request #3 (`feat/ghost-api-interactive-demo`, Sting, 28 files) was opened at 18:49 UTC and merged by its author at 18:50 UTC as `b4c9e76` without a review; besides `ghostapi/` it edited AGENTS.md, README.md, CURRENT, DECISIONS, TEAM and replaced the skill-schema section of `docs/hackathon/CONTRACTS.md` with a link to `ghostapi/CONTRACTS.md` without the contract version step. Neither PR #3 nor `ghostapi/` has been reviewed in this context. Pull request #4 (`docs/pr1-review-checkpoint`) carries this documentation update. The user authorized committing and pushing the documentation. The commit replaces the duplicate tracked `hackathon-team/` pack with the current docs; the originals remain recoverable from Git at `25f061c`. The synthetic implementation is intentionally left untracked. Recheck local Git status and remote history before edits rather than assuming this dated snapshot is current.

Local experiment validation, rerun 2026-09-12 (evening) on Python 3.13.5: **24 tests, 2 errors** (`test_foreign_run_evidence_fails_without_stranding_run`, `test_malformed_adapter_payload_fails_without_stranding_run`; both fail inside the `Error` model's dict round-trip with `ContractError: Invalid union value: True`). The earlier 24-pass record is superseded; the cause has not been investigated. That suite is not part of the shared checkpoint. Publication validation: **70 local Markdown links resolve** in an exported staged snapshot; the Claude import resolves, the unpublished backend is absent, and the staged whitespace check passes. No live Steel test or fresh-Claude startup test has run.

## Latest work and next useful step

Prepared the planning docs, team progress board, root AI entry points and coding-task completion rule for sharing. Coding agents must maintain module READMEs, record changes and actual checks in TEAM, and update project context when appropriate. No runtime implementation changes are part of this publication.

**PR #1 review checkpoint (2026-09-12, evening).** Verified offline: `workers/visual/test_parser.py` passes with the Steel SDK stubbed, the package compiles, and the Hacker News evidence screenshot shows the reported title and points. Not run: the live Steel and UI-TARS scripts (need `STEEL_API_KEY` and a local model server). The PR merged during the review (`eb3095b`), so these are follow-ups requested from Thomas on a branch: bring the TEAM status **Building** to `main` (Thomas set it in `8082c65` on the PR branch three minutes after the merge, so it is stranded there; the example run has no interaction step, so `semantic_target` and `findings` are null); fix the stale README limitation text; add a requirements file and make the parser test importable without the Steel SDK; verify whether Steel click coordinates are in screenshot space while `elementFromPoint` uses viewport pixels (the screenshot includes about 88 px of browser chrome). Deferred to connection time: an interaction-step example, Ghost compilation input, the untested Claude backend. The review comment was posted on PR #1.

Next: Thomas applies the PR #1 follow-ups on a branch; the other workstreams publish their frameworks the same way (module README, TEAM row, pull request to `main`, no direct pushes, one reviewer before merge). INT-1 in TEAM remains the planning checkpoint before anything is connected: walk through one concrete request and agree component inputs, outputs, evidence, failures and shared session responsibility. Public-site choice, agent framework/model, remaining ownership and precise experiments are open.

## Ghost API local module

`ghostapi/` now contains a simulated workflow registry and interactive flowchart viewer. It demonstrates exact workflow lookup, no-match discovery, SQLite candidate/version/run storage, parameter binding, replay, validation, qualification, and concurrent UI observation while queued work executes. The chart supports pointer dragging, zoom, history navigation, live following, and evidence inspection. The browser/catalog and discovery procedure remain fixtures; this is not evidence of Steel integration or automatic trace compilation.

Focused validation at this checkpoint: 12 Python tests passed and 5 JavaScript tests passed. Coverage reports measured 72% Python coverage and 97.18% line / 91.30% branch coverage for the flowchart module. Root CI and Codecov configuration cover the module. Merged to `main` in `b4c9e76` (PR #3, 2026-09-12 18:50 UTC). The test and coverage figures above are Sting's report at that checkpoint and were not rerun here.
