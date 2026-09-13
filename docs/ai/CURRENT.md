# Current checkpoint

Updated: 2026-09-12. **HTML-1 DOM worker implemented locally; interactive dashboard prototype added; shared integration pending.** Ghost and visual modules have separate implementation/evidence below. Assignments and handoffs remain in [TEAM.md](TEAM.md).

## Latest user direction

The user supplied a detailed browser-worker runtime specification and explicitly requested its full implementation. This supersedes the earlier planning-only restriction for Tianqi's HTML-1 workstream. The browser worker handles one structured subtask; ARGUS decomposition/moderation, Ghost qualification, and visual execution remain separate responsibilities.

## What exists now

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

Prior evidence, not rerun for this review: the local demo drove real Chrome through search/extraction/verification/cleanup; a credentialed Steel-only sanity check created a session, connected Playwright, observed `https://example.com/` as `Example Domain`, and released the session in 5.671 seconds. It exposed and fixed a session ID format bug using a canonical UUID. No live GPT call or complete configured Steel task has been verified; the latter needs a reachable configured site instead of the local fixture.

Earlier documentation-only checkpoints and the local synthetic backend experiment are historical. The experiment's 24-test result does not describe this worker and must not be used as its validation. No older `backend/` code was present in the starting checkout.

## Next handoff

1. Agree the local 0.2 boundary at INT-1, then have Abel verify `Worker.run` inputs/reports and Sting review real trace evidence. The worker does not promote skills.
2. Coordinate with Thomas on one shared Steel toolbox/session owner and verify a continuing `needs_visual` handoff in an ARGUS-owned session. Worker-owned sessions are released.
3. Configure an OpenAI key and a reachable read-only site for the remaining live GPT + configured Steel task test. The independent Steel lifecycle check is already verified.
4. Record receiver revisions/results in TEAM; HTML-1 remains Building and INT-1/INT-2 remain unintegrated.

## Visual worker module

`workers/visual/` is merged on main. VLM-1 is Ready to connect, with reported parser, live Steel smoke, grounding calibration and a successful one-step Hacker News run. Preserve its [TEAM evidence](TEAM.md) and [module usage](../../workers/visual/README.md); its ARGUS/Ghost receiver checks remain pending. These are the visual workstream's recorded results, not checks rerun for HTML-1.

## Dashboard UX prototype

`frontend/dist/` contains a dependency-free Mission Control prototype with Paper and Midnight themes. The UX review update synchronizes immutable event snapshots across a decision-oriented execution flow, vertical recorded-only activity, and before/after sample evidence. History stays pinned until Return to live/latest. The flow uses Plan/Execute/Verify stages, an explicit proof decision, and a labeled visual-recovery loop returning to fresh DOM verification. Wide layouts show the fitted primary route; narrower chart space switches to a vertical layout with a fit/readable control. Successful reuse, price-filter failure with visual recovery, browser unavailability with retry, pause/resume, cancellation, and five-result completion are simulated. Runs, Ghost Library, and Evidence have consistent hash navigation; smaller screens use workspace panel tabs. No live service connection is claimed or implemented. This verified revision is deployed as owner-private Sites version 4 at `https://argus-mission-control.sunyihan666.chatgpt.site`. Usage and limitations are in [`frontend/README.md`](../../frontend/README.md).

## Ghost API local module

`ghostapi/` now contains a simulated workflow registry and interactive flowchart viewer. It demonstrates exact workflow lookup, no-match discovery, SQLite candidate/version/run storage, parameter binding, replay, validation, qualification, and concurrent UI observation while queued work executes. The chart supports pointer dragging, zoom, history navigation, live following, and evidence inspection. The browser/catalog and discovery procedure remain fixtures; this is not evidence of Steel integration or automatic trace compilation.

Focused validation at this checkpoint: 12 Python tests passed and 5 JavaScript tests passed. Coverage reports measured 72% Python coverage and 97.18% line / 91.30% branch coverage for the flowchart module. Root CI and Codecov configuration cover the module. The feature changes are maintained on `feat/ghost-api-interactive-demo`.
