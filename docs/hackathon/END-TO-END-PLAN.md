# ARGUS end-to-end integration plan

Date: 2026-09-13. Status: proposed implementation plan requested by the user;
this checkpoint implements no runtime changes and establishes no new live evidence.
Inspected checkout: `feat/ghost-api-interactive-demo`, HEAD `35f6cb1`, with existing
staged component/integration changes. Preserve those changes. Assignments and receiver
verification remain in [TEAM](../ai/TEAM.md).

## Outcome

One request entered in Mission Control produces an interpreted request, a bounded
subtask plan, real Steel browser execution, independently checked results, and a
conclusion linked to its evidence. The same interface shows Ghost exploration,
candidate creation, qualification and subsequent reuse. Users can inspect a running
subtask, follow its actual actions, cancel it, and reopen the completed run.

The first proof uses a configured read-only catalog. General public-site tasks remain
the product objective and require the separate worker expansion below. Finishing the
catalog demo does not finish that broader capability.

## What exists, and what must connect

| Component | Evidence in this checkout | Integration work |
| --- | --- | --- |
| [ARGUS](../../argus/README.md) | Controller, interpretation/gating/planning, dependencies, concurrency, provenance checks, JSON storage and ordered events exist. CLI wiring uses fake toolbox/moderator/Ghost; real toolbox mode is unavailable. | Runtime composition, real adapters, live action events, cancellation propagation and UI API. |
| [DOM worker](../../Agents/browser_worker/README.md) | Async `Worker.run`; configured `search_extract`; structured observations, field provenance and independent verification. | Translate ARGUS contracts, lend controller-owned sessions, expose action/evidence events and browser captures. |
| [Worker/Ghost connection](../../ghostapi/INTEGRATION.md) | Lookup, exploration/replay, candidate storage and fresh qualification work locally through the Ghost API. Same-session visual continuation is implemented with simulated handoff checks. | Consume this path from ARGUS; verify it with live Steel and models. |
| [Visual worker](../../Agents/visual/README.md) | UI-TARS runner and integrated Ghost handoff exist; separate historical live visual evidence is recorded in TEAM. | Verify continuation on the lent session, accounting and independently checkable results. |
| [Moderator](../../moderator/README.md) | Standalone harness dispatches visual workers and aggregates prose; its README records incorrect numerical findings. | Implement ARGUS's `assess_report`, `reconcile`, `synthesize` protocol with structured selections. |
| [Mission Control](../../frontend/README.md) | Existing map, activity, history and evidence interface; all execution and captures are fixtures. | Real request entry, dynamic subtask graph, API/SSE transport, persisted runs and actual evidence/results. |

These are source-inspection findings. Historical test counts and Steel smoke checks
remain attributed to their checkpoints; no model, browser or runtime tests ran for
this planning task.

## Workflow

```text
User enters task in Mission Control
  -> Run API creates request_id and run_id, starts ARGUS, streams events
  -> ARGUS interprets goals, sites, parameters and explicit success criteria
  -> Gate accepts, asks for clarification, or returns a typed rejection
  -> Planner creates subtasks with dependencies and bounded budgets
  -> Controller dispatches ready subtasks through its in-process toolbox
       -> acquire capacity and lend one Steel session per active subtask
       -> worker asks Ghost for a compatible qualified workflow
            match: replay with current inputs and fresh observations
            miss: DOM observation/action/extraction loop
       -> eligible DOM insufficiency: visual continuation in the same session
       -> independent checks produce records + checks + evidence + failures
       -> worker reports execution to Ghost; valid traces may become candidates
  -> Moderator assesses reports and reconciles conflicts/gaps
  -> Controller coordinates bounded verification and checks overall acceptance
  -> Moderator returns AnswerSelection over validated records
  -> Controller renders conclusion, source links and limitations
  -> sessions are released; one terminal result is persisted and shown in UI

Separate learning job:
candidate -> fresh qualification cases -> qualified version
          -> later request -> changed-input replay -> fresh validation
```

Monitoring runs throughout execution. Reconciliation and final selection happen only
after their input reports are available. A moderator decision cannot replace a failed
validator check. Qualification is a separate job with its own cost and progress;
finishing a user request does not automatically qualify a skill.

## First worked request

On the hosted controlled catalog:

> Find headphones under 150 USD and keyboards under 100 USD. Show the cheapest
> matching product in each category, with its price and source.

| Work | Inputs | Dependency | Expected output |
| --- | --- | --- | --- |
| Subtask A | `query=headphones`, `max_price=150` | None | Fresh matching records, price/currency/filter checks and evidence. |
| Subtask B | `query=keyboard`, `max_price=100` | None | The same report shape for keyboards. |
| Reconcile, validate, select | Accepted A and B reports, explicit minimum-price criterion | Both reports | Cheapest validated record per category, or an evidenced empty result. |

A and B can run concurrently in separate sessions. Selection is controller/moderator
work, not a third browser worker. The controlled truth set verifies that the minimum
is correct; a public-site run with incomplete coverage must qualify the ranking as
limited to observed results. Prices, counts and the final conclusion come from the
run, never from the dashboard's five-result CAD sample. USD here is an explicit
proposed demo input, not a silent currency conversion or new product default.

After this works, demonstrate a dependent request in which a search's URLs become
the next subtask's extraction inputs. ARGUS already supports dependency binding;
the current worker needs the expanded input/operation contract described below.

## Integration choices to implement

### One controller and one memory execution path

ARGUS owns scheduling, budgets, retries, session lifetime and terminal publication.
The moderator adapter returns decisions; do not invoke the standalone moderator
harness as another dispatcher. Keep the existing worker-to-Ghost HTTP connection;
ARGUS calls its toolbox in-process.

The old [Phase 3 prompts](ARGUS-HANDOFF-3.md) predate the new worker/Ghost connection.
Their `GhostDemoAdapter`, fixture validator and separate visual-session prerequisite
are not the plan for this live path. Use `GhostWorkflow` and its existing visual
adapter, and verify their actual lifecycle behavior.

Today ARGUS stage 4 calls `Ghost.match`, stage 9 calls `Ghost.validate`, and stage 11
calls `Ghost.compile`. The real HTTP API is lookup/candidate/run/qualification;
it has no independent `/validate` endpoint. Proposed compatibility approach:

- Add an explicit worker-resolved execution mode for live adapters, preserving the
  current fake modes. Stage 4 records that lookup is pending, then the worker emits
  the actual explore/reuse decision. Never label a run reuse before lookup resolves.
- Keep lookup, replay, run reporting and candidate submission in `GhostWorkflow`.
  The ARGUS bridge reads their result metadata; stage 11 must not submit a duplicate
  candidate. Ghost remains the authoritative skill registry; ARGUS stores references.
- Give stage 9 the native worker report context for both registered and open tasks.
  A validation adapter checks its exact run/subtask/record/evidence bindings and runs
  applicable deterministic checks using the worker verifier. Obtain a fresh browser
  observation when needed. Do not trust a success flag or validate live records against
  the Ghost demo's fixture catalog. Missing proof is inconclusive.
- Preserve task success if Ghost persistence/compilation fails. Distinguish task
  success, candidate status and qualification status in the result and UI.

### Contracts and runtime

Create a narrow translation layer between ARGUS `SubtaskInput`/`WorkerReport` and
worker `SubtaskRequest`/`SubtaskReport` 0.2; do not rename all component contracts.
Map IDs, configured operations, schemas, parameters, success checks, allowed actions,
typed failures and metrics explicitly. Preserve each record's `source_observation_id`,
retrieval time and field provenance. Keep native evidence available for validation.
Unknown operations or schemas fail before session creation, with no made-up defaults.

ARGUS is synchronous and dispatches worker threads; the DOM worker and Ghost client
are asynchronous. Use one managed async runtime for browser/client work and a
synchronous toolbox facade. Keep Playwright clients on the loop that created them;
avoid independent `asyncio.run` calls around persistent browser resources.

The session manager lends `ownership=argus`, `close_on_finish=false` sessions and
releases them once after all users have stopped. Bound global browser/visual capacity
to four across normal runs and qualification jobs. Start the first UI milestone with
one active user run and up to four subtask slots. Route cancellation to the worker's
async event, join in-flight actions within configured timeouts, then clean up.
DOM replay/exploration, visual fallback, moderator-triggered attempts and qualification
must have explicit budget accounting; fallback does not reset the remaining budget.

### Final conclusion

Reuse ARGUS contract 0.5: the moderator selects validated record indices and fields
plus fixed-vocabulary notes; the controller derives and renders the answer. Implement
explicit filter/rank/limit criteria over those records. Any additional aggregate or
comparison sentence needs a deterministic calculation and a typed, provenance-bound
representation before publication. Free-form moderator prose is not evidence.

Show the selected answer first, then records with source links, validation checks,
unresolved gaps and measured execution details. Failed/inconclusive runs can show
their collected evidence, but cannot present it as a verified conclusion. Preserve
the controller's current terminal statuses initially; partial success is a later
contract change, not a UI invention.

## Code to write

Paths below are proposed additions/edits, not files already implemented.

| Files | Responsibility |
| --- | --- |
| `argus/adapters/worker_contracts.py` | Request/report translation, capability checks, provenance and failure/metric mapping. |
| `argus/adapters/steel_sessions.py`, `dom_toolbox.py` | Shared async runtime/session lifecycle and sync toolbox facade over `GhostWorkflow`; cancellation, capacity and fresh verification observations. |
| `argus/adapters/ghost_bridge.py` | Worker-resolved memory decisions, native validation context and candidate references without duplicate writes. |
| `argus/adapters/moderator.py` | Assess, reconcile and structured selection using the existing model boundary; bounded calls and deterministic fallback where valid. |
| `argus/runtime.py`, `argus/__main__.py` | Compose real services for live execution; explicit configuration validation and retained offline mode. |
| `argus/interfaces.py`, `contracts.py`, `controller.py`, `store.py` | Small contract extensions for actual worker progress, cancellation, memory resolution and report validation context; durable plan/evidence/event reads. |
| `argus/api/app.py`, `service.py` | Submit/list/read/cancel runs, clarification follow-up, SSE and controlled artifact serving; serve the existing frontend alongside the API locally. |
| `Agents/browser_worker/runner.py`, `schemas.py`, `browser/adapter.py`, `ghost.py` | Typed incremental action/observation/check/memory events; screenshots tied to observation IDs; shared remaining budgets. Phase callbacks alone are insufficient for the evidence UI. |
| `frontend/dist/api.js`, `run-store.js`, `app.js`, `index.html`, `styles.css` | Connect request entry and real run state, construct subtask nodes from the plan, render real captures and conclusion; keep sample mode explicit. |
| `ghostapi/api/`, worker Ghost/visual adapters | Qualification job connection, then supported quarantine/repair/versioning behavior; preserve existing lookup/candidate/run APIs. |
| `tests/integration/`, existing module tests, frontend browser tests | Cross-component contracts, failure paths, session cleanup, qualification/reuse and actual UI workflows. |

Maintain each affected module README as implementation lands. Record exact checks and
receiver revisions in TEAM. No framework rewrite or additional task-graph engine is
needed for this plan.

## UI API and events

Use a Python FastAPI boundary for the browser UI. Keep Ghost as its existing separate
service. A same-origin local API/frontend setup is the first target; the currently
hosted static prototype needs a reachable hosted backend and access configuration
before it can run live tasks.

| Proposed endpoint | Behavior |
| --- | --- |
| `POST /api/runs` | Text request plus client idempotency key; return `202` with run/request IDs after durable creation. Same key/body returns the same job; conflict returns `409`. |
| `GET /api/runs`, `GET /api/runs/{id}` | Persisted history and current snapshot/final result. |
| `GET /api/runs/{id}/events` | SSE over persisted event sequence; reconnect after `Last-Event-ID`, replay missing events, then follow live. |
| `POST /api/runs/{id}/cancel` | Request actual worker cancellation; display cancellation pending until cleanup/terminal event. |
| `POST /api/runs/{id}/clarifications` | Create a linked follow-up request using the original text plus explicit answer; preserve the original terminal `needs_input` run. |
| `GET /api/runs/{id}/evidence/{observation_id}` | Return registered DOM/capture artifacts within that run, without accepting arbitrary filesystem paths. |
| `GET /api/workflows` | Read the real Ghost registry for the library. |
| `POST /api/workflows/{id}/versions/{version}/qualifications` | Schedule a bounded fresh-session qualification job with explicit cases and visible progress. |

Extend the existing `Event` envelope (`run_id`, `sequence`, `timestamp`, `type`,
`stage`, `message`, `data`). Add subtask/execution IDs and evidence references in
typed payloads. Useful events include plan creation, subtask start, memory decision,
action completion, observation saved, visual handoff, check result, moderator decision,
candidate outcome and terminal result. Serialize worker emissions through the run's
existing ordered writer. Expose actions and decision summaries, not private model
reasoning. Persist evidence before emitting its link; exclude provider credentials
and browser connection handles.

Mission Control consumes snapshots plus events and preserves its pinned historical
selection while newer events arrive. Stream disconnection means connection lost, not
run failed. Reload restores the run. Pause/resume has no backend implementation today;
disable it in live mode until cooperative pause is implemented. Recovery buttons must
also map to real bounded operations before they are enabled.

## Build order and acceptance

1. **Contract/configuration baseline.** Agree the mapping above, one hosted controlled
   catalog and its exact schemas/criteria. Resolve the recorded worker model allowlist
   mismatch through supported explicit configuration; preserve user settings. Confirm
   the ARGUS model setting and visual endpoint independently. Pass adapter contract
   tests, including unknown operations, wrong IDs, currencies and missing proof.
2. **One real request through the UI.** Implement runtime/adapters, moderator boundary,
   submit/read/SSE/cancel and one configured search. Pass one run from typed input to
   validated output with real Steel/model evidence, visible actions, persisted artifacts
   and released sessions. No fixture interpreter/planner/result may stand in for the
   natural-language path in this acceptance check.
3. **Real decomposition and conclusion.** Run the two-category example with separate
   sessions and visible task nodes. Check correct selection against the truth set;
   exercise worker failure, bad-record rejection, cancellation and SSE reconnect/reload.
   Verify that failed evidence cannot be published as a successful conclusion.
4. **Visible learning and reuse.** Show actual trace -> candidate -> three fresh
   qualification cases (including changed inputs and evidenced empty results) ->
   qualified -> changed-input reuse. Display actual records and model-call counts.
   Worker replay can have zero reasoning calls while ARGUS/moderator still use models;
   measure both scopes and qualification cost separately.
5. **Visual continuation and supported repair.** Prove one eligible DOM failure hands
   the same session to UI-TARS and returns to independent validation. Add a controlled
   second UI version, detect incompatible/broken skills, quarantine the affected scope,
   perform one bounded repair, save a new candidate version and requalify in fresh
   sessions. Compatibility rejection is distinct from an executed replay failure;
   demonstrate each honestly. Unsupported changes/canvas-only unproven answers remain
   inconclusive or fail. This repair capability is new work, not existing behavior.
6. **General-task worker expansion.** Extend operation/input contracts for `navigate`,
   `search`, `open_results`, `extract`, typed dependency outputs and evidence-grounded
   generic extraction. Supply a runtime public-network/action policy and per-task
   validation that does not require prewritten site selectors or silently omit user
   criteria. Verify held-out read-only sites plus a search -> detail-extraction chain.
   Unknown or unprovable capabilities must be explicit; do not weaken configured-site
   guards or fabricate a `SiteConfig` to make an arbitrary plan dispatch. This is a
   separate substantial implementation phase, not a contract rename.

The existing MVP also requires a suitable public-site example alongside the controlled
site. Select and access-check that site during the baseline, configure its read-only
workflow, then repeat the connected request and learning checks there. Generality
claims depend on the held-out tests in step 6.

## Decisions and remaining limits

- Proposed: retain ARGUS JSON run/evidence storage and the already implemented Ghost
  SQLite registry. This is an explicit exception to the older project-wide JSON-only
  decision; settle it at INT-1 before implementing new persistence behavior.
- Proposed: first connected workflow uses the existing supported search/extraction
  operation, then expand worker capabilities. Do not claim that the current open-world
  ARGUS planner makes arbitrary live browsing work.
- Needed for live acceptance: a reachable configured site, compatible model settings,
  Steel capacity and a reachable UI-TARS endpoint for visual checks. The recorded
  Steel-only smoke does not establish a complete model/browser/Ghost run.
- Authenticated actions, purchases and submissions remain outside this read-only
  demonstration. The existing later action/confirmation design is unchanged.
- Use the existing [evaluation checklist](EVALUATION.md) for final evidence and demo
  readiness. Capture a labeled backup from verified runs. No component connection is
  marked Integrated until its receiver has verified the exact revision and report.
