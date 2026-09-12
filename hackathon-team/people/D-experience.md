# Person D — dashboard, controlled demo, testing, and presentation

Name: TBD  
Branch/worktree: `feat/experience` (proposed; not created)  
Owned paths: `frontend/`, `demo-site/`, `docs/demo/`  
Current status: not started / not yet reported  
Last updated by owner: not yet  
Next checkpoint: dashboard using C's sample events plus controlled catalog v1  
Blocked by: frontend stack decision and C's versioned fixtures

## Mission

Make the system understandable and testable. Build the dashboard from agreed examples, create a deterministic controlled catalog with two UI versions, lead the shared acceptance checklist, and prepare the three-minute demonstration. The interface must reveal what actually happened: exploration, candidate creation, qualification, reuse, repair, fallback, validation failure, or terminal failure.

This role fits someone who can understand code and build with AI assistance. Work in small, visible slices; have the coding assistant inspect current files and contract examples before editing. Check every generated UI state manually. C owns backend contract changes and helps connect the API.

## Interfaces and dependencies

| You receive | From | You deliver | To |
| --- | --- | --- | --- |
| Versioned sample events/results/skills, then live API | C | Dashboard and API-client feedback | C |
| Safe evidence and browser behavior | A | Human-readable action/evidence views | Judges/team |
| Match, validation, qualification, and repair reports | B through C | Faithful skill/validation states | Judges/team |
| Operation/check definitions | B/C | Controlled catalog truth set and scenarios | A/B/C |

Keep presentation components separate from the API client so C can help with integration without rewriting the interface. Sample mode must be visibly labeled.

## Work order and acceptance checks

### D1. Dashboard shell with sample events — hours 0–4

- Build one request form using the frozen fields; plain-language input is optional until C supports it.
- Show current run stage, event timeline, interpreted parameters, results, and validation summary.
- Add a skill view with skill ID/version, candidate/qualified/quarantined status, inputs, steps, source run, and qualification cases.
- Implement empty, loading, API-disconnected, validation-failed, and terminal-failed states.
- Do not add controls that lack complete backend behavior.

Done when every sample scenario supplied by C renders clearly and the screen works at a normal laptop width. Check a narrow width if time permits; preserve readable text and controls.

### D2. Controlled catalog v1/v2 — hours 1–7

- Build a small deterministic catalog with known titles/prices/currency and filters matching the operation contract.
- Version 1 and version 2 produce the same semantic result but expose one deliberate supported target change, such as a label/role/container change.
- Provide one unsupported change state for S9.
- Expose an explicit version switch for team testing; hide or avoid changing it during the normal user workflow.
- Document the exact expected result sets for fixed inputs.

Done when A can explore v1 and B can validate results against the truth set. Version 2 must cause the intended old-target failure before repair; changing only CSS colors does not test target repair.

### D3. Connect live API with C — hours 5–10

- Put API access in one small client module.
- Reconnect through current-run polling if the event stream disconnects.
- Deduplicate by run ID and event sequence; render one terminal state.
- Keep sample/live data visually distinct and never silently fall back to samples.

Done when one real exploration initiated in the dashboard reaches a validated result and remains understandable after a simulated event-stream interruption.

### D4. Make learning/reuse/repair legible — hours 8–16

- Show why the matcher explored or selected a skill.
- Show bound parameters and skill version used.
- Distinguish task completion, candidate creation, and qualification.
- On repair, show old version failure, repair candidate, qualification result, and final version/fallback.
- Show measured elapsed time, browser actions, model calls, and tokens only when supplied by the backend; render unavailable values honestly.

Done when a teammate unfamiliar with the code can explain S1–S9 from the UI without reading server logs.

### D5. Lead evaluation and presentation — hours 12–24

- Own [EVALUATION.md](../EVALUATION.md); ask each technical owner for actual evidence.
- Run manual acceptance after feature freeze and record the tested revision.
- Write a concise demo script with one speaker and a backup speaker.
- Capture one successful backup video before final visual polishing.
- Ensure project claims match demonstrated scope and measurements.

## Suggested interface layout

```text
┌─────────────────────────────────────────────────────┐
│ Request form                         Run / Stop*     │
├─────────────────────┬───────────────────────────────┤
│ Run timeline        │ Browser evidence / results    │
│ match               │ interpreted parameters        │
│ explore or reuse    │ extracted records             │
│ validate            │ validation checks             │
│ compile/repair      │ measurements                   │
├─────────────────────┴───────────────────────────────┤
│ Ghost skill: version, state, inputs, steps, tests   │
└─────────────────────────────────────────────────────┘
* Show Stop only after C implements cancellation end to end.
```

Prioritize readable state and evidence over animation. Keep the judge-facing language plain: “Using learned skill v2” and “Validation failed: one result exceeded the maximum price.”

## How my code works — owner fills this from the implementation

- App entry point and run command:
- Main components/pages:
- API client and sample/live mode boundary:
- Event ordering, deduplication, reconnect behavior:
- State model and terminal-state rendering:
- How evidence, validation, skill state, and metrics are displayed:
- Controlled catalog data, filters, v1/v2 difference, and truth set:
- Accessibility/responsive checks actually performed:
- Files a teammate should read first:

## Current task board

| ID | Task | Status | Evidence / revision | Next action |
| --- | --- | --- | --- | --- |
| D1 | Dashboard shell | Not started | — | Await stack and fixtures |
| D2 | Request/timeline/results states | Not started | — | Render sample events |
| D3 | Skill/version/validation view | Not started | — | Use contract examples |
| D4 | Controlled catalog v1 | Not started | — | Freeze truth data |
| D5 | Controlled changes v2/unsupported | Not started | — | Coordinate expected failure with A/B |
| D6 | Live API connection | Not started | — | Pair with C |
| D7 | Acceptance and demo | Not started | — | Maintain EVALUATION.md |

## Manual UI and demo checks

| Scenario/state | Status | Revision / notes |
| --- | --- | --- |
| Sample vs live labeling | Not checked | — |
| Loading and queued | Not checked | — |
| Exploration success | Not checked | — |
| Qualified reuse | Not checked | — |
| Candidate remains candidate | Not checked | — |
| Validation failure | Not checked | — |
| Repair and fallback | Not checked | — |
| API disconnect/reconnect | Not checked | — |
| Empty results | Not checked | — |
| Laptop/narrow layout | Not checked | — |

## Handoffs and requests

| Time | To/from | Exact request or delivered artifact | Needed by | Status |
| --- | --- | --- | --- | --- |
| — | C | Versioned sample events/results/skills and API base path | Hour 1–3 | Pending |
| — | A/B | Agree on controlled v2 change and truth set | Hour 6–10 | Pending |
| — | Team | Draft script and fixed scenario values | Hour 16 | Pending |

## Validation actually run

| Time | Revision | Check/scenario | Result | Artifact/notes |
| --- | --- | --- | --- | --- |
| — | — | No checks run | Not run | Initial planning state |

## Decisions, blockers, and risks

| Time | Type | Detail | Owner / next step |
| --- | --- | --- | --- |
| — | Decision needed | Select familiar frontend stack | D + C at kickoff |

## Checkpoint update template

```text
Time / revision:
Status and visible behavior:
Files changed:
API/sample behavior used:
Manual/automated checks actually run and result:
Unverified state / blocker and owner:
Next action before the next gate:
```
