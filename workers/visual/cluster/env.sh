#!/usr/bin/env bash
# Shared configuration and environment detection for the VLM cluster package.
# Sourced by the other scripts; safe to source repeatedly.
#
# Everything here is DETECTED rather than hardcoded, so the package works on Nibi,
# Trillium or a plain workstation without edits. Override any value by exporting it
# before sourcing (e.g. VLM_MODEL=holo-72b, VLM_PORT=8090).

# --- model catalogue -------------------------------------------------------------
# The "-i1-" GGUF repos ship no mmproj and therefore cannot do vision. Do not add them.
vlm_model_spec() {
  case "${1:-}" in
    uitars-72b) echo "mradermacher/UI-TARS-72B-DPO-GGUF UI-TARS-72B-DPO.Q4_K_M.gguf UI-TARS-72B-DPO.mmproj-fp16.gguf" ;;
    uitars-7b)  echo "mradermacher/UI-TARS-1.5-7B-GGUF UI-TARS-1.5-7B.Q4_K_M.gguf UI-TARS-1.5-7B.mmproj-f16.gguf" ;;
    holo-72b)   echo "mradermacher/Holo1.5-72B-GGUF Holo1.5-72B.Q4_K_M.gguf Holo1.5-72B.mmproj-f16.gguf" ;;
    holo-7b)    echo "mradermacher/Holo1.5-7B-GGUF Holo1.5-7B.Q4_K_M.gguf Holo1.5-7B.mmproj-f16.gguf" ;;
    *) return 1 ;;
  esac
}

VLM_MODEL="${VLM_MODEL:-uitars-72b}"     # the measured driver; see workers/visual/README.md
VLM_PORT="${VLM_PORT:-8080}"
VLM_NGL="${VLM_NGL:-99}"                 # offload all layers; llama.cpp splits across GPUs
VLM_CTX="${VLM_CTX:-8192}"

# --- where to work ---------------------------------------------------------------
# $USER is not guaranteed (unset under sbatch on some sites, and in Git Bash), and the
# other scripts run with `set -u`.
VLM_USER="${USER:-${USERNAME:-$(id -un 2>/dev/null || echo vlm)}}"

# Prefer $SCRATCH, but only if it is actually writable from this node: SciNet Trillium
# mounts $HOME read-only on compute nodes, and some sites do the reverse.
vlm_pick_workdir() {
  local c
  for c in "${VLM_WORK:-}" "${SCRATCH:-}/vlm" "${HOME:-}/vlm" "/tmp/vlm-$VLM_USER"; do
    [ -n "$c" ] && [ "$c" != "/vlm" ] || continue
    if mkdir -p "$c" 2>/dev/null && [ -w "$c" ]; then echo "$c"; return 0; fi
  done
  return 1
}
VLM_WORK="$(vlm_pick_workdir)" || { echo "no writable work directory found" >&2; return 1 2>/dev/null || exit 1; }
VLM_MODELS="$VLM_WORK/models"
VLM_LLAMA="${VLM_LLAMA:-$VLM_WORK/llama.cpp}"
VLM_SERVER_BIN="${VLM_SERVER_BIN:-$VLM_LLAMA/build/bin/llama-server}"

# --- helpers ---------------------------------------------------------------------
vlm_have()      { command -v "$1" >/dev/null 2>&1; }
vlm_gpu_count() { nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l; }
vlm_gpu_total_mib() {
  nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits 2>/dev/null \
    | awk '{s+=$1} END {print s+0}'
}
vlm_server_up() { curl -sf -m 3 "http://127.0.0.1:${VLM_PORT}/health" 2>/dev/null | grep -q '"status"'; }

# Load modules if this looks like an Alliance/Lmod site. Never fatal: a workstation or
# a container has no modules and needs none.
vlm_load_modules() {
  vlm_have module || return 0
  module load StdEnv/2023 2>/dev/null || true
  module load gcc cuda cmake python 2>/dev/null || module load cuda cmake python 2>/dev/null || true
}

vlm_python() { vlm_have python3 && echo python3 || echo python; }
