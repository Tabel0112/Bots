import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ghostapi.api.app import create_app


def lookup_body(query="headphones"):
    return {
        "schema_version": "0.1",
        "task_id": "task-1",
        "agent_id": "steel-agent-1",
        "site_id": "shop.example",
        "operation": "search_products",
        "parameters": {"query": query},
        "required_outputs": ["title", "url"],
        "allowed_actions": ["navigate", "fill", "click", "extract"],
    }


def candidate_body():
    body = lookup_body()
    body.update({
        "description": "Search products by query",
        "input_schema": {"query": {"type": "string", "required": True}},
        "output_schema_id": "product-list.v1",
        "validator_id": "product-search.v1",
        "preconditions": ["search form is visible"],
        "trace": [
            {
                "step_id": "fill-query",
                "action": "fill",
                "target": {"strategy": "semantic", "role": "textbox", "label": "Search"},
                "value": "headphones",
                "input_parameter": "query",
                "expected_state": {"field_contains": "headphones"},
                "observation_before": "obs-1",
                "observation_after": "obs-2",
                "outcome": "succeeded",
                "timestamp": "2026-09-12T12:00:00Z",
            },
            {
                "step_id": "extract",
                "action": "extract",
                "target": {"strategy": "semantic", "description": "product results"},
                "expected_state": {},
                "observation_before": "obs-2",
                "observation_after": "obs-3",
                "outcome": "succeeded",
                "timestamp": "2026-09-12T12:00:01Z",
            },
        ],
        "result": [{"title": "Studio headphones", "url": "https://shop.example/item/1"}],
        "evidence_refs": ["obs-1", "obs-2", "obs-3"],
    })
    return body


class GhostApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        app = create_app(Path(self.directory.name) / "ghost.sqlite3")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.directory.cleanup()

    def test_health_graph_and_openapi_are_served_together(self):
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        graph = self.client.get("/graph")
        self.assertEqual(graph.status_code, 200)
        self.assertIn("Agent workflow lifecycle", graph.text)
        self.assertIn("/v1/workflows/lookup", self.client.get("/openapi.json").json()["paths"])

    def test_candidate_qualification_lookup_and_run_report(self):
        no_match = self.client.post("/v1/workflows/lookup", json=lookup_body())
        self.assertEqual(no_match.json()["decision"], "explore")

        created = self.client.post("/v1/workflows/candidates", json=candidate_body())
        self.assertEqual(created.status_code, 201)
        identity = created.json()

        still_candidate = self.client.post("/v1/workflows/lookup", json=lookup_body("keyboard"))
        self.assertEqual(still_candidate.json()["decision"], "explore")

        qualification = {
            "schema_version": "0.1",
            "agent_id": "qualification-worker",
            "reports": [
                {"parameters": {"query": "keyboard"}, "validation_status": "passed",
                 "empty_result": False, "evidence_refs": ["qualification-1"]},
                {"parameters": {"query": "mouse"}, "validation_status": "passed",
                 "empty_result": False, "evidence_refs": ["qualification-2"]},
                {"parameters": {"query": "nothing"}, "validation_status": "passed",
                 "empty_result": True, "evidence_refs": ["qualification-3"]},
            ],
        }
        qualified = self.client.post(
            f"/v1/workflows/{identity['skill_id']}/versions/{identity['version']}/qualification",
            json=qualification,
        )
        self.assertEqual(qualified.json()["status"], "qualified")

        reused = self.client.post("/v1/workflows/lookup", json=lookup_body("keyboard")).json()
        self.assertEqual(reused["decision"], "reuse")
        self.assertEqual(reused["workflow"]["bound_steps"][0]["value"], "keyboard")
        self.assertEqual(
            reused["workflow"]["bound_steps"][0]["expected_state"]["field_contains"], "keyboard"
        )

        report = {
            "schema_version": "0.1",
            "task_id": "task-2",
            "agent_id": "steel-agent-1",
            "status": "succeeded",
            "trace": [],
            "result": [{"title": "Keyboard"}],
            "evidence_refs": ["run-observation"],
            "metrics": {"browser_action_count": 3},
        }
        run = self.client.post(
            f"/v1/workflows/{identity['skill_id']}/versions/{identity['version']}/runs", json=report
        )
        self.assertEqual(run.status_code, 201)
        self.assertEqual(run.json()["status"], "succeeded")
        self.assertGreaterEqual(len(self.client.get("/v1/activity").json()), 5)

    def test_rejects_ambiguous_parameter_origin_and_unknown_fields(self):
        ambiguous = candidate_body()
        ambiguous["trace"][0]["value"] = "different"
        response = self.client.post("/v1/workflows/candidates", json=ambiguous)
        self.assertEqual(response.status_code, 422)
        self.assertIn("does not match", response.text)

        request = lookup_body()
        request["unexpected"] = True
        self.assertEqual(self.client.post("/v1/workflows/lookup", json=request).status_code, 422)

    def test_failed_qualification_does_not_promote_candidate(self):
        identity = self.client.post("/v1/workflows/candidates", json=candidate_body()).json()
        reports = [
            {"parameters": {"query": value}, "validation_status": "passed",
             "empty_result": False, "evidence_refs": [f"obs-{value}"]}
            for value in ("one", "two", "three")
        ]
        response = self.client.post(
            f"/v1/workflows/{identity['skill_id']}/versions/{identity['version']}/qualification",
            json={"schema_version": "0.1", "agent_id": "qualifier", "reports": reports},
        )
        self.assertEqual(response.json()["status"], "candidate")


if __name__ == "__main__":
    unittest.main()
