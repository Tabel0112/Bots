#!/usr/bin/env bash
# Does a 72B UI-TARS choose to zoom() where the 7B does not?
#
# Serves a UI-TARS GGUF on a GPU node and runs zoom_selfdirect_bench.py against it.
# Self-contained: generates its own test image, so no browser, no Steel key, no repo.
#
#   bash run_zoom_selfdirect.sh              # UI-TARS-72B (the open question)
#   SIZE=7b bash run_zoom_selfdirect.sh      # UI-TARS-7B baseline, for comparison
#   PORT=8090 SIZE=7b bash run_zoom_selfdirect.sh   # run both side by side
#
# SciNet Trillium note: $HOME is read-only on compute nodes, so everything written at
# runtime goes to $SCRATCH. Download on a login node (compute nodes have no internet).
set -euo pipefail

WORK="${WORK:-${SCRATCH:-$HOME}/vlm-eval}"
SIZE="${SIZE:-72b}"
PORT="${PORT:-8080}"
NGL="${NGL:-99}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

case "$SIZE" in
  72b) REPO=mradermacher/UI-TARS-72B-DPO-GGUF; W=UI-TARS-72B-DPO.Q4_K_M.gguf; MM=UI-TARS-72B-DPO.mmproj-fp16.gguf; LABEL=uitars-72b ;;
  7b)  REPO=mradermacher/UI-TARS-1.5-7B-GGUF;  W=UI-TARS-1.5-7B.Q4_K_M.gguf;  MM=UI-TARS-1.5-7B.mmproj-f16.gguf;  LABEL=uitars-7b  ;;
  *) echo "SIZE must be 72b or 7b"; exit 1 ;;
esac
# The "-i1-" repos ship no mmproj and cannot do vision -- do not substitute them.

mkdir -p "$WORK/models"; cd "$WORK"
nvidia-smi --query-gpu=name,memory.total --format=csv || true

LS="${LLAMA_SERVER:-$HOME/llama.cpp/build/bin/llama-server}"
[ -x "$LS" ] || { echo "llama-server not found at $LS; set LLAMA_SERVER=/path/to/llama-server"; exit 1; }

python3 -m pip install -q --user huggingface_hub pillow httpx 2>/dev/null || true
for f in "$W" "$MM"; do
  if [ ! -f "models/$f" ]; then
    echo "== downloading $f (login node only; compute nodes have no internet) =="
    hf download "$REPO" "$f" --local-dir models
  fi
done

"$LS" -m "models/$W" --mmproj "models/$MM" -ngl "$NGL" -c 8192 \
  --port "$PORT" --host 127.0.0.1 > "$WORK/server-$LABEL.log" 2>&1 &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
echo "== loading (tail -f $WORK/server-$LABEL.log) =="
until curl -sf "http://127.0.0.1:$PORT/health" | grep -q '"status"'; do
  kill -0 $SERVER 2>/dev/null || { echo "server died:"; tail -30 "$WORK/server-$LABEL.log"; exit 1; }
  sleep 5
done

cd "$WORK"
python3 "$HERE/zoom_selfdirect_bench.py" --label "$LABEL" --base-url "http://127.0.0.1:$PORT/v1"

cat <<'NOTE'

Reference: UI-TARS-1.5-7B in a live browser run called zoom() zero times over 8 steps
and misread the value anyway. Reading accuracy is already settled separately -- every
model tested reads a magnified crop near-perfectly, so a low zoom_rate here means the
ceiling is self-direction, not perception.
NOTE
