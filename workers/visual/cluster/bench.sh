#!/usr/bin/env bash
# Run the reading and self-direction benchmarks against the local server.
# Both generate their own test image, so no browser, Steel key or network is needed.
#
#   bash bench.sh                 # both benchmarks
#   bash bench.sh reading         # exact-value reading only
#   bash bench.sh selfdirect      # does it choose to zoom
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=env.sh
source ./env.sh

WHICH="${1:-both}"
EVAL_DIR="$(cd .. && pwd)/examples/reading-eval"
P=$(vlm_python)
BASE="http://127.0.0.1:$VLM_PORT/v1"

vlm_server_up || { echo "nothing serving on :$VLM_PORT -- run serve.sh first" >&2; exit 1; }
mkdir -p "$VLM_WORK/results"; cd "$VLM_WORK/results"

if [ "$WHICH" = "both" ] || [ "$WHICH" = "reading" ]; then
  echo "== reading accuracy: $VLM_MODEL =="
  $P "$EVAL_DIR/cluster_bench.py" --label "$VLM_MODEL" --base-url "$BASE"
fi

if [ "$WHICH" = "both" ] || [ "$WHICH" = "selfdirect" ]; then
  echo
  echo "== self-direction (does it zoom): $VLM_MODEL =="
  $P "$EVAL_DIR/zoom_selfdirect_bench.py" --label "$VLM_MODEL" --base-url "$BASE" --max-turns 6
fi

cat <<'NOTE'

Reference numbers on image 9d4f489a6dc239c8 (see workers/visual/README.md):
                     reading full/magnified     zoom_rate   overall
  UI-TARS-1.5-7B          -- / --                2/12        7/12
  UI-TARS-72B             -- / --               12/12       10/12
  Holo1.5-7B            10/12 / 11/12            n/a (no action space)
  Holo1.5-72B           11/12 / 12/12            n/a

Compare only runs whose printed image hash matches: a different Pillow can render
different pixels, and cross-hash comparisons have produced wrong conclusions before.
NOTE
echo "results written under $VLM_WORK/results"
