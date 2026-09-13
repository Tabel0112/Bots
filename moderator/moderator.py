"""ARGUS moderator (provisional): dispatches subtasks to browser subagents, monitors
their traces while they run, emits contract-style events, and combines worker reports
into one final user-facing result.

Boundary note: moderator behavior is ARGUS's workstream; this is a runnable harness
for INT-2 wiring, to be adopted/replaced by ARGUS. Event and result shapes follow
docs/hackathon/CONTRACTS.md v0.1 concepts (stages, events, RunResult, metrics).
"""

import json
import os
import subprocess
import sys
import threading
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VISUAL_WORKER_DIR = os.path.join(REPO_ROOT, "workers", "visual")

STALL_AFTER_S = 120
DEFAULT_SUBTASK_TIMEOUT_S = 600
DEFAULT_MODEL = os.environ.get("MODERATOR_MODEL", "gpt-5.6-sol")


def load_env():
    for path in (os.path.join(REPO_ROOT, ".env"), os.path.join(VISUAL_WORKER_DIR, ".env")):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())


class Moderator:
    def __init__(self, spec, out_dir=None):
        self.spec = spec
        self.request_id = spec.get("request_id") or f"request-{int(time.time())}"
        self.out_dir = out_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                               "runs", self.request_id)
        self.timeout_s = spec.get("budgets", {}).get("per_subtask_timeout_s", DEFAULT_SUBTASK_TIMEOUT_S)
        self.max_parallel = spec.get("budgets", {}).get("max_parallel", 1)
        self._seq = 0
        self._lock = threading.Lock()
        self._events_file = None
        self.worker_states = {}

    # ---- events ----------------------------------------------------------

    def emit(self, type_, stage=None, message="", data=None):
        with self._lock:
            self._seq += 1
            event = {
                "schema_version": "0.1",
                "run_id": self.request_id,
                "sequence": self._seq,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "type": type_,
                "stage": stage,
                "message": message,
                "data": data or {},
            }
            self._events_file.write(json.dumps(event, ensure_ascii=False) + "\n")
            self._events_file.flush()
            print(f"[moderator #{self._seq}] {type_}" + (f"/{stage}" if stage else "") +
                  (f": {message}" if message else ""), flush=True)

    # ---- worker execution + live monitoring ------------------------------

    def _run_worker(self, sub):
        sid = sub["subtask_id"]
        log_dir = os.path.join(self.out_dir, sid)
        if os.path.isdir(log_dir):
            import shutil
            shutil.rmtree(log_dir)
        os.makedirs(log_dir, exist_ok=True)
        state = self.worker_states[sid]
        state.update({"log_dir": log_dir, "started_at": time.time()})

        cmd = [sys.executable, "-m", "browser_subagent", sub["subtask"],
               "--request-id", self.request_id, "--subtask-id", sid,
               "--log-dir", log_dir,
               "--max-steps", str(sub.get("max_steps", 25))]
        if sub.get("start_url"):
            cmd += ["--url", sub["start_url"]]
        if sub.get("backend"):
            cmd += ["--backend", sub["backend"]]

        self.emit("stage_changed", "exploring", f"{sid}: dispatched to visual worker",
                  {"subtask_id": sid, "subtask": sub["subtask"]})
        with open(os.path.join(log_dir, "worker_stdout.txt"), "w", encoding="utf-8") as out:
            proc = subprocess.Popen(cmd, cwd=VISUAL_WORKER_DIR, stdout=out,
                                    stderr=subprocess.STDOUT)
            state["proc"] = proc
            monitor = threading.Thread(target=self._monitor_trace, args=(sid,), daemon=True)
            monitor.start()
            try:
                proc.wait(timeout=self.timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                state["timed_out"] = True
                self.emit("run_failed", "failed", f"{sid}: killed after {self.timeout_s}s budget",
                          {"subtask_id": sid, "code": "BUDGET_EXCEEDED"})
            state["exit_code"] = proc.returncode
            state["done"] = True
            monitor.join(timeout=5)

        report_path = os.path.join(log_dir, "report.json")
        if os.path.exists(report_path):
            with open(report_path, "r", encoding="utf-8") as f:
                state["report"] = json.load(f)
        if not state.get("timed_out"):
            outcome = (state.get("report") or {}).get("outcome", "failed")
            self.emit("run_completed" if outcome == "succeeded" else "run_failed",
                      "completed" if outcome == "succeeded" else "failed",
                      f"{sid}: worker finished ({outcome})",
                      {"subtask_id": sid, "exit_code": state["exit_code"]})

    def _monitor_trace(self, sid):
        state = self.worker_states[sid]
        trace_path = os.path.join(state["log_dir"], "trace.jsonl")
        pos = 0
        last_activity = time.time()
        stalled = False
        while not state.get("done"):
            time.sleep(2)
            if not os.path.exists(trace_path):
                continue
            with open(trace_path, "r", encoding="utf-8") as f:
                f.seek(pos)
                new = f.read()
                pos = f.tell()
            for line in filter(None, (l.strip() for l in new.splitlines())):
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                last_activity = time.time()
                stalled = False
                if rec.get("type") == "session_start":
                    self.emit("action_observed", "exploring",
                              f"{sid}: browser session live",
                              {"subtask_id": sid, "viewer_url": rec.get("viewer_url")})
                elif rec.get("type") == "step":
                    self.emit("action_observed", "exploring",
                              f"{sid}: {rec.get('step_id')} {rec['action'].get('name')} ({rec.get('outcome')})",
                              {"subtask_id": sid, "step_id": rec.get("step_id"),
                               "action": rec.get("action"), "outcome": rec.get("outcome"),
                               "url": rec.get("url"), "error": rec.get("error")})
            if not stalled and time.time() - last_activity > STALL_AFTER_S:
                stalled = True
                self.emit("action_observed", "exploring",
                          f"{sid}: no activity for {STALL_AFTER_S}s (possible stall)",
                          {"subtask_id": sid, "warning": "stall"})

    # ---- aggregation + synthesis -----------------------------------------

    def _aggregate(self):
        subtasks = []
        metrics = {"elapsed_ms": 0, "browser_action_count": 0, "model_call_count": 0}
        failures = []
        for sub in self.spec["subtasks"]:
            sid = sub["subtask_id"]
            state = self.worker_states[sid]
            report = state.get("report")
            if report is None:
                outcome = "failed"
                summary = ("budget exceeded" if state.get("timed_out")
                           else f"no worker report (exit code {state.get('exit_code')})")
                failures.append({"subtask_id": sid, "reason": summary})
            else:
                outcome = report["outcome"]
                summary = report["summary"]
                failures.extend({"subtask_id": sid, **f} for f in report.get("failures", []))
                for k in metrics:
                    metrics[k] += (report.get("metrics") or {}).get(k) or 0
            subtasks.append({
                "subtask_id": sid,
                "subtask": sub["subtask"],
                "outcome": outcome,
                "summary": summary,
                "findings": (report or {}).get("findings"),
                "evidence": (report or {}).get("evidence"),
            })
        workers_ok = all(s["outcome"] == "succeeded" for s in subtasks)
        # Worker completion is NOT task validation (CONTRACTS v0.1). Until a validator
        # is wired (Ghost validate / a site truth set), findings stay unverified and the
        # run must not be presented as validated success.
        validation = {
            "status": "inconclusive",
            "validator_id": None,
            "reason": "no independent validator configured; worker-reported values are "
                      "unverified (a VLM can misread values it looks at)",
            "checks": [],
        }
        return {
            "schema_version": "0.1-provisional",
            "run_id": self.request_id,
            "task": self.spec.get("task"),
            "status": "succeeded" if workers_ok else "failed",
            "workers_completed": workers_ok,
            "validation": validation,
            "subtasks": subtasks,
            "failures": failures,
            "metrics": metrics,
        }

    def _synthesize(self, result):
        parts = [f"- {s['subtask_id']} [{s['outcome']}]: {s['summary']}"
                 + (f" | findings: {json.dumps(s['findings'], ensure_ascii=False)}" if s["findings"] else "")
                 for s in result["subtasks"]]
        digest = "\n".join(parts)
        caveat = ("\n\nNote: these values are worker-reported and have not been "
                  "independently validated.") if result["validation"]["status"] != "passed" else ""
        fallback = f"Task: {result['task']}\nOverall: {result['status']}\n{digest}{caveat}"

        if not os.environ.get("OPENAI_API_KEY"):
            return fallback, "deterministic (no OPENAI_API_KEY)"
        try:
            from openai import OpenAI
            client = OpenAI()
            prompt = (
                "You are the moderator of a multi-agent browser automation system. "
                "Subagents executed the subtasks below and reported these outcomes. "
                "Write the final answer for the user: combine the findings, resolve nothing "
                "beyond what the reports support, state failures and gaps honestly, and do "
                "not invent data that no worker reported.\n\n"
                "IMPORTANT: these values are self-reported by vision models reading "
                "screenshots and have NOT been independently validated; such models do "
                "misread numbers. Present them as worker-reported, and end with one short "
                "line stating they are unverified. Never claim they are confirmed.\n\n"
                f"User task: {result['task']}\n\nWorker reports:\n{digest}"
            )
            resp = client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_completion_tokens=800,
            )
            return resp.choices[0].message.content.strip(), DEFAULT_MODEL
        except Exception as e:
            return fallback + f"\n\n(synthesis model unavailable: {e})", f"deterministic (model error)"

    # ---- entry point -------------------------------------------------------

    def run(self):
        os.makedirs(self.out_dir, exist_ok=True)
        self._events_file = open(os.path.join(self.out_dir, "events.jsonl"), "w", encoding="utf-8")
        try:
            self.emit("stage_changed", "queued", f"run {self.request_id} accepted",
                      {"task": self.spec.get("task"),
                       "subtasks": [s["subtask_id"] for s in self.spec["subtasks"]]})
            for sub in self.spec["subtasks"]:
                self.worker_states[sub["subtask_id"]] = {}

            pending = list(self.spec["subtasks"])
            threads = []
            while pending or any(t.is_alive() for t in threads):
                running = sum(t.is_alive() for t in threads)
                while pending and running < self.max_parallel:
                    sub = pending.pop(0)
                    t = threading.Thread(target=self._run_worker, args=(sub,))
                    t.start()
                    threads.append(t)
                    running += 1
                time.sleep(1)

            self.emit("stage_changed", "validating", "all workers finished; combining reports")
            result = self._aggregate()
            final_answer, synthesizer = self._synthesize(result)
            result["final_answer"] = final_answer
            result["synthesized_by"] = synthesizer

            with open(os.path.join(self.out_dir, "final_result.json"), "w", encoding="utf-8") as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            self.emit("run_completed" if result["status"] == "succeeded" else "run_failed",
                      "completed" if result["status"] == "succeeded" else "failed",
                      f"final result written ({result['status']})",
                      {"final_result": os.path.join(self.out_dir, "final_result.json")})
            return result
        finally:
            self._events_file.close()
