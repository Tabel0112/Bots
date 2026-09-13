# Visual browser worker (VLM-1)

Vision-driven browser subagent for ARGUS. Receives one subtask, drives a Steel cloud
browser session with a VLM (screenshot → action loop), and returns a worker report with
step-level evidence (screenshots, semantic DOM targets, network requests) that Ghost can
compile into candidate skills.

## DOM-first Ghost connection

The [worker connection guide](../../ghostapi/INTEGRATION.md) is the entry point for
integrated execution. Set `GHOST_API_URL`, install the root package with
`python -m pip install -e '.[test,visual]'`, and run
`python -m Agents.browser_worker.ghost_cli --request <subtask.json>` from the repo root.
The HTML/DOM worker always goes first; this module is invoked when DOM requests visual
help or an appropriate canvas-related failure occurs. It attaches to the retained Steel
session instead of creating another browser. Existing standalone commands below remain
available for visual-only experiments.

`browser_subagent/ghost_adapter.py` adapts the handoff to the existing
`UITarsSubagent.run` loop and retains its screenshots/report. The run method now accepts
an opaque `session_ref`, explicit `close_on_finish`, and optional structured `task/site`
context. An attached session is disconnected without release unless cleanup authority
was transferred. `STEEL_API_KEY` and `UITARS_BASE_URL` are required for the live fallback;
Ghost owns no Steel credentials. Its Python entry point is
`await run_visual_handoff(task, site, dom_report, cancel_event)`.

Integrated actions obey configured read-only controls and domains. Typing replaces the
focused field and records its parameter origin; `select(value='...')` selects a focused
native dropdown. Unsupported drag/hotkey/scroll operations are rejected for this first
connection. Screenshots and model inspection remain evidence, while only stable semantic
steps can enter a Ghost candidate. Independent configured DOM extraction can validate a
visual result. For a canvas-only answer with no such validator, the caller receives its
summary/screenshots with `outcome: inconclusive` and no candidate, even if the model says
`finished`. This prevents an unsupported validation claim.

Tests: `python -m pytest -c tests/browser_worker/pytest.ini tests/browser_worker/test_ghost_visual.py -q`
from the root. They exercise the real UI-TARS loop with simulated browser/model responses,
session detach behavior, input origins and evidence normalization. A live Steel + UI-TARS
connection check is still pending. The existing parser/smoke commands below retain their
original scope. The Claude backend is not used by the new connection.

Two interchangeable VLM backends:

- **`uitars` (default)** — a UI-TARS model on any OpenAI-compatible server (llama.cpp
  `llama-server`). No API cost. **Use UI-TARS-72B on a GPU node**: it decides to magnify
  before reading a value 12/12 of the time against the 7B's 2/12, and is right 83% vs
  58% (see "Self-direction"). The 7B Q4 fits an 8GB GPU and is the laptop fallback, but
  it mostly answers small text from the full page, which is a coin flip.
- **`claude`** — Claude Opus 5 with the `computer_toolset_20260801` computer-use tool.
  Needs `ANTHROPIC_API_KEY`. Written but untested.

## Entry point

```bash
cd Agents/visual
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
- `READER_BASE_URL` — optional second endpoint used only to read exact values off
  magnified crops (see "Split driver and reader"). Unset by default.

## Serving the model

llama.cpp build required either way (from ggml-org/llama.cpp releases; on Windows CUDA,
`llama-bNNNN-bin-win-cuda-12.4-x64.zip` plus the matching `cudart-...zip`, unzipped
together). Weights live outside the repo, any location.

### UI-TARS-72B on a GPU node — the configuration to use

Q4_K_M is ~47GB of weights plus a 1.4GB projector, so it needs roughly 60GB of VRAM
with context; it fits one H100 80GB, and llama.cpp will otherwise split it across
several GPUs. Download on a login node — compute nodes usually have no internet — and
write runtime files to `$SCRATCH`, because SciNet Trillium mounts `$HOME` read-only on
compute nodes:

```bash
hf download mradermacher/UI-TARS-72B-DPO-GGUF UI-TARS-72B-DPO.Q4_K_M.gguf \
    UI-TARS-72B-DPO.mmproj-fp16.gguf --local-dir $SCRATCH/models
llama-server -m $SCRATCH/models/UI-TARS-72B-DPO.Q4_K_M.gguf \
    --mmproj $SCRATCH/models/UI-TARS-72B-DPO.mmproj-fp16.gguf \
    -ngl 99 -c 8192 --port 8080 > $SCRATCH/server.log 2>&1 &
```

Then point the worker at it, over an SSH tunnel if it is not the local machine:

```bash
ssh -N -L 8080:localhost:8080 user@gpu-node &
export UITARS_BASE_URL=http://127.0.0.1:8080/v1
```

The `-i1-` GGUF repos ship no `mmproj` and therefore cannot do vision — do not
substitute them.

### UI-TARS-1.5-7B locally — fallback

Fits an 8GB GPU and is fine for grounding and for developing the harness, but see
"Self-direction" before trusting it to read exact values.

```bash
hf download mradermacher/UI-TARS-1.5-7B-GGUF UI-TARS-1.5-7B.Q4_K_M.gguf \
    UI-TARS-1.5-7B.mmproj-f16.gguf --local-dir <models>
llama-server -m <models>/UI-TARS-1.5-7B.Q4_K_M.gguf --mmproj <models>/UI-TARS-1.5-7B.mmproj-f16.gguf -ngl 99 -c 8192 --port 8080
```

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

### Model size does help, measured on a GPU cluster

`cluster_bench.py` generates its own test image deterministically and prints a SHA256 of
it, so a cluster node and a laptop can prove they scored identical pixels before their
numbers are compared. Run on one Trillium GPU node, image `9d4f489a6dc239c8`, both
Q4_K_M:

| Condition | Holo1.5-**7B** | Holo1.5-**72B** |
| --- | --- | --- |
| Full page | 10/12 (83%) | **11/12 (92%)** |
| Magnified crop | 11/12 (92%) | **12/12 (100%)** |

The 72B wins in both conditions, and only the 72B reaches 100%. The 7B's misses are also
worse in kind — 345→**7** on the full page, and 55→**1** even when magnified — whereas
the 72B's single miss is a near-hit (229→223). So for reading exact values, both axes
matter: magnify *and* use the larger reader where one is available.

Do not compare numbers across different image hashes. An earlier laptop run of the 7B on
a different image scored 11/12 and made the 72B look no better; on identical pixels the
7B scores 10/12.

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

### Split driver and reader (cluster deployment)

The driver and the value-reader do not have to be the same model, and the measurements
say they should not be: UI-TARS has the action space and grounds clicks well, while
Holo1.5-72B is the better reader. Point them at separate endpoints:

```bash
# reader on a cluster GPU node, reached through an SSH tunnel
ssh -N -L 8081:localhost:8081 user@cluster-node &
python -m browser_subagent "<subtask>" --url <start> \
    --base-url http://127.0.0.1:8080/v1 \
    --reader-url http://127.0.0.1:8081/v1
```

`--reader-url` (or `READER_BASE_URL`) is optional. When set, every `zoom()` also goes to
the reader model and its answer is added to the driver's feedback and recorded in the
trace as `reader_answer`. When unset, or if the reader call fails, the driver still sees
the magnified image — the reader is an enhancement, never a dependency.

Serving the 72B reader on a node (Q4_K_M, ~47GB, fits one 80GB GPU or splits across
several; SciNet Trillium mounts `$HOME` read-only on compute nodes, so write to
`$SCRATCH`):

```bash
hf download mradermacher/Holo1.5-72B-GGUF Holo1.5-72B.Q4_K_M.gguf \
    Holo1.5-72B.mmproj-f16.gguf --local-dir $SCRATCH/models
llama-server -m $SCRATCH/models/Holo1.5-72B.Q4_K_M.gguf \
    --mmproj $SCRATCH/models/Holo1.5-72B.mmproj-f16.gguf \
    -ngl 99 -c 8192 --port 8081 > $SCRATCH/server.log 2>&1 &
```

The reader question must be a **targeted question**, not a transcription request.
Holo1.5 answers "…how many points does it have? Answer with the number only" accurately
but hallucinates on "transcribe this image exactly" — the worker derives the question
from the subtask for this reason.

### Self-direction: does the driver choose to zoom?

Reading accuracy and *deciding to magnify* are different abilities. `zoom_selfdirect_bench.py`
measures the second: the agent is shown a full page, asked for an exact value, and run as a
real loop (its `zoom()` returns a magnified crop). Same generated image `9d4f489a6dc239c8`,
UI-TARS Q4_K_M, on one H100:

| | zoom_rate | overall correct | correct when it zoomed |
| --- | --- | --- | --- |
| UI-TARS-1.5-**7B** | 2/12 (17%) | 7/12 (58%) | 2/2 |
| UI-TARS-**72B** | **12/12 (100%)** | **10/12 (83%)** | 10/12 |

Self-direction is the ability that scales. The 7B almost never magnifies — it answers
straight from the full page (a coin flip) or wanders into stray clicks, drags and scrolls.
The 72B magnifies every time and then answers, in two or three turns.

Two ways to exploit that, and the cheap one is worth trying first:

- **Use the 72B as the driver** on DOM-less pages — 83% vs 58%, at ~10x the serving cost.
- **Stop relying on self-direction.** Both models are accurate *once magnified*, and 7B
  grounding is good, so the worker can locate with the 7B and magnify deterministically
  in code rather than hoping the model asks. That keeps the 7B and needs no cluster.

Two measurement traps are worth repeating, because both produced confidently wrong
numbers before they were found. The bench originally stopped after two turns, which
measured impatience rather than capability; and it was stateless, rebuilding one fresh
message per turn, so UI-TARS — which reasons from its own action history — repeated
itself forever (`zoom->wait->zoom->wait`) and scored 1/12 despite zooming every time.
Any harness measuring an agent must carry its history.

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

Three abilities behave differently with size, which is why the 72B is the configured
driver and the 7B is only a fallback:

- **Grounding (where to click)** — the 7B Q4 is already good: clicks land within ~10px
  on real pages. Size buys little here.
- **Reading small text** — magnification matters more than parameters; every model
  tested reads a magnified crop near-perfectly and a full page unreliably. At equal
  pixels the 72B is still ahead (Holo1.5: 11/12 vs 10/12 full page, 12/12 vs 11/12
  magnified).
- **Self-direction (deciding to magnify at all)** — this is where size is decisive, and
  it is what makes the 7B unusable for exact values unaided: zoom_rate 12/12 vs 2/12,
  overall 83% vs 58%.

Sizes to plan VRAM against (GGUF, plus ~1.4GB mmproj and context on top):

| Model | Quant | File | Notes |
| --- | --- | --- | --- |
| UI-TARS-72B-DPO | Q4_K_M | 47.4GB | **the configuration in use**; ~60GB with context, fits one H100 80GB |
| UI-TARS-72B-DPO | Q4_K_S | 43.9GB | fits a 48GB card only without much context |
| UI-TARS-72B-DPO | Q2_K | 29.6GB | fits 36GB, but Q2 degrades the instruction-following that is the whole point |
| UI-TARS-1.5-7B | Q4_K_M | 4.7GB | laptop fallback, 8GB GPU |
| UI-TARS-1.5-7B | F16 | 15.2GB | same weights as the Q4 — no gain in self-direction |

The `-i1-` 72B repos ship no `mmproj` and cannot do vision. On Apple silicon also raise
the GPU wired limit (`sudo sysctl iogpu.wired_limit_mb=...`) and expect slow image
prefill, which multiplies across an agent loop.

If no GPU node is available, the alternative to a bigger model is to stop depending on
self-direction: locate with the 7B and magnify deterministically in code (see "Split
driver and reader"), or use `--backend claude` for accuracy-critical steps.

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
- **The 7B fallback does not reliably use `zoom()`** — 2/12, and in a live Hacker News
  run it took 8 steps, called `zoom()` zero times, wandered into a story page and
  misread the points (197 vs 182 actual; 168 and 162 on earlier runs). This is the
  reason the 72B is the configured driver. Where a DOM exists, prefer structural
  extraction over reading pixels regardless of model.
- **The 72B's measured 83% is on a generated page**, not a live browser session: the
  agent-loop run against a real site has not been repeated with it yet. Its two misses
  were reading errors after a correct zoom, not planning failures.
- Report shape is provisional (`0.1-provisional`), to be settled at INT-1.
