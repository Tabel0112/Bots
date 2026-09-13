#!/usr/bin/env bash
# Reproduce the small-text reading eval with a 72B model on a GPU node (H100 80GB).
#
# Self-contained: reuses the committed page.png + truth.json, so it needs no Steel key,
# no browser and no network access back to a laptop.
#
#   bash run_72b_eval.sh                      # UI-TARS-72B
#   MODEL=holo bash run_72b_eval.sh           # Holo1.5-72B
#
# Expect ~50GB of downloads and ~10 min of model load.
set -euo pipefail

WORK="${WORK:-$HOME/vlm-eval}"
MODEL="${MODEL:-uitars}"
PORT="${PORT:-8080}"
NGL="${NGL:-99}"

case "$MODEL" in
  uitars) REPO=mradermacher/UI-TARS-72B-DPO-GGUF; W=UI-TARS-72B-DPO.Q4_K_M.gguf;  MM=UI-TARS-72B-DPO.mmproj-fp16.gguf; LABEL=uitars-72b-q4 ;;
  holo)   REPO=mradermacher/Holo1.5-72B-GGUF;     W=Holo1.5-72B.Q4_K_M.gguf;      MM=Holo1.5-72B.mmproj-f16.gguf;      LABEL=holo1.5-72b-q4 ;;
  *) echo "MODEL must be uitars or holo"; exit 1 ;;
esac
# NB: the "-i1-" repos have no mmproj, so they cannot do vision. Use the ones above.

mkdir -p "$WORK/models"; cd "$WORK"
nvidia-smi --query-gpu=name,memory.total --format=csv || true

# --- llama.cpp (CUDA) ---
if [ ! -x "$WORK/llama.cpp/build/bin/llama-server" ]; then
  echo "== building llama.cpp =="
  [ -d llama.cpp ] || git clone --depth 1 https://github.com/ggml-org/llama.cpp
  cmake -S llama.cpp -B llama.cpp/build -DGGML_CUDA=ON -DLLAMA_CURL=OFF >/dev/null
  cmake --build llama.cpp/build --config Release -j"$(nproc)" --target llama-server
fi

# --- weights ---
python3 -m pip install -q --user huggingface_hub httpx pillow
for f in "$W" "$MM"; do
  [ -f "models/$f" ] || python3 -c "
from huggingface_hub import hf_hub_download
import shutil; p = hf_hub_download('$REPO', '$f')
shutil.copy(p, 'models/$f'); print('got', '$f')"
done

# --- serve ---
"$WORK/llama.cpp/build/bin/llama-server" -m "models/$W" --mmproj "models/$MM" \
  -ngl "$NGL" -c 8192 --port "$PORT" --host 127.0.0.1 > server.log 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
echo "== waiting for model load (watch: tail -f $WORK/server.log) =="
until curl -sf "http://127.0.0.1:$PORT/health" | grep -q '"status"'; do
  kill -0 $SERVER 2>/dev/null || { echo "server died:"; tail -30 server.log; exit 1; }
  sleep 5
done

# --- eval on the identical page the 7B models were scored on ---
cd "$(dirname "$(readlink -f "$0")")/../.."      # workers/visual
python3 eval_reading.py --label "$LABEL" --base-url "http://127.0.0.1:$PORT/v1" \
  --reuse examples/reading-eval/page.png --reuse-truth examples/reading-eval/truth.json

echo
echo "7B baselines on this same page:  ui-tars 6/12 full, 12/12 magnified"
echo "                                 holo1.5 10/12 full, 12/12 magnified"
