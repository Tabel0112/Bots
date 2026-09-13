"""Thread-safe execution service for Mission Control demo runs."""

from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path

from argus.demo_runtime import (
    DEFAULT_REQUESTS,
    build_demo_controller,
    infer_scenario,
    interpreted_request,
)
from argus.live_runtime import build_live_controller


class RunService:
    def __init__(self, store_root: str | Path):
        self.store_root = Path(store_root)
        runtime = os.getenv("ARGUS_RUNTIME", "live").lower()
        self.runtime = runtime
        self.controller = (
            build_demo_controller(self.store_root)
            if runtime == "controlled"
            else build_live_controller(self.store_root)
        )
        self.threads: dict[str, threading.Thread] = {}
        self.scenarios: dict[str, str] = {}

    @property
    def store(self):
        return self.controller.store

    def submit(self, scenario: str | None, text: str | None = None) -> dict:
        if text and text.strip():
            scenario = infer_scenario(text)
        elif scenario not in DEFAULT_REQUESTS:
            raise ValueError("Request text is required.")
        request_id = f"request-{uuid.uuid4().hex[:10]}"
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        interpreted = interpreted_request(
            scenario, request_id, text, live=self.runtime != "controlled"
        )
        self.scenarios[run_id] = scenario

        def execute():
            self.controller.run(interpreted, request_id, run_id)

        thread = threading.Thread(target=execute, name=run_id, daemon=True)
        self.threads[run_id] = thread
        thread.start()
        deadline = time.monotonic() + 1
        while not self.store._run_path(run_id).exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        return {"run_id": run_id, "request_id": request_id, "scenario": scenario}

    def get(self, run_id: str) -> dict:
        payload = self.store.run(run_id)
        payload["scenario"] = self.scenarios.get(run_id) or self._scenario(payload)
        payload["runtime"] = self._runtime(payload) or self.runtime
        return payload

    def events(self, run_id: str) -> list[dict]:
        return self.store.events(run_id)

    def cancel(self, run_id: str) -> None:
        self.controller.cancel(run_id)

    def list_runs(self) -> list[dict]:
        if not self.store.index_path.exists():
            return []
        items = []
        for run_dir in (
            self.store.runs_dir.iterdir() if self.store.runs_dir.exists() else []
        ):
            try:
                run = self.get(run_dir.name)
            except (OSError, ValueError):
                continue
            items.append(
                {
                    "run_id": run["run_id"],
                    "status": run.get("status"),
                    "scenario": run.get("scenario"),
                    "stage": run.get("stage"),
                    "runtime": run.get("runtime"),
                }
            )
        return sorted(items, key=lambda item: item["run_id"], reverse=True)

    @staticmethod
    def _runtime(payload: dict) -> str | None:
        """Runtime recorded in the persisted run: interpreted.model is argus-demo/... or argus-live/..."""
        model = (payload.get("interpreted") or {}).get("model") or ""
        if model.startswith("argus-demo"):
            return "controlled"
        if model.startswith("argus-live"):
            return "live"
        return None

    @staticmethod
    def _scenario(payload: dict) -> str | None:
        model = (payload.get("interpreted") or {}).get("model") or ""
        return model.rsplit("/", 1)[-1] or None
