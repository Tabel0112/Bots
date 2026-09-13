"""Thread-safe execution service for Mission Control runs.

Runtime modes (``ARGUS_RUNTIME``):

- ``connected`` (default): the real controller, DOM worker, Ghost and moderator
  (see :mod:`argus.runtime`). Raw request text goes to the ARGUS interpreter.
- ``controlled``: the offline fixture runtime with keyword routing. No website,
  model or Ghost is used; runs are labeled as fixture data.
- ``scrape`` (formerly ``live``): the deprecated three-site scraper in
  :mod:`argus.live_runtime`. Kept only until INT-2 passes on the connected runtime.
"""

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
from argus.runtime import (
    build_connected_controller,
    connected_environment_problems,
    normalise_runtime,
)


class RunService:
    def __init__(
        self, store_root: str | Path, *, controller=None, runtime: str | None = None
    ):
        self.store_root = Path(store_root)
        self.runtime = normalise_runtime(runtime or os.getenv("ARGUS_RUNTIME"))
        self.problems: list[str] = []
        if controller is not None:
            self.controller = controller
        elif self.runtime == "controlled":
            self.controller = build_demo_controller(self.store_root)
        elif self.runtime == "scrape":
            from argus.live_runtime import build_live_controller

            self.controller = build_live_controller(self.store_root)
        else:
            self.problems = connected_environment_problems()
            if self.problems:
                raise RuntimeError(
                    "ARGUS_RUNTIME=connected cannot start: " + "; ".join(self.problems)
                )
            self.controller = build_connected_controller(self.store_root)
        self.threads: dict[str, threading.Thread] = {}
        self.scenarios: dict[str, str] = {}
        self.parents: dict[str, str] = {}
        self.texts: dict[str, str] = {}

    @property
    def store(self):
        return self.controller.store

    # -- submission -----------------------------------------------------------

    def submit(self, scenario: str | None, text: str | None = None) -> dict:
        request_id = f"request-{uuid.uuid4().hex[:10]}"
        run_id = f"run-{uuid.uuid4().hex[:10]}"
        if self.runtime == "connected":
            if not text or not text.strip():
                raise ValueError("Request text is required.")
            payload = text.strip()
            self.scenarios[run_id] = "connected"
            self.texts[run_id] = payload
        else:
            if text and text.strip():
                scenario = infer_scenario(text)
            elif scenario not in DEFAULT_REQUESTS:
                raise ValueError("Request text is required.")
            payload = interpreted_request(
                scenario, request_id, text, live=self.runtime != "controlled"
            )
            self.scenarios[run_id] = scenario

        def execute():
            self.controller.run(payload, request_id, run_id)

        thread = threading.Thread(target=execute, name=run_id, daemon=True)
        self.threads[run_id] = thread
        thread.start()
        deadline = time.monotonic() + 1
        while not self.store._run_path(run_id).exists() and time.monotonic() < deadline:
            time.sleep(0.005)
        return {
            "run_id": run_id,
            "request_id": request_id,
            "scenario": self.scenarios[run_id],
        }

    def clarify(self, run_id: str, answer: str) -> dict:
        """Start a follow-up request for a run that ended ``needs_input``.

        The original text is preserved and the answer is appended explicitly, so
        the interpreter sees both. The original run stays as it was.
        """
        if not answer or not answer.strip():
            raise ValueError("A clarification answer is required.")
        parent = self.get(run_id)
        if parent.get("status") != "needs_input":
            raise ValueError("Only runs that ended needs_input accept clarifications.")
        original = (
            self.texts.get(run_id)
            or (parent.get("interpreted") or {}).get("raw_text")
            or ""
        )
        if not original:
            raise ValueError("The original request text is not available for this run.")
        created = self.submit(None, f"{original}\nClarification: {answer.strip()}")
        self.parents[created["run_id"]] = run_id
        created["parent_run_id"] = run_id
        return created

    # -- reads -----------------------------------------------------------------

    def get(self, run_id: str) -> dict:
        payload = self.store.run(run_id)
        payload["scenario"] = self.scenarios.get(run_id) or self._scenario(payload)
        payload["runtime"] = self._runtime(payload) or self.runtime
        payload["evidence_files"] = self.evidence_files(run_id)
        if run_id in self.parents:
            payload["parent_run_id"] = self.parents[run_id]
        if run_id in self.texts:
            payload["request_text"] = self.texts[run_id]
        return payload

    def events(self, run_id: str) -> list[dict]:
        return self.store.events(run_id)

    def cancel(self, run_id: str) -> None:
        self.controller.cancel(run_id)

    def evidence_files(self, run_id: str) -> list[str]:
        directory = self.store.evidence_dir(run_id)
        if not directory.is_dir():
            return []
        return sorted(path.name for path in directory.iterdir() if path.is_file())

    def evidence_path(self, run_id: str, observation_id: str) -> Path | None:
        """Resolve a stored evidence file inside the run, or None."""
        directory = self.store.evidence_dir(run_id).resolve()
        candidate = (directory / observation_id).resolve()
        if candidate.parent != directory or not candidate.is_file():
            return None
        return candidate

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
        """Runtime recorded in the persisted run.

        The fixture and scraper runtimes stamp ``interpreted.model`` with
        ``argus-demo/...`` or ``argus-live/...``; the connected runtime records the
        real model name, so anything else means connected.
        """
        model = (payload.get("interpreted") or {}).get("model") or ""
        if model.startswith("argus-demo"):
            return "controlled"
        if model.startswith("argus-live"):
            return "scrape"
        if model:
            return "connected"
        return None

    @staticmethod
    def _scenario(payload: dict) -> str | None:
        model = (payload.get("interpreted") or {}).get("model") or ""
        if model.startswith(("argus-demo", "argus-live")):
            return model.rsplit("/", 1)[-1] or None
        return "connected" if model else None
