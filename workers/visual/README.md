# Visual browser worker (VLM-1)

Vision-driven browser subagent for ARGUS. Receives one subtask, drives a Steel cloud
browser session with a VLM (screenshot → action loop), and returns a worker report with
step-level evidence (screenshots, semantic DOM targets, network requests) that Ghost can
compile into candidate skills.

Two interchangeable VLM backends:

- **`uitars` (default)** — UI-TARS-1.5-7B running locally on an OpenAI-compatible server
  (llama.cpp `llama-server`, Q4_K_M quant, fits an 8GB GPU). No API cost.
- **`claude`** — Claude Opus 5 with the `computer_toolset_20260801` computer-use tool.
  Needs `ANTHROPIC_API_KEY`.

## Entry point

```bash
cd workers/visual
python -m browser_subagent "find the top story and report its title and points" \
    --url https://news.ycombinator.com \
    --backend uitars --max-steps 40 \
    --request-id request-example --subtask-id subtask-1
```

Prints a JSON result to stdout (exit 0 on success, 1 on failure) and writes
`runs/<ts>/report.json` (worker report), `runs/<ts>/trace.jsonl` (action trace) and
`runs/<ts>/steps/*.png` (observations). The Steel live viewer URL is printed at start.

Python API: `from browser_subagent import UITarsSubagent, load_env` →
`UITarsSubagent().run(subtask, start_url=...)`.

## Environment

Names only, values in an untracked `.env` in this directory (`KEY=value` lines):

- `STEEL_API_KEY` — required (Steel cloud browser).
- `ANTHROPIC_API_KEY` — only for `--backend claude`.
- `UITARS_BASE_URL` — OpenAI-compatible endpoint, default `http://127.0.0.1:8080/v1`.

## Local UI-TARS server

One-time setup (files live outside the repo):

1. llama.cpp CUDA build in `C:\Users\Thomas\llamacpp` (from ggml-org/llama.cpp releases,
   `llama-bNNNN-bin-win-cuda-12.4-x64.zip` + `cudart-...zip`, unzipped together).
2. Model in `C:\Users\Thomas\models`: `UI-TARS-1.5-7B.Q4_K_M.gguf` +
   `UI-TARS-1.5-7B.mmproj-f16.gguf` (HF `mradermacher/UI-TARS-1.5-7B-GGUF`).

Start the server:

```bash
C:/Users/Thomas/llamacpp/llama-server.exe -m C:/Users/Thomas/models/UI-TARS-1.5-7B.Q4_K_M.gguf --mmproj C:/Users/Thomas/models/UI-TARS-1.5-7B.mmproj-f16.gguf -ngl 99 -c 8192 --port 8080
```

## Dependencies

Python 3.11+. `pip install steel-sdk playwright pillow httpx anthropic` (anthropic only
for the claude backend; playwright is used for CDP navigation/DOM evidence only — no
browser download needed).

## Input / output / failure

- Input: subtask string + optional start URL (+ request/subtask IDs for the report).
- Output: worker report (`report.json`) aligned with CONTRACTS v0.1 concepts —
  `outcome`, `summary`, `findings`, per-step `actions` (step_id, action, semantic_target,
  observation_before/after refs, outcome, timestamp), `evidence` (screenshots, session
  replay URL, network requests), `metrics` (elapsed_ms, browser_action_count,
  model_call_count). Shape is provisional pending INT-1.
- Failures are explicit: per-step `failures` list; run-level `outcome: failed` with
  reason in `summary` (max-steps exceeded, model refusal, unparseable outputs, action
  errors). Coordinates are recorded alongside the DOM element under the click
  (`semantic_target`) — coordinates alone are not treated as durable locators.

## Tests

```bash
python test_parser.py    # UI-TARS action parser + coordinate rescaling (offline)
python smoke_test.py     # live Steel session: navigate/screenshot/act/element/network
```

## Known limitations

- UI-TARS end-to-end run not yet validated (local server setup in progress); the Steel
  layer and parser are tested. The claude backend loop is written but needs an API key
  to run.
- `type` uses trailing `\n` to submit; canvas-only pages depend entirely on the VLM's
  visual grounding; no auth flows (out of MVP scope).
- Report shape is provisional (`0.1-provisional`), to be settled at INT-1.
