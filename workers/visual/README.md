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

One-time setup (model files live outside the repo, any location):

1. llama.cpp release build for your GPU (from ggml-org/llama.cpp releases; on Windows
   CUDA, `llama-bNNNN-bin-win-cuda-12.4-x64.zip` + the matching `cudart-...zip`,
   unzipped together).
2. `UI-TARS-1.5-7B.Q4_K_M.gguf` + `UI-TARS-1.5-7B.mmproj-f16.gguf` from HF
   `mradermacher/UI-TARS-1.5-7B-GGUF` (Q4 fits an 8GB GPU; F16 on 24GB+ removes
   quantization grounding error).

Start the server (local example — substitute your own paths):

```bash
llama-server -m <models>/UI-TARS-1.5-7B.Q4_K_M.gguf --mmproj <models>/UI-TARS-1.5-7B.mmproj-f16.gguf -ngl 99 -c 8192 --port 8080
```

Point `UITARS_BASE_URL` at the server if it is not on `127.0.0.1:8080` (e.g. a LAN
workstation running the F16 model).

## Coordinate convention

- UI-TARS-1.5 action coordinates are treated as **raw screenshot pixels** (no
  smart-resize rescaling). Verified by a 3-point grounding calibration
  (`calibrate_vision.py`): coordinates came back within ~1% of original pixel space.
- Steel mouse/keyboard actions operate in **full-window screenshot space** (the
  screenshot includes the browser chrome), so model clicks map 1:1 to actions.
- DOM APIs use page-viewport space, which sits at a constant offset from screenshot
  space (~(4, 87) px, browser chrome + border). The offset is measured empirically once
  per session with a mousemove probe, and `semantic_target` lookups subtract it.

## Dependencies

Python 3.11+. `pip install -r requirements.txt` (anthropic is only needed for the
claude backend; playwright is used for CDP navigation/DOM evidence only — no browser
download needed). `test_parser.py` runs offline with only httpx installed.

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

- The committed example (`examples/hn-top-story/`) is **observation-only**: navigate →
  one model call → `finished()`. No click/type/scroll steps, so `semantic_target` and
  `findings` are null and Ghost has nothing to compile from it yet. An interaction-step
  example is the next deliverable. Its `report.json` was hand-edited after the run to
  point evidence paths at the copies shipped beside it.
- The claude backend loop is written but untested (needs `ANTHROPIC_API_KEY`); its
  metrics fields are not populated yet.
- `type` uses trailing `\n` to submit; canvas-only pages depend entirely on the VLM's
  visual grounding; no auth flows (out of MVP scope).
- Q4 grounding error is roughly 10-40px on sparse synthetic images (better on real
  pages); the F16 model is the upgrade path if precision limits real tasks.
- Report shape is provisional (`0.1-provisional`), to be settled at INT-1.
