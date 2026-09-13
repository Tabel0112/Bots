#!/usr/bin/env bash
# Build llama.cpp and fetch model weights. Idempotent: re-running skips finished steps.
#
#   bash setup.sh                    # default model (uitars-72b)
#   VLM_MODEL=holo-72b bash setup.sh
#
# On a cluster whose compute nodes have internet (e.g. Nibi) this runs anywhere. Where
# they do not (e.g. SciNet Trillium), run it on a login node first -- the work dir is
# shared, so the compute node then finds everything already in place.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=env.sh
source ./env.sh

SPEC="$(vlm_model_spec "$VLM_MODEL" || true)"
[ -n "$SPEC" ] || { echo "unknown VLM_MODEL='$VLM_MODEL' (uitars-72b|uitars-7b|holo-72b|holo-7b)" >&2; exit 1; }
read -r REPO W MM <<<"$SPEC"

echo "== setup: $VLM_MODEL into $VLM_WORK =="
vlm_load_modules
mkdir -p "$VLM_MODELS"

# --- python deps (user site; no venv needed for three pure-python packages) --------
P=$(vlm_python)
if ! $P -c 'import PIL, httpx, huggingface_hub' 2>/dev/null; then
  echo "== installing python deps =="
  $P -m pip install --quiet --user huggingface_hub pillow httpx
fi

# --- llama.cpp ---------------------------------------------------------------------
if [ -x "$VLM_SERVER_BIN" ]; then
  echo "== llama-server already built =="
else
  echo "== building llama.cpp (CUDA) =="
  [ -d "$VLM_LLAMA/.git" ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp "$VLM_LLAMA"
  cmake -S "$VLM_LLAMA" -B "$VLM_LLAMA/build" -DGGML_CUDA=ON -DLLAMA_CURL=OFF
  cmake --build "$VLM_LLAMA/build" --config Release -j"$(nproc)" --target llama-server
  [ -x "$VLM_SERVER_BIN" ] || { echo "build finished but $VLM_SERVER_BIN is missing" >&2; exit 1; }
fi

# --- weights ------------------------------------------------------------------------
# hf download resumes, so an interrupted transfer is safe to re-run.
for f in "$W" "$MM"; do
  if [ -f "$VLM_MODELS/$f" ]; then
    echo "== have $f ($(du -h "$VLM_MODELS/$f" | cut -f1)) =="
  else
    echo "== downloading $f =="
    if vlm_have hf; then hf download "$REPO" "$f" --local-dir "$VLM_MODELS"
    else $P -m huggingface_hub.commands.huggingface_cli download "$REPO" "$f" --local-dir "$VLM_MODELS"
    fi
  fi
done

cat <<EOF

setup complete
  work dir : $VLM_WORK
  server   : $VLM_SERVER_BIN
  weights  : $VLM_MODELS/$W
             $VLM_MODELS/$MM

next: bash serve.sh        (start the model)
      bash bench.sh        (measure it)
EOF
