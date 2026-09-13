#!/usr/bin/env bash
# Report what this node can actually do, before anything expensive is attempted.
# Read-only: downloads nothing, builds nothing, starts nothing.
#
#   bash doctor.sh
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=env.sh
source ./env.sh

ok()   { printf '  ok    %s\n' "$*"; }
warn() { printf '  WARN  %s\n' "$*"; }
bad()  { printf '  FAIL  %s\n' "$*"; FAILED=1; }
FAILED=0

echo "== host =="
echo "  $(hostname)  user=$VLM_USER  ${SLURM_JOB_ID:+slurm_job=$SLURM_JOB_ID}"

echo "== filesystem =="
ok "work dir: $VLM_WORK ($(df -h "$VLM_WORK" 2>/dev/null | awk 'NR==2{print $4}') free)"
[ -w "${HOME}" ] && ok "\$HOME writable" || warn "\$HOME read-only from this node (normal on SciNet Trillium)"
if [ -n "${SCRATCH:-}" ]; then
  [ -w "$SCRATCH" ] && ok "\$SCRATCH writable: $SCRATCH" || warn "\$SCRATCH set but not writable: $SCRATCH"
else
  warn "\$SCRATCH unset; using $VLM_WORK"
fi

echo "== gpu =="
if vlm_have nvidia-smi; then
  nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader | sed 's/^/  /'
  N=$(vlm_gpu_count); TOT=$(vlm_gpu_total_mib)
  ok "$N GPU(s), ${TOT} MiB total"
  case "$VLM_MODEL" in
    *72b) NEED=61000 ;;
    *)    NEED=8000  ;;
  esac
  if [ "$TOT" -ge "$NEED" ]; then ok "enough VRAM for $VLM_MODEL (needs ~${NEED} MiB)"
  else bad "$VLM_MODEL needs ~${NEED} MiB, node has ${TOT} MiB"; fi
  [ -n "${CUDA_VISIBLE_DEVICES:-}" ] && ok "allocation: CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES" \
    || warn "CUDA_VISIBLE_DEVICES unset; nvidia-smi may show GPUs this job cannot use"
else
  bad "no nvidia-smi: not a GPU node"
fi

echo "== internet (compute nodes differ by site) =="
if curl -sf -m 10 -o /dev/null https://huggingface.co; then
  ok "huggingface.co reachable -- models can be fetched from this node"
else
  warn "huggingface.co unreachable -- run setup.sh on a login/data-transfer node instead"
fi

echo "== toolchain =="
vlm_load_modules
for t in git cmake curl; do vlm_have "$t" && ok "$t $(command -v "$t")" || bad "$t missing"; done
vlm_have nvcc && ok "nvcc $(nvcc --version 2>/dev/null | tail -1 | tr -s ' ')" || warn "nvcc missing (module load cuda?)"
P=$(vlm_python); vlm_have "$P" && ok "$P $($P --version 2>&1)" || bad "python missing"
$P -c 'import PIL, httpx' 2>/dev/null && ok "python: pillow + httpx present" || warn "python: pillow/httpx missing (setup.sh installs them)"
vlm_have hf && ok "hf CLI present" || warn "hf CLI missing (setup.sh installs huggingface_hub)"

echo "== artefacts =="
[ -x "$VLM_SERVER_BIN" ] && ok "llama-server built: $VLM_SERVER_BIN" || warn "llama-server not built yet"
SPEC="$(vlm_model_spec "$VLM_MODEL" || true)"
[ -n "$SPEC" ] || { echo "unknown VLM_MODEL='$VLM_MODEL'" >&2; exit 1; }
read -r _repo W MM <<<"$SPEC"
for f in "$W" "$MM"; do
  if [ -f "$VLM_MODELS/$f" ]; then ok "have $(du -h "$VLM_MODELS/$f" | cut -f1) $f"
  else warn "missing $f"; fi
done
vlm_server_up && ok "a server is already answering on :$VLM_PORT" || warn "nothing serving on :$VLM_PORT"

echo
[ "$FAILED" -eq 0 ] && echo "doctor: no blocking problems -- next: bash setup.sh" \
                    || { echo "doctor: blocking problems above"; exit 1; }
