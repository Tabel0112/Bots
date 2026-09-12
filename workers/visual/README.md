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
workstation running a larger model).

### Measured: resolution, not model size, is the bottleneck

`eval_reading.py` captures one live Hacker News page, reads the DOM **only to build a
ground-truth set** (the model never sees it), then asks the model to read each story's
points from pixels alone — the canvas/WebGL situation where no DOM extraction exists.

Two 7B models at the same quant, scored on the **identical** captured page:

| Condition | UI-TARS-1.5-7B-Q4 | Holo1.5-7B-Q4 |
| --- | --- | --- |
| Full page, 100% browser zoom | 6/12 (50%) | **10/12 (83%)** |
| Full page, 150% browser zoom | 10/12 (83%) | not run |
| Magnified crop of the story's row | **12/12 (100%)** | **12/12 (100%)** |

Errors are pure small-scale OCR confusions (128→188, 582→482, 486→496, 58→88), not
misunderstanding: the same model reads the same value perfectly once it is bigger. So
exact-value reading on a DOM-less page is a **resolution** problem, and there are two
independent fixes:

- **Browser page-zoom at capture time** — deterministic, needs no cooperation from the
  model, works on canvas. 50% → 83%.
- **Magnified region reads** (`zoom()`) — 100%, but requires either the model to invoke
  it (the 7B does not) or the worker to magnify around a located region.

Reproduce:

```bash
python eval_reading.py --label ui-tars --base-url http://127.0.0.1:8080/v1
python eval_reading.py --label ui-tars-z150 --page-zoom 150 --out-dir runs/eval150
# compare a second model on the identical captured page:
python eval_reading.py --label other --base-url http://127.0.0.1:8081/v1 \
    --reuse runs/eval/page.png
```

(Caveat: with `--page-zoom`, `getBoundingClientRect` returns unzoomed coordinates under
`body.zoom`, so the *magnified* column of a zoomed run crops the wrong rows and its
number is not meaningful. The full-page column is unaffected.)

### Aggregate questions are not a VLM capability at this size

Asked "which story has the HIGHEST number of points?" on the same page (true answer:
"google.com/goto: Google's anti-scraping update", 582):

- UI-TARS answered "Navier-Stokes Announcement, 267 points" — wrong story, wrong number.
- Holo1.5 emitted malformed JSON (`{"website": ..., "path": "/newest/"}`) — it is tuned
  for localization and screen QA, not free-form reasoning.

This is the same failure the moderator harness hit when it reported the wrong
most-commented story. Comparison and aggregation should not be asked of the model in
one glance: read the individual values (reliable when magnified) and compute the
maximum in code.

### Division of labour that the measurements support

- **Locating / acting** — UI-TARS (it has the action space; Holo1.5 has none, so it
  cannot drive a session).
- **Reading exact values** — magnify the region first; either model is then perfect,
  and Holo1.5 is markedly better without magnification (83% vs 50%).
- **Comparing / aggregating** — in Python, never in the model.

### Choosing a model

Two different weaknesses show up in practice, and they respond differently to model
size:

- **Grounding (where to click)** — the 7B Q4 is already good: clicks land within ~10px
  on real pages.
- **Reading small text and self-directing (when to zoom, when to scan)** — the 7B is
  weak: it misreads small numbers (unstable across runs) and does not reliably invoke
  `zoom()` even when instructed to.

Sizes for a fixed memory budget (GGUF, plus ~1.4GB mmproj and context on top):

| Model | Quant | File | Fits 36GB unified? |
| --- | --- | --- | --- |
| UI-TARS-1.5-7B | Q4_K_M | 4.7GB | yes, easily |
| UI-TARS-1.5-7B | F16 | 15.2GB | yes — same weights, so no gain in self-direction |
| UI-TARS-72B-DPO | Q2_K | 29.6GB | borderline; Q2 degrades instruction-following badly |
| UI-TARS-72B-DPO | Q3_K_S | 34.5GB | no — leaves nothing for mmproj/context/KV |
| UI-TARS-72B-DPO | Q4_K_S | 43.9GB | no |

On an Apple-silicon machine also raise the GPU wired limit
(`sudo sysctl iogpu.wired_limit_mb=...`), and expect slow image prefill: a 72B at low
quant may take tens of seconds per step, which multiplies across an agent loop.

Practical reading: F16 of the same 7B is not worth it (identical weights), and 72B does
not comfortably fit 36GB. If 7B self-direction is the blocker, the higher-leverage move
is the `--backend claude` path for accuracy-critical steps rather than a bigger local
model. Untested alternatives worth a try at the same size: `Holo1.5-7B`.

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
  pages).
- **The 7B does not reliably use `zoom()`.** The action exists and works, and the
  prompt instructs the model to zoom before reporting exact values, but in a live
  Hacker News run the model took 8 steps, called `zoom()` zero times, wandered into a
  story page, and still misread the points (197 vs 182 actual; 168 and 162 on earlier
  runs). Exact-value reading on a DOM-less page therefore needs a driver that follows
  the instruction — see "Choosing a model". Where a DOM exists, prefer structural
  extraction over reading pixels.
- Report shape is provisional (`0.1-provisional`), to be settled at INT-1.
