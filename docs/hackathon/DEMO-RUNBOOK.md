# Demo runbook — 2026-09-14

What this demo proves, live: a typed request runs through ARGUS, a cloud browser
(Steel) on a public page, independent validation, and a controller-rendered
answer with evidence; a successful run becomes a Ghost skill; a qualified skill is
reused with zero worker model calls; an open-world request on a site ARGUS chooses
returns provenance-backed results; requests ARGUS cannot serve end honestly.

Everything below was run on 2026-09-13 (run ids in the ARGUS-3 and OW-1 blocks of
[TEAM.md](../ai/TEAM.md)). Nothing else is claimed.

## Before the demo (20 minutes, one person)

1. Checkout: `main` after PR #17 merges, or `feat/argus-3-followups`. `.env` must hold
   `OPENAI_API_KEY`, `ARGUS_MODEL=gpt-5.6-sol`, `OPENAI_MODEL=gpt-5.4`,
   `OPENAI_REASONING_EFFORT=medium`, `STEEL_API_KEY` (the key replaced on 2026-09-13).
2. Python environment with `argus/requirements.txt` and
   `Agents/browser_worker/requirements-tested.txt` installed (the worker's FastAPI pin
   wins; both suites pass with it).
3. Steel: open https://app.steel.dev and confirm **no live session**. The plan allows
   one; a leftover blocks every run. Release anything live.
4. Registry: use the demo database that already holds the qualified skill
   (`ghostapi/ghost-demo.sqlite3`, copied from the 2026-09-13 Steel runs). If it is
   missing, run step "Recreate the qualified skill" below.
5. Start three terminals from the repository root:

```bash
GHOST_DATABASE_PATH=ghostapi/ghost-demo.sqlite3 python -m uvicorn ghostapi.api.app:app --port 8766
```

```bash
ARGUS_RUNTIME=connected ARGUS_MODERATOR=module GHOST_API_URL=http://127.0.0.1:8766 WORKER_BROWSER=steel WORKER_SITES_FILE=demo-site/sites.hosted.json ARGUS_STORE=argus-runs-demo python -m argus.api
```

   (The third terminal is spare for the qualification script.) Open
   http://127.0.0.1:4173 and check the sidebar says **Connected runtime** and
   `/api/health` shows no problems.
6. Warm-up: run prompt 1 once. If it fails at "Could not initialize the browser
   session", wait ten seconds and run it again (the toolbox already retries once).
7. Hosted catalog: https://tabel0112.github.io/Bots-Hosting/catalog.html must load in
   a normal browser. The path is case-sensitive.

## Script (about 4 minutes)

Type the prompts exactly. Use "keyboard" singular and price limits 100, 150 or 200
(never 50 on Steel).

**1. Explore (about 45 s).** Prompt:
`Find headphones under 150 USD in the demo catalog and show the five cheapest with their prices and sources.`
Show: the event stream (interpreting → gating → planning → Ghost lookup "explore" →
worker actions fill/select/click/extract → moderator accept → validation passed),
the two records with prices and source links, and "skill candidate created".
Say: every record cites an observation from this run; the checks listed are the
worker's own verifier plus ARGUS binding checks.

**2. Reuse (about 20 s).** Prompt:
`Find headphones under 100 USD in the demo catalog and show the cheapest one with its price and source.`
Show: the match event's reason "qualified workflow demo-catalog.search_extract v1
compatible; the worker will replay it", the **Replayed qualified skill · 0 model
calls** badge, and the elapsed time versus step 1. Open Ghost Library: v1 qualified.
Say: qualification ran three fresh replays with changed inputs including an evidenced
empty result; reuse replays the recorded semantic steps without a model.

**3. Two categories (about 40 s).** Prompt:
`Find headphones under 150 USD and a keyboard under 100 USD in the demo catalog. Show the cheapest matching product in each category with its price and source.`
Show: two subtasks, both replayed, reconcile merged, 40 checks passed. Note honestly
that the answer lists three records ordered by price; per-category trimming is a
known open item.

**4. Open world (about 90 s).** Prompt:
`On en.wikipedia.org, search for Toronto condominium towers and list the first five matching articles in the order the site search returns them, with titles and links.`
Show: no site config exists for Wikipedia; the worker finds the search box itself,
searches, extracts five articles with links; the result panel lists the checks that
could not be applied on an open site. Say: this is the generic path; a site that
blocks automation (realtor.ca returned 403) ends with an honest failure, not an
invented answer.

**5. Honesty (about 15 s, optional).** Prompt:
`Give me the best condos for sale in Toronto with their prices and links.`
Show: ARGUS chooses a site on its own, then asks what "best" should mean rather than
guessing. Stop there or answer "lowest listed price" and show the site-access failure.

## If something goes wrong

| Symptom | Do |
| --- | --- |
| "Could not initialize the browser session" | A leftover Steel session or a transient reject. Check app.steel.dev, release any live session, wait ten seconds, rerun. |
| Run ends `needs_input` | The gate asked a question; answer it in the box under the result and the follow-up runs. |
| Sidebar says a different runtime, or health lists problems | The API started without its environment; fix the variables and restart terminal 2. |
| Reuse explores instead of replaying | The registry has no qualified skill (wrong database or fresh copy). Recreate it below. |
| Open-world run fails with "site access" | The site blocks automation. Use the Wikipedia prompt. |

## Recreate the qualified skill (about 3 minutes)

Run prompt 1 once (a candidate is saved), then from the repository root with the
same environment as terminal 2, run the qualification script used on 2026-09-13
(`docs/hackathon/scripts/qualify_hosted.py`) with the run id shown in the UI:

```bash
python docs/hackathon/scripts/qualify_hosted.py argus-runs-demo/runs/<run-id> demo-catalog.search_extract 1 docs/hackathon/scripts/qualify-inputs-steel.json
```

Expect three succeeded replays with zero model calls and `status: qualified`.

## Do not claim

Arbitrary websites; pagination or detail pages; visual fallback; repair of a changed
site; concurrent runs; hosted deployment; that any teammate has verified their
boundary (sign-off is tomorrow's first task).
