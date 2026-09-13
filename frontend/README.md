# ORION

## Purpose and entry point

Mission Control submits Shopping, Travel Plan, and Job Search demonstrations to
the ORION API and displays the actual controller workflow: stage changes,
subagent dispatch, Ghost matches, worker actions, moderator decisions, validation,
candidate creation, and the controller-published conclusion.

## Run

From the repository root with Python 3.11 or newer:

```bash
python -m argus.api
```

Open `http://127.0.0.1:4173`. Set `ORION_STORE` to choose the JSON run directory;
otherwise `argus-runs/` is used. Stop with Ctrl+C.

## Inputs, outputs, and failures

Enter a request in natural language and launch it. The three example buttons only
load sample text. The backend classifies the submitted words, extracts supported
parameters and criteria, and selects the live execution contract. Editing the
text can change the number of workers, price limits, trip length, remote filter,
salary ranking, and result limit.
Successful output includes controller-rendered lines, validated records, source
URLs, and observation IDs. Runs stream live and can be reopened. Cancel requests
propagate to ORION. Unsupported request domains or shopping categories return HTTP
422 with a clear message shown inline under the form; the request is never rerouted
to a different task. Missing runs return 404.

In connected mode, an open request that does not name a website can receive a
policy-checked model suggestion. The workflow header shows, for example,
`Site: realtor.ca, chosen by ORION`, using the persisted `site_choice` parameter.
Open-search results render normally, and the result panel separately lists worker
limitations under “Checks not applicable on an open site”; those skipped checks
are never presented as passes. The selected site and limitations remain visible
when a stored run is reopened.

The sidebar label comes from `/api/health`: "Connected runtime", "Controlled
fixture runtime" or "Scrape runtime (deprecated)". In the connected runtime the
example buttons are suggestions only; ORION interprets the text with its model, and
a run that ends `needs_input` shows the gate's question with an answer box that
starts a linked follow-up run. Records link to their evidence image when the run
has one stored. Runs made in the controlled runtime carry a "Fixture data" badge
in the run list and on the result panel, so sample data cannot be mistaken for a
live result. Those surfaces also identify whether persisted worker reports used a
qualified Ghost replay (zero model calls) or exploration, including the exploration
model-call total. The Ghost Library view reads `/api/workflows` and distinguishes
candidate workflow versions from qualified ones.

## Checks

```bash
node --check frontend/dist/app.js
python -m unittest argus.tests.test_demo_runtime -v
```

Browser checks cover all scenarios, event inspection, run reopening, cancellation,
theme switching, and responsive layouts.

## Dependencies and limitations

The frontend uses browser-native HTML, CSS, JavaScript, and `EventSource`. FastAPI
and Uvicorn serve it. The default backend calls Steel and reads current public pages
from Staples, Wikivoyage, and Remotive; `STEEL_API_KEY` is required. It uses Ghost's
local registry lookup and streams ORION, subagent, Ghost, worker-action, moderator,
validation, and synthesis events. Captures are observation references, not image
files. Run lists fetch each run snapshot to derive their execution badges, so a
large archive produces one detail request per displayed run. Qualification is an
API job rather than an interactive frontend control. GPT/UI-TARS recovery and
arbitrary page technologies remain unfinished. Open search is limited to generic
DOM controls and same-site read-only navigation; it does not prove ranking
quality, non-query filters, completeness or pagination, and its zero-result state
uses a visible-text heuristic. Site suggestion and the browser path require the
connected backend and its configured model/browser services; only offline fake
and local-browser coverage was run for OW-1. Static Sites hosting cannot execute
the local Python API without a separately hosted backend.

In the connected runtime a request that names no website is routed through ORION's
site suggestion: the workflow header shows "Site: <domain>, chosen by ORION" and the
worker runs the open-world `open_search` operation on that site. Checks that do not
apply on an unconfigured site are listed under the result as limitations.
