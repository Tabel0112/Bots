"""Build a worker report from a run's result + trace.jsonl.

Aligned with docs/hackathon/CONTRACTS.md v0.1 concepts (ActionRecord, RunResult,
metrics); provisional shape pending INT-1 agreement.
"""

import json
import os


def _load_trace(trace_path):
    records = []
    with open(trace_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def build_worker_report(result, log_dir, subtask, request_id=None, subtask_id=None):
    records = _load_trace(os.path.join(log_dir, "trace.jsonl"))
    steps = [r for r in records if r.get("type") == "step"]
    network = [r for r in records if r.get("type") == "network"]
    session = next((r for r in records if r.get("type") == "session_start"), {})

    actions = []
    failures = []
    for s in steps:
        action = {
            "step_id": s.get("step_id"),
            "action": s.get("action"),
            "semantic_target": s.get("element"),
            "observation_before": s.get("observation_before"),
            "observation_after": s.get("observation_after"),
            "url": s.get("url"),
            "outcome": s.get("outcome"),
            "timestamp": s.get("ts"),
            "worker_reasoning": s.get("model_text"),
            "ghost_action": s.get("ghost_action"),
        }
        actions.append(action)
        if s.get("error"):
            failures.append({"step_id": s.get("step_id"), "reason": s["error"]})

    return {
        "schema_version": "0.1-provisional",
        "worker": "visual",
        "worker_model": session.get("model"),
        "request_id": request_id,
        "subtask_id": subtask_id,
        "subtask": subtask,
        "outcome": "succeeded" if result.get("success") else "failed",
        "summary": result.get("summary"),
        "findings": result.get("data"),
        "actions": actions,
        "evidence": {
            "session_id": result.get("session_id"),
            "session_replay_url": result.get("viewer_url"),
            "screenshots": sorted({a["observation_after"] for a in actions if a.get("observation_after")}),
            "network_requests": network,
            "trace_file": os.path.join(log_dir, "trace.jsonl"),
        },
        "metrics": result.get("metrics"),
        "failures": failures,
    }


def save_worker_report(result, subtask, request_id=None, subtask_id=None):
    log_dir = result["log_dir"]
    report = build_worker_report(result, log_dir, subtask, request_id, subtask_id)
    path = os.path.join(log_dir, "report.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    return path, report
