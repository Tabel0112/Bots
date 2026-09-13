# ORION live demo — setup and results

This is the version shown in the recorded demo: ORION Mission Control on the
**live runtime**, which visits real public websites in Steel browser sessions and
returns fresh records, every one with its source URL and the observation it came
from. No model is called during a run; the three examples are served by fixed
public pages.

| Example | Site visited | What comes back |
| --- | --- | --- |
| Shopping | staples.com search pages for headphones and keyboards | cheapest item per category under the price limit, with price and source |
| Travel | en.wikivoyage.org/wiki/Toronto | six sourced stops over three days (Eat, See, Do) |
| Jobs | remotive.com software development listings | roles that show a salary range, with company and link |

## Setup (about 5 minutes)

1. Checkout `main`. Python with `argus/requirements.txt` and
   `ghostapi/requirements.txt` installed, plus `playwright` and `steel-sdk`
   (both are imported by the live runtime).
2. `.env` in the repository root with `STEEL_API_KEY`. Nothing else is needed for
   the live runtime.
3. Steel: open https://app.steel.dev and confirm **no live session**. The plan
   allows one; the live runtime opens sessions one at a time, but a leftover
   session from elsewhere blocks every run.
4. Start the Ghost registry for the demo in one terminal, from the repository
   root. It uses its own database file, separate from the runbook's:

```bash
GHOST_DATABASE_PATH=ghostapi/ghost-live.sqlite3 python -m uvicorn ghostapi.api.app:app --host 127.0.0.1 --port 8767
```

5. Register the three site workflows once (re-running changes nothing):

```bash
python ghostapi/demo/seed_live_workflows.py --url http://127.0.0.1:8767
```

   Expected output, one line per workflow, each ending in `qualified`.

6. Start Mission Control on the live runtime in a second terminal:

```bash
ARGUS_RUNTIME=scrape ARGUS_STORE=argus-runs/demo-live GHOST_API_URL=http://127.0.0.1:8767 GHOST_DATABASE_PATH=ghostapi/ghost-live.sqlite3 python -m uvicorn argus.api.app:create_app --factory --host 127.0.0.1 --port 4174
```

   On Windows Git Bash, put the variables on the same line as shown; in
   PowerShell set them with `$env:NAME = "value"` first.

7. Open http://127.0.0.1:4174. The sidebar must say **Live runtime**, and
   http://127.0.0.1:4174/api/health must show `"runtime": "scrape"`,
   `"steel_configured": true` and `"ghost_api_url": "http://127.0.0.1:8767"`.
   The **Ghost Library** tab must list three qualified workflows:
   `staples.com.search`, `en.wikivoyage.org.search`, `remotive.com.search`.

## Producing the results (about 1 minute per example)

Run **one example at a time**; wait for the previous run to finish before
starting the next.

1. On the Mission page, click **Shopping example** and **Run mission**. The
   request is the preloaded text:
   `Find headphones at most 150 USD and keyboards at most 100 USD. Show the cheapest matching product in each category, with its price and source.`
   Expect about 20 to 25 seconds, status **succeeded**, validation **passed**,
   and two records, one per category, each within its price limit with a
   staples.com source link.
2. Click **Travel example**, then **Run mission**. Expect about 20 seconds and
   six records with day, time, kind and a wikivoyage.org source link; each stop
   also carries the listing's own URL.
3. Click **Jobs example**, then **Run mission**. Expect about 15 seconds and the
   listings on the board that show a salary range, ranked by salary, with a
   remotive.com link. The count depends on the board that day; on 2026-09-13 one
   listing showed a range.

Every run appears under **Runs**; the **Live workflow** view shows the event
stream (interpreting, gating, planning, Ghost lookup, the Steel navigation and
extraction actions, validation, synthesis) and the inspector for any event.
The **Ghost Library** stays at the three workflows; each run's lookup is
recorded in the registry's activity.

The record text you type can differ from the examples: the live runtime
classifies the request by its wording (a product word such as headphones or
keyboard with a price, a Toronto trip, or remote software jobs). Anything it
cannot map ends with an honest "could not map this request" instead of a guess.

## Results recorded on 2026-09-13

Run from these exact commands, examples one after another:

| Example | Status | Records | Time |
| --- | --- | --- | --- |
| Shopping | succeeded, validation passed | headphones 149.00 USD, keyboard 49.95 USD | 24 s |
| Travel | succeeded, validation passed | 6 stops, days 1 to 3 | 21 s |
| Jobs | succeeded, validation passed | 1 role, $170k to $200k | 15 s |

## If something goes wrong

- **"The live browser subtask did not complete"** (`SESSION_UNAVAILABLE`): a Steel
  session was refused or the page did not load in 30 s. Check
  https://app.steel.dev for a leftover live session, release it, run again.
- **Zero records on Shopping**: staples.com changed its result markup or served a
  bot check. Open the source link from a normal browser; if the page shows the
  items, the extraction pattern in `argus/live_runtime.py` needs updating.
- **Ghost Library says "Registry unavailable"**: the Mission Control process was
  started without `GHOST_API_URL`, or the registry on :8767 is not running.
- **Sidebar still says something other than Live runtime after a restart**: the
  browser cached the old script; hard-refresh once.

## Do not claim

- That the live runtime uses a model, the DOM worker, or the visual worker. It is
  a fixed reader for three public pages, validated by ARGUS binding checks.
- That a live run replays a Ghost workflow. The registry lists the three site
  workflows and records each lookup; the browsing itself is the live runtime's.
- Any result other than the table above.
