"""Offline API tests for bounded Ghost qualification jobs."""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.api.service import RunService
from argus.store import JsonStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
PLAN = json.loads((EXAMPLES / "plan.json").read_text(encoding="utf-8"))

INPUTS = [
    {"query": "keyboard", "max_price": 100, "max_results": 5, "currency": "USD"},
    {"query": "speakers", "max_price": 150, "max_results": 5, "currency": "USD"},
    {"query": "unobtainium", "max_price": 150, "max_results": 5, "currency": "USD"},
]


class FakeClient:
    def __init__(self, url):
        self.url = url
        self.closed = False

    async def close(self):
        self.closed = True


class FakeWorkflow:
    def __init__(self, worker, client):
        self.worker = worker
        self.client = client

    async def qualify(self, raw, skill_id, version, inputs):
        return {
            "qualification": {
                "skill_id": skill_id,
                "version": version,
                "status": "qualified",
            },
            "runs": [
                {
                    "outcome": "succeeded",
                    "records": [{"title": item["query"]}],
                    "metrics": {"model_call_count": 0},
                }
                for item in inputs
            ],
        }


class FailingWorkflow(FakeWorkflow):
    async def qualify(self, raw, skill_id, version, inputs):
        raise RuntimeError("provider detail")


class Response:
    status = 200

    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit):
        return json.dumps(self.payload).encode()


class QualificationApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = directory.name
        store = JsonStore(self.root)
        snapshot = {
            "run_id": "run-explore",
            "status": "succeeded",
            "interpreted": {"request_id": "request-explore"},
            "plan": PLAN,
        }
        store.create_run("run-explore", "request-explore", snapshot)
        controller = SimpleNamespace(store=store, cancel=lambda _run_id: None)
        service = RunService(self.root, controller=controller, runtime="connected")
        self.app = create_app(self.root, service=service)
        self.app.state.qualification_sites_loader = self.sites
        self.app.state.qualification_settings_factory = lambda: object()
        self.app.state.qualification_worker_factory = lambda **kwargs: SimpleNamespace(
            **kwargs
        )
        self.app.state.qualification_client_factory = FakeClient
        self.app.state.qualification_workflow_factory = FakeWorkflow
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.env = mock.patch.dict(
            os.environ, {"GHOST_API_URL": "http://ghost.invalid"}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    @staticmethod
    def sites():
        from Agents.browser_worker.config import load_sites

        return load_sites()

    def wait(self, job_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            result = self.client.get(f"/api/workflows/qualifications/{job_id}").json()
            if result["status"] != "running":
                return result
            time.sleep(0.01)
        self.fail("qualification job did not finish")

    def test_accepted_job_succeeds_with_summaries(self):
        response = self.client.post(
            "/api/workflows/demo-catalog.search_extract/versions/1/qualifications",
            json={"run_id": "run-explore", "inputs": INPUTS},
        )
        self.assertEqual(response.status_code, 202)
        result = self.wait(response.json()["job_id"])
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["result"]["status"], "qualified")
        self.assertEqual(len(result["runs"]), 3)
        self.assertEqual(result["runs"][0]["records"], 1)
        self.assertEqual(result["runs"][0]["metrics"]["model_call_count"], 0)

    def test_failing_workflow_exposes_only_class_name(self):
        self.app.state.qualification_workflow_factory = FailingWorkflow
        response = self.client.post(
            "/api/workflows/demo-catalog.search_extract/versions/1/qualifications",
            json={"run_id": "run-explore", "inputs": INPUTS},
        )
        result = self.wait(response.json()["job_id"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], "RuntimeError")
        self.assertNotIn("provider detail", json.dumps(result))

    def test_requires_exactly_three_distinct_changed_inputs(self):
        for inputs in (INPUTS[:2], [INPUTS[0], INPUTS[0], INPUTS[2]]):
            with self.subTest(inputs=inputs):
                response = self.client.post(
                    "/api/workflows/s/versions/1/qualifications",
                    json={"run_id": "run-explore", "inputs": inputs},
                )
                self.assertEqual(response.status_code, 422)
        exploration = PLAN["subtasks"][0]["parameters"]
        response = self.client.post(
            "/api/workflows/s/versions/1/qualifications",
            json={"run_id": "run-explore", "inputs": [exploration, *INPUTS[:2]]},
        )
        self.assertEqual(response.status_code, 422)

    def test_missing_run_is_404(self):
        response = self.client.post(
            "/api/workflows/s/versions/1/qualifications",
            json={"run_id": "run-missing", "inputs": INPUTS},
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_ghost_url_does_not_start_job(self):
        with mock.patch.dict(os.environ, {"GHOST_API_URL": ""}):
            response = self.client.post(
                "/api/workflows/s/versions/1/qualifications",
                json={"run_id": "run-explore", "inputs": INPUTS},
            )
        self.assertEqual(response.status_code, 422)

    def test_workflow_registry_proxies_urlopen(self):
        workflows = [
            {"skill_id": "s", "version": 1, "status": "candidate"},
            {"skill_id": "s", "version": 2, "status": "qualified"},
        ]
        with mock.patch(
            "argus.api.qualification.urllib.request.urlopen",
            return_value=Response(workflows),
        ) as opened:
            response = self.client.get("/api/workflows")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), workflows)
        opened.assert_called_once_with("http://ghost.invalid/v1/workflows", timeout=5.0)

    def test_workflow_registry_maps_http_errors_to_502(self):
        with mock.patch(
            "argus.api.qualification.urllib.request.urlopen",
            side_effect=OSError("provider detail"),
        ):
            response = self.client.get("/api/workflows")
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("provider detail", response.text)


if __name__ == "__main__":
    unittest.main()
