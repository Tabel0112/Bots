# VLM cluster package

Sets up a local VLM server on a GPU node and measures it. Built for **Nibi**, whose
compute nodes have internet access, so the whole thing runs in one interactive session
with no login-node staging. It detects its environment rather than hardcoding a site, so
it also works on Trillium (where `$HOME` is read-only on compute nodes and downloads
must happen on a login node) and on a plain workstation.

The model it configures by default, UI-TARS-72B, is the measured driver for the visual
worker — see [`../README.md`](../README.md) for why.

## Quick start on Nibi

```bash
# from a login node
git clone <this repo> ~/Bots && cd ~/Bots/workers/visual/cluster

# grab an interactive GPU node (an account is mandatory on Alliance clusters)
salloc --account=def-YOURPI --gpus-per-node=1 --cpus-per-task=8 --mem=64G --time=2:00:00

bash doctor.sh     # what can this node do? read-only, changes nothing
bash setup.sh      # build llama.cpp + fetch weights (~47GB for a 72B), idempotent
bash serve.sh      # start the model, or reuse one already listening
bash bench.sh      # reading accuracy + self-direction
```

Unattended instead:

```bash
sbatch --account=def-YOURPI job.sbatch
```

## Scripts

| Script | Does |
| --- | --- |
| `env.sh` | Shared config and detection. Sourced by the rest; not run directly. |
| `doctor.sh` | Reports GPUs, VRAM vs the model's needs, writable work dir, internet reachability, toolchain, what is already built or downloaded. Downloads and starts nothing. |
| `setup.sh` | Builds llama.cpp with CUDA and fetches weights. Skips finished steps, and `hf download` resumes, so it is safe to re-run after an interruption. |
| `serve.sh` | Starts `llama-server`, or reuses one already healthy on the port. `--wait` stays in the foreground for batch jobs; `--stop` kills it. |
| `bench.sh` | Runs `cluster_bench.py` (reading) and `zoom_selfdirect_bench.py` (self-direction) from `../examples/reading-eval/`. |
| `job.sbatch` | doctor → setup → serve → bench → stop, unattended. |

## Configuration

Export before running; every value has a working default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `VLM_MODEL` | `uitars-72b` | `uitars-72b`, `uitars-7b`, `holo-72b`, `holo-7b` |
| `VLM_PORT` | `8080` | Server port |
| `VLM_WORK` | `$SCRATCH/vlm`, else `$HOME/vlm`, else `/tmp` | First writable candidate wins |
| `VLM_NGL` | `99` | Layers offloaded to GPU; llama.cpp splits across all visible GPUs |
| `VLM_CTX` | `8192` | Context size |

```bash
VLM_MODEL=holo-72b VLM_PORT=8081 bash setup.sh && VLM_MODEL=holo-72b VLM_PORT=8081 bash serve.sh
```

Running two models at once on different ports is how the worker's split driver/reader
setup is exercised — see "Split driver and reader" in the worker README.

## Using the server from the visual worker

The worker talks to any OpenAI-compatible endpoint, so tunnel the port to wherever the
worker runs:

```bash
ssh -N -L 8080:localhost:8080 user@nibi-gpu-node &
export UITARS_BASE_URL=http://127.0.0.1:8080/v1
python -m browser_subagent "<subtask>" --url <start>
```

## VRAM

A 72B at Q4_K_M is ~47GB of weights plus a 1.4GB projector, so budget ~60GB with
context: one H100 80GB, or several smaller cards. `doctor.sh` checks the total against
the selected model and says so before you spend 47GB of transfer. The 7B models need
~8GB.

## Gotchas this package already handles

- **`-i1-` GGUF repos ship no `mmproj`** and cannot do vision. The catalogue in `env.sh`
  only lists repos that include one.
- **Read-only `$HOME` on some compute nodes** (SciNet Trillium). `env.sh` picks the
  first writable work directory instead of assuming.
- **No internet from compute nodes on some sites.** `doctor.sh` reports reachability;
  where it fails, run `setup.sh` on a login node first — the work directory is shared.
- **`nvidia-smi` shows the whole node, not your allocation.** `doctor.sh` prints
  `CUDA_VISIBLE_DEVICES` so a partial allocation is visible.
- **Servers are left running deliberately.** A 72B takes minutes to load; `serve.sh`
  reuses a healthy one and only `--stop` kills it.
- **CRLF.** `.gitattributes` pins `*.sh` to LF, so a Windows checkout cannot produce
  `set: pipefail: invalid option name` on a Linux node.

## Comparing results

Both benchmarks print a SHA256 of the image they generated. **Only compare runs whose
hash matches** — a different Pillow version renders different pixels, and a cross-hash
comparison has already produced a confidently wrong conclusion once (it made a 72B look
no better than a 7B).
