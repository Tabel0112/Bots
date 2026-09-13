import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.demo_runtime import build_demo_controller, infer_scenario, interpreted_request


class DemoRuntimeTests(unittest.TestCase):
    def test_natural_language_controls_domain_and_parameters(self):
        self.assertEqual(infer_scenario("Show the top 2 remote engineering jobs by salary"), "jobs")
        jobs = interpreted_request(None, "request-jobs-natural", "Show the top 2 remote engineering jobs by salary")
        self.assertEqual(jobs.intents[0].criteria[-1].parameter, 2)
        trip = interpreted_request(None, "request-trip-natural", "Plan a two-day Toronto food and art trip")
        self.assertEqual(trip.intents[0].parameters["days"].value, 2)
        shopping = interpreted_request(None, "request-shop-natural", "Find keyboards under $50")
        self.assertEqual(shopping.intents[0].parameters["max_price"].value, 50)
        with self.assertRaisesRegex(ValueError, "shopping, travel-planning, or job-search"):
            infer_scenario("Summarize my notes")

    def test_all_scenarios_reach_validated_conclusions(self):
        expected = {"shopping": 2, "travel": 6, "jobs": 3}
        for scenario, count in expected.items():
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as root:
                controller = build_demo_controller(root)
                request = interpreted_request(scenario, f"request-{scenario}")
                result = controller.run(request, request.request_id, f"run-{scenario}")
                self.assertEqual(result.status, "succeeded")
                self.assertEqual(result.validation["status"], "passed")
                self.assertEqual(len(result.answer.records), count)
                types = {event["type"] for event in controller.store.events(result.run_id)}
                self.assertIn("match_decided", types)
                self.assertIn("worker_action_recorded", types)
                self.assertIn("report_assessed", types)
                self.assertIn("synthesized", types)

    def test_run_api_persists_snapshot_and_event_log(self):
        with tempfile.TemporaryDirectory() as root:
            with patch.dict("os.environ", {"ARGUS_RUNTIME": "controlled"}), TestClient(create_app(root)) as client:
                created = client.post("/api/runs", json={"scenario": "shopping"})
                self.assertEqual(created.status_code, 202)
                run_id = created.json()["run_id"]
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    run = client.get(f"/api/runs/{run_id}").json()
                    if run["status"] != "running":
                        break
                    time.sleep(0.02)
                self.assertEqual(run["status"], "succeeded")
                log = client.get(f"/api/runs/{run_id}/event-log")
                self.assertEqual(log.status_code, 200)
                self.assertGreater(len(log.json()), 20)


if __name__ == "__main__":
    unittest.main()
