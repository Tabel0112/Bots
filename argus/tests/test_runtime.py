"""Connected runtime composition and the Mission Control API around it.

These tests never touch a browser, a model or the Ghost API: the controller is
injected with fakes and a scripted interpreter, and the environment checks run
with ``check_ghost=False`` or with a fake site loader.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.api.service import RunService
from argus.controller import Controller
from argus.fakes import FakeGhost, FakeToolbox, StubModerator
from argus.runtime import (
    connected_environment_problems,
    normalise_runtime,
)
from argus.store import JsonStore
from argus.tests.test_end_to_end import missing_query, single

CLEAN_ENV = {
    name: ""
    for name in (
        "OPENAI_API_KEY",
        "ARGUS_MODEL",
        "GHOST_API_URL",
        "STEEL_API_KEY",
        "WORKER_BROWSER",
        "WORKER_BROWSER_EXECUTABLE",
        "ARGUS_RUNTIME",
    )
}


def _fake_controller(root, interpret):
    return Controller(
        FakeToolbox(),
        StubModerator(),
        FakeGhost(),
        JsonStore(root),
        interpret=interpret,
    )


def _wait(client, run_id, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.02)
    raise AssertionError(f"{run_id} still running")


class RuntimeModeTests(unittest.TestCase):
    def test_normalise_runtime(self):
        self.assertEqual(normalise_runtime(None), "connected")
        self.assertEqual(normalise_runtime("CONTROLLED"), "controlled")
        self.assertEqual(normalise_runtime("live"), "scrape")
        with self.assertRaisesRegex(ValueError, "ARGUS_RUNTIME"):
            normalise_runtime("fixture")

    def test_environment_problems_name_every_missing_piece(self):
        with patch.dict(os.environ, CLEAN_ENV, clear=False):
            problems = connected_environment_problems(
                check_ghost=False, sites_loader=dict
            )
        joined = "\n".join(problems)
        for name in ("OPENAI_API_KEY", "ARGUS_MODEL", "GHOST_API_URL", "STEEL_API_KEY"):
            self.assertIn(name, joined)
        self.assertIn("no configured worker sites", joined)

    def test_environment_ok_for_local_browser(self):
        env = dict(
            CLEAN_ENV,
            OPENAI_API_KEY="k",
            ARGUS_MODEL="m",
            GHOST_API_URL="http://127.0.0.1:8766",
            WORKER_BROWSER="local",
            WORKER_BROWSER_EXECUTABLE="/usr/bin/true",
        )
        with patch.dict(os.environ, env, clear=False):
            problems = connected_environment_problems(
                check_ghost=False, sites_loader=lambda: {"demo-catalog": object()}
            )
        self.assertEqual(problems, [])

    def test_environment_reports_site_loader_failure(self):
        def broken():
            raise FileNotFoundError("sites.json")

        with patch.dict(os.environ, CLEAN_ENV, clear=False):
            problems = connected_environment_problems(
                check_ghost=False, sites_loader=broken
            )
        self.assertTrue(any("FileNotFoundError" in p for p in problems))

    def test_connected_service_refuses_to_start_without_environment(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.dict(os.environ, CLEAN_ENV, clear=False),
            self.assertRaisesRegex(RuntimeError, "cannot start"),
        ):
            RunService(root, runtime="connected")


class ConnectedApiTests(unittest.TestCase):
    def _client(self, root, interpret):
        service = RunService(
            root, controller=_fake_controller(root, interpret), runtime="connected"
        )
        return TestClient(create_app(root, service=service))

    def test_text_request_runs_through_the_controller(self):
        with (
            tempfile.TemporaryDirectory() as root,
            self._client(root, lambda text, rid: single(rid)) as client,
        ):
            health = client.get("/api/health").json()
            self.assertEqual(health["runtime"], "connected")
            self.assertEqual(health["problems"], [])
            self.assertEqual(health["scenarios"], [])
            created = client.post(
                "/api/runs", json={"text": "Find headphones under $150"}
            )
            self.assertEqual(created.status_code, 202)
            self.assertEqual(created.json()["scenario"], "connected")
            run = _wait(client, created.json()["run_id"])
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["runtime"], "connected")
            self.assertEqual(run["evidence_files"], [])
            listed = client.get("/api/runs").json()
            self.assertEqual(listed[0]["runtime"], "connected")
            self.assertEqual(listed[0]["scenario"], "connected")

    def test_connected_requires_text(self):
        with (
            tempfile.TemporaryDirectory() as root,
            self._client(root, lambda text, rid: single(rid)) as client,
        ):
            rejected = client.post("/api/runs", json={"scenario": "shopping"})
            self.assertEqual(rejected.status_code, 422)

    def test_needs_input_then_clarification_starts_a_follow_up(self):
        def interpret(text, rid):
            return single(rid) if "Clarification:" in text else missing_query(rid)

        with (
            tempfile.TemporaryDirectory() as root,
            self._client(root, interpret) as client,
        ):
            created = client.post("/api/runs", json={"text": "Find something"})
            first = _wait(client, created.json()["run_id"])
            self.assertEqual(first["status"], "needs_input")
            self.assertTrue(first["answer"]["lines"])
            empty = client.post(
                f"/api/runs/{first['run_id']}/clarifications", json={"answer": " "}
            )
            self.assertEqual(empty.status_code, 422)
            follow = client.post(
                f"/api/runs/{first['run_id']}/clarifications",
                json={"answer": "headphones"},
            )
            self.assertEqual(follow.status_code, 202)
            self.assertEqual(follow.json()["parent_run_id"], first["run_id"])
            second = _wait(client, follow.json()["run_id"])
            self.assertEqual(second["status"], "succeeded")
            self.assertEqual(second["parent_run_id"], first["run_id"])
            self.assertIn("Clarification: headphones", second["request_text"])
            again = client.post(
                f"/api/runs/{second['run_id']}/clarifications",
                json={"answer": "x"},
            )
            self.assertEqual(again.status_code, 422)
            missing = client.post(
                "/api/runs/run-does-not-exist/clarifications", json={"answer": "x"}
            )
            self.assertEqual(missing.status_code, 404)

    def test_evidence_route_serves_only_registered_run_files(self):
        with (
            tempfile.TemporaryDirectory() as root,
            self._client(root, lambda text, rid: single(rid)) as client,
        ):
            created = client.post("/api/runs", json={"text": "Find headphones"})
            run = _wait(client, created.json()["run_id"])
            store = client.app.state.run_service.store
            evidence_dir = store.evidence_dir(run["run_id"])
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "observation-1.png").write_bytes(b"\x89PNG")
            (store.run_dir(run["run_id"]) / "secret.txt").write_text("no")
            snapshot = client.get(f"/api/runs/{run['run_id']}").json()
            self.assertEqual(snapshot["evidence_files"], ["observation-1.png"])
            ok = client.get(f"/api/runs/{run['run_id']}/evidence/observation-1.png")
            self.assertEqual(ok.status_code, 200)
            self.assertEqual(ok.content, b"\x89PNG")
            for bad in ("missing.png", "..%2Fsecret.txt", "%2e%2e", "secret.txt"):
                response = client.get(f"/api/runs/{run['run_id']}/evidence/{bad}")
                self.assertEqual(response.status_code, 404, bad)
            unknown = client.get("/api/runs/run-nope/evidence/observation-1.png")
            self.assertEqual(unknown.status_code, 404)


if __name__ == "__main__":
    unittest.main()
