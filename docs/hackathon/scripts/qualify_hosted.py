"""Qualify a Ghost candidate with the same request shape ARGUS used for exploration.

Usage (repository root, same environment as the connected API):
  python docs/hackathon/scripts/qualify_hosted.py <run dir> <skill_id> <version> <inputs.json>
Three distinct input sets are required, one of them an evidenced empty result.
Replays use worker-owned sessions and zero model calls.
"""
import asyncio, json, os, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[3] / ".env")
from Agents.browser_worker.config import Settings, load_sites
from Agents.browser_worker.ghost import GhostWorkflow
from Agents.browser_worker.runner import Worker
from argus.adapters.worker_contracts import to_subtask_request
from argus.contracts import Budget, Subtask, SubtaskInput
from ghostapi.client import GhostClient

run_dir, skill_id, version, inputs_path = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
run = json.load(open(os.path.join(run_dir, "run.json")))
subtask = Subtask.from_dict(run["plan"]["subtasks"][0])
sites = load_sites()
raw = to_subtask_request(SubtaskInput(run["run_id"], subtask, "w", Budget(30, 120.0), "explore", None, run["run_id"]), sites).model_dump(mode="json")
raw["visual_fallback_available"] = False


async def main():
    async with GhostClient(os.environ.get("GHOST_API_URL", "http://127.0.0.1:8766")) as client:
        result = await GhostWorkflow(Worker(sites=sites, settings=Settings.from_env()), client).qualify(raw, skill_id, version, json.load(open(inputs_path)))
        print("qualification:", result["qualification"])
        print("runs:", [(r["outcome"], len(r["records"]), r["metrics"]["model_calls"], r["metrics"]["elapsed_ms"]) for r in result["runs"]])


asyncio.run(main())
