# Current checkpoint

Updated: 2026-09-12 (evening). Phase: **building component frameworks; integration deferred**. Four workstream assignments are confirmed in [TEAM.md](TEAM.md). Each workstream builds its own skeleton, README and evidence now; connecting components waits for the INT checkpoints in TEAM.

## Latest user direction

The user supplied a detailed browser-worker runtime specification and explicitly requested its full implementation. This supersedes the earlier planning-only restriction for Tianqi's HTML-1 workstream. The browser worker handles one structured subtask; ARGUS decomposition/moderation, Ghost qualification, and visual execution remain separate responsibilities.



[browser_worker/README.md](../../browser_worker/README.md) is the setup and usage entry point. The new `browser_worker/` module includes:

- Strict Pydantic request, action, evidence, and report contract 0.2; trusted site config and deterministic input/action/domain/value-origin validation.
- Async Steel session create/attach/release and Playwright browser operations; local Chromium mode for reproducible tests. Observation-bound element refs, fresh DOM checks, guarded redirect hops, and typed execution errors.
- GPT-5.4 Responses function tools with medium reasoning, one operation per call, bounded retries/actions/model calls/runtime, cancellation, and operational logging.
- Deterministic extraction from observed field mappings and independent schema, provenance, filter, count, empty-state, freshness, and link checks. Unproven completion returns `inconclusive`.
- A transport-neutral `Worker.run`, FastAPI submit/execute/poll/cancel routes, in-process session exclusion/idempotency, and typed visual handoff/trace output.
- A small local catalog and explicitly scripted reasoning substitute for no-key smoke tests. Reports identify browser/reasoning backends. This is not the product-level two-version demonstration.

The full product flow remains in [ARCHITECTURE.md](ARCHITECTURE.md). Contract 0.2 is an implemented local HTML-1 boundary awaiting INT-1 agreement and receiver verification, not a project-wide contract. The older provisional contract 0.1 is preserved. ARGUS should call `Worker.run` in-process; FastAPI is optional, not a required HTTP layer between ARGUS and its toolbox. DOM/visual adapter convergence, shared Steel ownership and `needs_visual` compatibility require coordination with Thomas at INT-1.

## Validation and checkout

Work started from `main` at `baf341a`; PR #7 on `codex/browser-worker-steel` is rebased onto `origin/main` at `b4c9e76`, preserving the merged Ghost and visual modules. Recheck Git status and the pull request before editing.

PR #7 review checks: **74 DOM tests passed** on Python 3.12.14/Windows (37.72 seconds); Ruff lint/format and whitespace checks passed. The existing Ghost Python suite also passed all 12 tests. Ghost/visual source and Ghost CI remain unchanged against main. DOM test/lint configuration is scoped to its module; a separate Linux CI job was added (remote result pending). Review fixes cover ambient network traffic, model failure-code authority and portable browser setup.



## Next handoff

1. Agree the local 0.2 boundary at INT-1, then have Abel verify `Worker.run` inputs/reports and Sting review real trace evidence. The worker does not promote skills.
2. Coordinate with Thomas on one shared Steel toolbox/session owner and verify a continuing `needs_visual` handoff in an ARGUS-owned session. Worker-owned sessions are released.
3. Configure an OpenAI key and a reachable read-only site for the remaining live GPT + configured Steel task test. The independent Steel lifecycle check is already verified.
4. Record receiver revisions/results in TEAM; HTML-1 remains Building and INT-1/INT-2 remain unintegrated.

**PR #1 review checkpoint (2026-09-12, evening).** Verified offline: `workers/visual/test_parser.py` passes with the Steel SDK stubbed, the package compiles, and the Hacker News evidence screenshot shows the reported title and points. Not run: the live Steel and UI-TARS scripts (need `STEEL_API_KEY` and a local model server). The PR merged during the review (`eb3095b`), so these are follow-ups requested from Thomas on a branch: bring the TEAM status **Building** to `main` (Thomas set it in `8082c65` on the PR branch three minutes after the merge, so it is stranded there; the example run has no interaction step, so `semantic_target` and `findings` are null); fix the stale README limitation text; add a requirements file and make the parser test importable without the Steel SDK; verify whether Steel click coordinates are in screenshot space while `elementFromPoint` uses viewport pixels (the screenshot includes about 88 px of browser chrome). Deferred to connection time: an interaction-step example, Ghost compilation input, the untested Claude backend. The review comment was posted on PR #1.

Next: Thomas applies the PR #1 follow-ups on a branch; the other workstreams publish their frameworks the same way (module README, TEAM row, pull request to `main`, no direct pushes, one reviewer before merge). INT-1 in TEAM remains the planning checkpoint before anything is connected: walk through one concrete request and agree component inputs, outputs, evidence, failures and shared session responsibility. Public-site choice, agent framework/model, remaining ownership and precise experiments are open.

## Ghost API local module

`ghostapi/` now contains a simulated workflow registry and interactive flowchart viewer. It demonstrates exact workflow lookup, no-match discovery, SQLite candidate/version/run storage, parameter binding, replay, validation, qualification, and concurrent UI observation while queued work executes. The chart supports pointer dragging, zoom, history navigation, live following, and evidence inspection. The browser/catalog and discovery procedure remain fixtures; this is not evidence of Steel integration or automatic trace compilation.

Focused validation at this checkpoint: 12 Python tests passed and 5 JavaScript tests passed. Coverage reports measured 72% Python coverage and 97.18% line / 91.30% branch coverage for the flowchart module. Root CI and Codecov configuration cover the module. Merged to `main` in `b4c9e76` (PR #3, 2026-09-12 18:50 UTC). The test and coverage figures above are Sting's report at that checkpoint and were not rerun here.
