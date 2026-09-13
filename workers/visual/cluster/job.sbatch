#!/usr/bin/env bash
#SBATCH --job-name=vlm-bench
#SBATCH --gpus-per-node=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=64G
#SBATCH --time=02:00:00
#SBATCH --output=vlm-bench-%j.out
#
# Unattended setup + serve + benchmark, for when you do not want to babysit an
# interactive allocation.
#
#   sbatch --account=def-YOURPI job.sbatch
#   VLM_MODEL=holo-72b sbatch --account=def-YOURPI --export=ALL,VLM_MODEL job.sbatch
#
# Adjust --gpus-per-node to fit the model: a 72B Q4_K_M needs ~60GB of VRAM, so one
# H100 80GB is enough, while smaller cards need several (llama.cpp splits automatically).
# An account is mandatory on Alliance clusters.
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
