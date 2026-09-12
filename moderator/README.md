# Moderator (provisional ARGUS harness)

Dispatches subtasks to browser subagents, monitors them live while they work, and
combines their worker reports into one final user-facing result.

Boundary: moderator behavior belongs to the ARGUS workstream (Abel). This module is a
runnable harness for INT-2 wiring — event and result shapes follow
`docs/hackathon/CONTRACTS.md` v0.1 concepts and are `0.1-provisional` pending INT-1.

## What it does

1. Accepts a run spec (task + subtasks with IDs, start URLs, budgets).
2. Spawns each subtask as a `workers/visual` subprocess (bounded concurrency,
   default 1 — Steel plan limit).
3. **Monitors while running**: tails each worker's `trace.jsonl`, emits ordered
   contract-style events (`stage_changed`, `action_observed`, `run_completed` /
   `run_failed`) to stdout and `events.jsonl`; flags stalls (no activity 120s) and
   kills workers that exceed the per-subtask time budget (`BUDGET_EXCEEDED`).
4. **Combines at the end**: aggregates every worker `report.json` into one result
   (per-subtask outcomes, findings, evidence refs, failures, summed metrics) and
   synthesizes the final user answer with an OpenAI model
   (default `gpt-5.6-sol`, override with `MODERATOR_MODEL`). With no
   `OPENAI_API_KEY`, or on a model error, it falls back to a deterministic combined
   summary — failures are reported honestly either way.

## Entry point

Run from the repository root (not from inside `moderator/`):

```bash
python -m moderator --spec moderator/example_run.json
```

Quick single-task mode:

```bash
python -m moderator --task "Find the top story on Hacker News and report its title and points." --url https://news.ycombinator.com
```

Outputs under `moderator/runs/<request_id>/`: `events.jsonl` (ordered run events),
`<subtask_id>/` (each worker's trace, screenshots, report, stdout), and
`final_result.json` (combined result + `final_answer`).

## Environment

Read from `.env` at the repo root or `workers/visual/.env` (names only; values
untracked): `OPENAI_API_KEY` (synthesis), `MODERATOR_MODEL` (optional), plus the
worker's own `STEEL_API_KEY` / `UITARS_BASE_URL`.

## Dependencies

`pip install -r requirements.txt` (openai), plus `workers/visual/requirements.txt`
for the workers it spawns. The visual worker needs its UI-TARS server running (see
`workers/visual/README.md`).

## Failure handling

- Worker crash / non-zero exit with no report → subtask marked failed with the exit
  code; run status `failed`.
- Budget exceeded → worker killed, `BUDGET_EXCEEDED` event, honest failure in the
  final result.
- Synthesis model unavailable → deterministic fallback answer, flagged in
  `synthesized_by`.
- Overall `status` is `succeeded` only when every subtask succeeded — and that means
  *workers completed*, not *result validated*; see the harness-check finding above.

## Harness check finding — worker reports are not ground truth

A two-subtask run against Hacker News completed cleanly (all events ordered, both
workers succeeded, answer synthesized). Checking the answer against the workers' own
saved screenshots showed **both reported values were wrong**:

| Worker claim | Screenshot ground truth |
| --- | --- |
| top story "168 points" (162 on a repeat run) | 182 points — misread, and not stable across runs |
| most comments: "We must pace the frontier", 430 | 430 is correct for that story, but "google.com/goto: Google's anti-scraping update" had 458 — wrong story selected |

The mechanics were fine; the epistemics were not. A vision model reliably reads a
*title* but misreads *numbers*, and "find the maximum" needs a scan the worker did not
perform. This is precisely the contract's "operational click success is distinct from
final task validation".

Consequence in this module: the moderator never presents worker output as validated.
`status` reports only whether workers completed (`workers_completed`), `validation` is
a separate block that stays `inconclusive` until a real validator is wired, and the
final answer carries an explicit unverified note. Closing the gap needs Ghost's
`validate(request, items, evidence, checks)` or a controlled-site truth set — that is
the INT-2 seam, not something this module should fake.

## Known limitations

- Routes to the visual worker only; HTML-worker routing and Ghost `match/validate`
  calls land at INT-2 when those boundaries are callable.
- No cancellation endpoint yet; no retries (per contract: no recursive retry loops).
- `gpt-5.6-sol` synthesis is prompt-only combination; it is instructed not to invent
  data beyond worker reports, and the deterministic digest is always preserved in
  `final_result.json`.
