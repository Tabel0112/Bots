#!/usr/bin/env bash
# Start llama-server, or reuse one already listening. Deliberately does NOT kill the
# server on exit: a 72B takes minutes to load and is worth keeping up between runs.
#
#   bash serve.sh              # background, returns once healthy
#   bash serve.sh --wait       # stay in the foreground (for an sbatch job)
#   bash serve.sh --stop
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=env.sh
source ./env.sh

SPEC="$(vlm_model_spec "$VLM_MODEL" || true)"
[ -n "$SPEC" ] || { echo "unknown VLM_MODEL='$VLM_MODEL'" >&2; exit 1; }
read -r _repo W MM <<<"$SPEC"
LOG="$VLM_WORK/server-$VLM_MODEL.log"

if [ "${1:-}" = "--stop" ]; then
  pkill -f "llama-server.*$W" && echo "stopped $VLM_MODEL" || echo "nothing to stop"
  exit 0
fi

if vlm_server_up; then
  echo "reusing the server already on :$VLM_PORT"
  exit 0
fi

[ -x "$VLM_SERVER_BIN" ] || { echo "llama-server missing -- run setup.sh first" >&2; exit 1; }
for f in "$W" "$MM"; do
  [ -f "$VLM_MODELS/$f" ] || { echo "missing weights $VLM_MODELS/$f -- run setup.sh first" >&2; exit 1; }
done

vlm_load_modules
echo "== starting $VLM_MODEL on :$VLM_PORT (log: $LOG) =="
if [ "${1:-}" = "--wait" ]; then
  exec "$VLM_SERVER_BIN" -m "$VLM_MODELS/$W" --mmproj "$VLM_MODELS/$MM" \
    -ngl "$VLM_NGL" -c "$VLM_CTX" --port "$VLM_PORT" --host 127.0.0.1
fi

"$VLM_SERVER_BIN" -m "$VLM_MODELS/$W" --mmproj "$VLM_MODELS/$MM" \
  -ngl "$VLM_NGL" -c "$VLM_CTX" --port "$VLM_PORT" --host 127.0.0.1 > "$LOG" 2>&1 &
SERVER=$!

echo "== loading (a 72B takes several minutes) =="
until vlm_server_up; do
  kill -0 $SERVER 2>/dev/null || { echo "server died:"; tail -30 "$LOG"; exit 1; }
  sleep 5
done
echo "ready on http://127.0.0.1:$VLM_PORT/v1  (pid $SERVER)"
echo "reach it from a laptop with:  ssh -N -L $VLM_PORT:localhost:$VLM_PORT $VLM_USER@$(hostname)"
