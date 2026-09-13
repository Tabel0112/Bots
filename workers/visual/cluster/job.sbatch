#!/usr/bin/env bash
#SBATCH --job-name=vlm-bench
#SBATCH --gpus-per-node=h100:1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=vlm-bench-%j.out
#
# Unattended setup + serve + benchmark, for when you do not want to babysit an
# interactive allocation.
#
#   sbatch --account=def-csutton-ab job.sbatch
#   VLM_MODEL=holo-72b sbatch --account=def-csutton-ab --export=ALL,VLM_MODEL job.sbatch
#
# Nibi rejects an untyped GPU request ("There is no 4 GPU-type on the system"), hence
# h100:1 rather than 1. One H100 80GB holds a 72B Q4_K_M (~60GB with context); the MIG
# slices (nvidia_h100_80gb_hbm3_*) are 10-40GB and suit the 7B only. An account is
# mandatory on Alliance clusters, and the prefix varies (def-/rrg-/ctb-).
set -euo pipefail
cd "$(dirname "$(scontrol show job "$SLURM_JOB_ID" | awk -F= '/Command=/{print $2}' | awk '{print $1}')" 2>/dev/null || echo .)"

export VLM_MODEL="${VLM_MODEL:-uitars-72b}"

echo "== node: $(hostname), job $SLURM_JOB_ID, model $VLM_MODEL =="
bash doctor.sh || echo "(doctor reported problems; continuing so the log shows where it fails)"
bash setup.sh
bash serve.sh
bash bench.sh
bash serve.sh --stop
echo "== done =="
