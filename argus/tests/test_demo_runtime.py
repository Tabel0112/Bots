import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from argus.api.app import create_app
from argus.demo_runtime import (
    build_demo_controller,
    infer_scenario,
    interpreted_request,
)


class DemoRuntimeTests(unittest.TestCase):
    def test_natural_language_controls_domain_and_parameters(self):
        self.assertEqual(
            infer_scenario("Show the top 2 remote engineering jobs by salary"), "jobs"
        )
        jobs = interpreted_request(
            None,
            "request-jobs-natural",
            "Show the top 2 remote engineering jobs by salary",
        )
        self.assertEqual(jobs.intents[0].criteria[-1].parameter, 2)
        trip = interpreted_request(
            None, "request-trip-natural", "Plan a two-day Toronto food and art trip"
        )
        self.assertEqual(trip.intents[0].parameters["days"].value, 2)
        shopping = interpreted_request(
            None, "request-shop-natural", "Find keyboards under $50"
        )
        self.assertEqual(shopping.intents[0].parameters["max_price"].value, 50)
        with self.assertRaisesRegex(ValueError, "will not guess"):
            infer_scenario("Summarize my notes")

    def test_unsupported_requests_are_rejected_not_rerouted(self):
        for text in (
            "give me the best condo in toronto",
            "find the best remote control car",
            "plan a two-day trip to montreal",
            "what is the salary of a plumber",
        ):
            with (
                self.subTest(text=text),
                self.assertRaisesRegex(ValueError, "will not guess"),
            ):
                infer_scenario(text)
        for text, scenario in (
            ("Plan a two-day Toronto food and art trip", "travel"),
            ("Show the top 2 remote engineering jobs by salary", "jobs"),
            ("Find keyboards under $50", "shopping"),
        ):
            self.assertEqual(infer_scenario(text), scenario)

    def test_api_rejects_unsupported_request_with_422(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.dict("os.environ", {"ARGUS_RUNTIME": "controlled"}),
            TestClient(create_app(root)) as client,
        ):
            rejected = client.post(
                "/api/runs", json={"text": "give me the best condo in toronto"}
            )
            self.assertEqual(rejected.status_code, 422)
            self.assertIn("will not guess", rejected.json()["detail"])
            self.assertEqual(client.get("/api/runs").json(), [])
            self.assertEqual(client.get("/api/health").json()["runtime"], "controlled")

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
                types = {
                    event["type"] for event in controller.store.events(result.run_id)
                }
                self.assertIn("match_decided", types)
                self.assertIn("worker_action_recorded", types)
                self.assertIn("report_assessed", types)
                self.assertIn("synthesized", types)

    def test_run_api_persists_snapshot_and_event_log(self):
        with (
            tempfile.TemporaryDirectory() as root,
            patch.dict("os.environ", {"ARGUS_RUNTIME": "controlled"}),
            TestClient(create_app(root)) as client,
        ):
            created = client.post("/api/runs", json={"scenario": "shopping"})
            self.assertEqual(created.status_code, 202)
            run_id = created.json()["run_id"]
            # Windows CI runs the fixture slowly; wait generously and join the
            # run thread so the temporary store is not removed under it.
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                run = client.get(f"/api/runs/{run_id}").json()
                if run["status"] != "running":
                    break
                time.sleep(0.02)
            client.app.state.run_service.threads[run_id].join(timeout=30)
            self.assertEqual(run["status"], "succeeded")
            self.assertEqual(run["runtime"], "controlled")
            self.assertEqual(client.get("/api/runs").json()[0]["runtime"], "controlled")
            log = client.get(f"/api/runs/{run_id}/event-log")
            self.assertEqual(log.status_code, 200)
            self.assertGreater(len(log.json()), 20)


if __name__ == "__main__":
    unittest.main()
