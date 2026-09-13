# ARGUS Mission Control prototype

## Purpose and entry point

A dependency-free UX prototype for supervising a browser-agent run. Entry point: [dist/index.html](dist/index.html), with [dist/app.js](dist/app.js) for behavior and [dist/styles.css](dist/styles.css) for Paper and Midnight themes.

The sample task is fixed: five wireless headphones under $150 CAD. All browser captures, workflows, checks, and records are fixtures. No ARGUS, Ghost, Steel, model, or worker API is called.

## Setup and local preview

No installation, build, or environment variables are required. From the repository root:

```sh
python -m http.server 4173 --bind 127.0.0.1 --directory frontend/dist
```

Open http://127.0.0.1:4173/. A modern browser supporting ResizeObserver and Array.findLast is required.

## Inputs, outputs, and failure examples

Choose a scenario and launch:
- Successful reuse: qualified sample Ghost steps feed the DOM worker; visual fallback is skipped; all five sample results are delivered.
- Filter failure & recovery: the page still shows Any price and a $199 item. Execution blocks, the price check fails, and no verified result is shown. Try visual recovery to correct the sample filter and resume verification in the same run.
- Browser unavailable: execution blocks without a capture. Retry connection simulates a restored session; Cancel ends execution and retains earlier events.

Pause/Resume controls execution. Cancel retains history and suppresses further actions. A new run may start after completion or cancellation; only one run is active at once.

## Inspection behavior

Only emitted events appear in the vertical activity list. Each event retains a snapshot of node states, checks, and before/after browser observations. Selecting an event or an already-executed node pins this shared history selection until Return to live/latest. Incoming activity never replaces the selected historical moment. Unrecorded nodes have no inspectable event.

Before/After compares sample page observations. Captions distinguish a newly captured observation from a previously available observation. Checks link to their supporting action. Completion exposes all five sample records and fixture source references; these are not external shopping links.

The execution map is a decision-oriented flow chart. Its primary route runs from Request through Result under Plan, Execute, and Verify stages. A diamond-shaped proof decision explicitly separates the successful route from the labeled Visual Worker recovery branch; recovery loops back into a fresh DOM check before evidence can continue. Future-node status labels are visually suppressed to reduce repeated noise while remaining available to assistive technology.

On wide desktops, the chart uses a fitted left-to-right overview so the complete route and the activity/evidence pair remain visible together; Readable size restores the unscaled canvas with internal scrolling. Below 900px of chart space, it changes to a readable vertical layout. Below 1050px of viewport width, Map/Activity/Evidence panel controls preserve content and the shared selection. Navigation remains named at narrow widths.

Runs and Evidence are hash-addressable pages backed by the current browser visit, not fabricated history counts. Ghost Library contains explicitly labeled sample workflows. The theme preference is stored locally if browser storage is available.

## Checks

```sh
node --check frontend/dist/app.js
git diff --check
```

Validate scenario progression, the proof-decision pass route, failed proof routing into visual recovery and back through DOM verification, pinned history while new events arrive, before/after capture changes, pause/resume/cancel, completed results, run reopening, evidence navigation, theme switching, keyboard tabs, responsive graph modes, and body bounds. Asset references are local: styles.css and app.js.

## Dependencies and limitations

No third-party dependencies or build pipeline. Browser-native HTML/CSS/JavaScript only. Hosted as static assets using frontend/.openai/hosting.json.

- Simulated snapshots are HTML representations, not actual browser screenshots or live video.
- Workflow qualification, source verification, and recovery are scripted fixtures, not runtime verification.
- Run/evidence records reset on reload; only theme preference persists.
- The fixed sample mission avoids suggesting arbitrary requests are executed.
- Runtime integration, real retries, server persistence, concurrent runs, and live connection status remain unfinished.
