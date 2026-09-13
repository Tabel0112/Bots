# Current checkpoint

Updated: 2026-09-13. **DOM-first worker–Ghost connection implemented locally; live Steel/model verification pending. ARGUS PR #10 includes the structured-publication review correction.** Ghost and visual modules have separate implementation/evidence below. Assignments and receiver handoffs remain in [TEAM.md](TEAM.md).

Mission Control now has a runnable controlled-data integration slice: Shopping,
Travel Plan and Job Search execute through the real ARGUS controller, dependency
scheduler, validation and publication boundary; a FastAPI/SSE layer drives the UI
with controller, Ghost, subagent, worker-action and moderator events. This is not
a live Steel/model/Ghost receiver check. Setup is in the module READMEs.
All three scenarios reached passed validation in direct checks; the final focused
3-test demo/API suite and full 456-test ARGUS suite passed, as did JS syntax, compileall,
whitespace, API health and HTTP page serving. The local server is running on port
4173. Browser visual QA remains pending because no computer-use browser was available.
The follow-up changed the launcher into natural-language routing: example buttons only
populate text, while backend classification and extraction now choose the domain and
derive supported parameters/criteria. A live HTTP request for the top two remote
software-engineering salaries produced a two-step plan and exactly two validated,
salary-ranked records. This remains deterministic controlled-domain interpretation,
not a general model interpreter or live website run.

Latest credential smoke check: the user configured Steel/OpenAI keys in ignored `.env`.
The real Steel adapter created a session, opened `https://example.com/`, observed
`Example Domain` and five visible elements, and released the session in 4.353 seconds.
The attempted live-model local catalog demo stopped before any OpenAI call: `.env`
selects `gpt-5.6-luna` / `low`, while `Settings` only accepts GPT-5.4 / medium or high.
User settings were preserved. No live visual endpoint is configured; full Steel + model
and Ghost execution remain unverified.

## Latest user direction

On 2026-09-13 the user requested a plan to combine ARGUS, Ghost, Steel-connected
subagents and the UI from natural-language input through decomposition to a final
conclusion. The [end-to-end integration plan](../hackathon/END-TO-END-PLAN.md) records
the inspected contracts, proposed files, workflow, UI API/events and acceptance order.
This checkpoint is planning/documentation only; no runtime code, credentials or
deployment changed and no live/runtime tests were run. Source inspection at HEAD
`35f6cb1` confirms fake ARGUS wiring, the simulated dashboard, the configured-only
`search_extract` worker boundary and the implemented worker/Ghost connection. The new
plan accounts for that connection instead of the older Phase 3 Ghost demo adapter.
Next: agree INT-1 mappings and configuration, then connect one real request through
the existing UI before adding the multi-subtask and learning demonstrations. General
public-site execution and supported repair are explicit further implementation phases.

The user authorized executing the worker–Ghost integration plan, then specified HTML/DOM
first and visual fallback when DOM cannot handle the page (for example, canvas). The
connection is implemented in `Agents/browser_worker/ghost.py`, `Agents/browser_worker/ghost_adapter.py`,
`ghostapi/client.py`, and the visual worker's `ghost_adapter.py`. See
[setup, contracts and demo commands](../../ghostapi/INTEGRATION.md). ARGUS/moderator
branches are not merged or wired by this change.

## Latest integration checkpoint

Mission Control now defaults to a live Steel runtime (`argus/live_runtime.py`) for
the bounded Shopping, Toronto Travel, and remote Job Search flows. Natural-language
requests visibly plan against `staples.com`, `en.wikivoyage.org`, or `remotive.com`;
worker-owned Steel sessions navigate those public pages, extract current DOM records,
and emit actions/evidence into the existing ARGUS event stream. Ghost match decisions
use the real local SQLite registry lookup; validation and moderator selection remain
controller-side adapters. `ARGUS_RUNTIME=controlled` is the explicit offline fallback.

Verified on 2026-09-13: live Shopping succeeded with two concurrent Steel sessions,
five records per category and two price-limited selections; live Job Search succeeded
on Remotive with an advertised annual salary; Wikivoyage section extraction returned
six real listings per subtask, passed validation, and selected four distinct stops
after assigning unique source anchors. The complete ARGUS suite passed 456
tests in 3.597s. Ghost procedure replay/candidate qualification, UI-TARS recovery,
and stored screenshot image serving remain pending and must not be presented as live.

`GHOST_API_URL` enables memory in `Worker.run` and the worker API; the new
`python -m Agents.browser_worker.ghost_cli` also runs tasks or explicit qualification. Lookup
selects compatible `0.2` workflows. DOM replay resolves fresh semantic targets, extracts
fresh fields and uses the independent validator with no model calls. Successful
exploration can save a candidate; a compilation or Ghost outage does not erase a
validated task result. Idempotency keys protect candidate/run writes.

DOM-insufficient/canvas cases can continue in the same Steel session through the existing
UI-TARS worker. Remaining budgets and cancellation cross the handoff; one owner releases
the session. An optional configured extraction recipe validates visual results against
fresh DOM records. Canvas-only answers without independent structured validation remain
inconclusive and cannot become candidates. Ghost's `0.2` qualification requires stored
fresh replay reports with changed inputs and an evidenced empty result; legacy `0.1`
fixture workflows are not reused by the new workers.

Checkout: `feat/ghost-api-interactive-demo`, based on main `60a45ec`. Existing Ghost API
work and this integration remain uncommitted. No teammate connection is marked Integrated.
Local Chrome plus the separate Ghost HTTP process verified exploration → candidate →
three fresh qualification runs → changed-input reuse, including correct records and
zero model calls on replay. Automated tests use simulated Steel/model responses for
visual handoff; no live credentials/model endpoint are configured here. See TEAM for
final check results. Authentication, general canvas validation, distributed session
locking, quarantine/repair and ARGUS/moderator receiver integration remain pending.
Final verification also checked the refreshed editable package outside the repository
and all 115 local links in the affected documentation. The temporary Ghost HTTP server
was stopped after the demo; no cloud sessions were opened for these checks.

## What exists now

[Agents/browser_worker/README.md](../../Agents/browser_worker/README.md) is the setup and usage entry point. The new `Agents/browser_worker/` module includes:

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

1. Configure Steel/OpenAI/UI-TARS credentials and a hosted read-only site, then run the
   connected CLI against it; localhost catalog proof does not establish cloud integration.
2. Thomas/Tianqi verify DOM → UI-TARS continuation in the same Steel session, including
   cleanup and a canvas answer that honestly remains inconclusive when validation is absent.
3. Sting verify the resulting `0.2` candidate, fresh qualification cases and changed-input
   reuse. Abel then consumes the worker report from ARGUS/moderator.
4. Record exact receiver revisions/results in TEAM before marking any connection Integrated.

ARGUS PR #10 now includes implementation commit `388764e` on `feat/argus-controller-base`. Contract 0.5 addresses Tianqi's remaining semantic-provenance finding: the moderator returns only an `AnswerSelection` of validated-record indices, field references and fixed-vocabulary notes. The controller rejects invalid selections, derives the records, and renders every user-facing line itself. Any moderator-authored prose is discarded before logging or persistence. Reconciliation may still select, drop, reorder or supersede accepted records, but cannot manufacture data.

The exact hostile phrase `is free and cures cancer`, supplied as moderator prose while citing a real record, is covered by a regression that proves neither string is published or stored. The cleanup also cleared the full Ruff baseline and makes the ARGUS lint job blocking. Checks actually run on the formatted tree before commit: `python3 -m unittest discover -s argus/tests -v` passed **453 tests in 1.249s** (Python 3.13.5); `ruff check argus`, `ruff format --check argus` and `git diff --check` passed. The registry fixture succeeded with controller-rendered lines; the vague open request ended `needs_input` at S4 with no session; the salary chain succeeded with four unique remote records in descending salary order. These are synthetic offline runs. No live model, browser, Steel, VLM, DNS or Ghost check was run. Remote CI for the pushed correction was pending at this checkpoint. Actual behavior and limits are in [argus/README.md](../../argus/README.md) and the newest ARGUS-1 block in [TEAM.md](TEAM.md).

## Visual worker module

`Agents/visual/` is merged on main. VLM-1 is Building, with reported parser, live Steel smoke, grounding calibration and a successful one-step Hacker News run. It still needs an interaction-step example that Ghost can compile. Preserve its [TEAM evidence](TEAM.md) and [module usage](../../Agents/visual/README.md); its ARGUS/Ghost receiver checks remain pending. These are the visual workstream's recorded results, not checks rerun for HTML-1.

## Dashboard UX prototype

`frontend/dist/` contains a dependency-free Mission Control prototype with Paper and Midnight themes. The UX review update synchronizes immutable event snapshots across a decision-oriented execution flow, vertical recorded-only activity, and before/after sample evidence. History stays pinned until Return to live/latest. The flow uses Plan/Execute/Verify stages, an explicit proof decision, and a labeled visual-recovery loop returning to fresh DOM verification. Wide layouts show the fitted primary route; narrower chart space switches to a vertical layout with a fit/readable control. Successful reuse, price-filter failure with visual recovery, browser unavailability with retry, pause/resume, cancellation, and five-result completion are simulated. Runs, Ghost Library, and Evidence have consistent hash navigation; smaller screens use workspace panel tabs. No live service connection is claimed or implemented. This verified revision is deployed as owner-private Sites version 4 at `https://argus-mission-control.sunyihan666.chatgpt.site`. Usage and limitations are in [`frontend/README.md`](../../frontend/README.md).

## Ghost API local module

`ghostapi/` now contains a simulated workflow registry and interactive flowchart viewer. It demonstrates exact workflow lookup, no-match discovery, SQLite candidate/version/run storage, parameter binding, replay, validation, qualification, and concurrent UI observation while queued work executes. The chart supports pointer dragging, zoom, history navigation, live following, and evidence inspection. The browser/catalog and discovery procedure remain fixtures; this is not evidence of Steel integration or automatic trace compilation.

An agent-facing FastAPI service now lives in `ghostapi/api/`. Steel-powered
subagents call it directly; ARGUS assignment is outside this service. It provides
strict version 0.1/0.2 lookup, candidate submission with explicit parameter origins,
run reporting, qualification, registry/activity reads, and SQLite persistence.
Running `python -m ghostapi.api` serves both the API and a live workflow graph at
`/graph`; `/docs` exposes OpenAPI. The new boundary has consumed real local browser
traces, but has not yet consumed a live Steel trace. Worker-supplied validation remains
the trust boundary; `0.2` qualification verifies stored run evidence and freshness metadata.

Prior FastAPI implementation validation: 4 FastAPI tests passed on Python 3.13; the
existing 12 Python demo tests and 5 JavaScript tests passed. A live localhost smoke
check verified `/health`, `/graph`, and `/openapi.json`. Compileall and the Git
whitespace check passed. Earlier coverage reports measured 72% Python coverage and
97.18% line / 91.30% branch coverage for the demo flowchart module; coverage was not
remeasured for the FastAPI addition. Changes remain uncommitted on
`feat/ghost-api-interactive-demo`.
