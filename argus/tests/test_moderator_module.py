"""Offline contract and controller tests for the production moderator module."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from functools import partial
from pathlib import Path

from argus import interfaces, planner
from argus.contracts import (
    AnswerSelection,
    Criterion,
    Event,
    InterpretedRequest,
    Plan,
    Subtask,
    TypedError,
    WorkerReport,
)
from argus.controller import Controller
from argus.fakes import (
    FakeGhost,
    FakeModelClient,
    FakePlannerClient,
    FakeToolbox,
)
from argus.model_client import ModelResult
from argus.store import JsonStore
from moderator import Moderator

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REPORT = json.loads((EXAMPLES / "worker_report.json").read_text(encoding="utf-8"))
REGISTRY = json.loads(
    (EXAMPLES / "interpreted_request.json").read_text(encoding="utf-8")
)
SALARY = json.loads(
    (EXAMPLES / "interpreted_request_open_salary.json").read_text(encoding="utf-8")
)
CHAIN = json.loads((EXAMPLES / "plan_open_chain.json").read_text(encoding="utf-8"))


def subtask(subtask_id: str = "subtask-1") -> Subtask:
    return Subtask(
        subtask_id=subtask_id,
        intent_index=0,
        site_id="demo-catalog",
        operation="search_products",
        parameters={"query": "headphones", "max_results": 5},
        concurrency_group=f"group-{subtask_id}",
        output_schema_id="product-list.v1",
        success_conditions=["results present or explicit empty state"],
    )


def report(
    subtask_id: str = "subtask-1",
    *,
    outcome: str = "succeeded",
    records: list[dict] | None = None,
    screenshots: tuple[str, ...] = ("observation-000.png",),
    failures: tuple[TypedError, ...] = (),
) -> WorkerReport:
    payload = copy.deepcopy(REPORT)
    payload.update(
        request_id="request-test",
        subtask_id=subtask_id,
        outcome=outcome,
        findings=records
        if records is not None
        else [
            {
                "title": "Item",
                "url": f"https://example.invalid/{subtask_id}",
                "source_observation_id": screenshots[0]
                if screenshots
                else "observation-missing",
            }
        ],
    )
    payload["evidence"]["screenshots"] = list(screenshots)
    value = WorkerReport.from_dict(payload)
    value.typed_failures = list(failures)
    return value


def plan(*ids: str) -> Plan:
    return Plan("plan-test", "request-test", [subtask(value) for value in ids], "now")


def interpreted(
    criteria: list[Criterion], raw_text: str = "find jobs"
) -> InterpretedRequest:
    payload = copy.deepcopy(SALARY)
    payload["request_id"] = "request-test"
    payload["raw_text"] = raw_text
    value = InterpretedRequest.from_dict(payload)
    value.intents[0].criteria = list(criteria)
    return value


def event(event_type: str, **data) -> Event:
    return Event(
        "run-1", 1, "2026-09-13T00:00:00Z", event_type, "dispatching", "", data
    )


class AssessTests(unittest.TestCase):
    def test_schema_invalid_report_fails(self):
        value = report()
        value.metrics = []
        decision = Moderator().assess_report(subtask(), value, [])
        self.assertEqual(decision.decision, "fail")

        decision = Moderator().assess_report(subtask(), {"outcome": "succeeded"}, [])
        self.assertEqual(decision.decision, "fail")

    def test_failed_report_carries_failure(self):
        failure = TypedError("AUTH_REQUIRED", "login wall", False, "step-1")
        decision = Moderator().assess_report(
            subtask(), report(outcome="failed", records=[], failures=(failure,)), []
        )
        self.assertEqual(decision.decision, "fail")
        self.assertEqual(decision.next_action["failure"], failure.to_dict())

    def test_first_retryable_failure_retries_once(self):
        failure = TypedError("TARGET_AMBIGUOUS", "two targets", True)
        moderator = Moderator()
        value = report(outcome="failed", records=[], failures=(failure,))
        self.assertEqual(
            moderator.assess_report(subtask(), value, []).decision,
            "retry_other_path",
        )
        self.assertEqual(moderator.assess_report(subtask(), value, []).decision, "fail")

    def test_success_without_evidence_verifies(self):
        decision = Moderator().assess_report(
            subtask(), report(records=[], screenshots=()), ["show results"]
        )
        self.assertEqual(decision.decision, "verify")
        self.assertIn("question", decision.next_action)

    def test_no_client_accepts_evidenced_success(self):
        decision = Moderator(None).assess_report(subtask(), report(), [])
        self.assertEqual(decision.decision, "accept")

    def test_model_accept_and_verify(self):
        accept = FakeModelClient(
            ModelResult(
                "ok",
                {
                    "decision": "accept",
                    "reason": "conditions met",
                    "unmet_conditions": [],
                    "verification_question": None,
                },
            )
        )
        accepted = Moderator(accept).assess_report(
            subtask(), report(), ["show results"]
        )
        self.assertEqual(accepted.decision, "accept")
        self.assertEqual(len(accept.calls), 1)

        verify = FakeModelClient(
            ModelResult(
                "ok",
                {
                    "decision": "verify",
                    "reason": "needs a fresh check",
                    "unmet_conditions": ["price visible"],
                    "verification_question": "Is the price visible?",
                },
            )
        )
        checked = Moderator(verify).assess_report(subtask(), report(), [])
        self.assertEqual(checked.decision, "verify")
        self.assertEqual(checked.next_action["question"], "Is the price visible?")

    def test_refusal_and_invalid_output_never_accept(self):
        results = (
            ModelResult("refusal", raw_text="provider secret"),
            ModelResult("invalid", raw_text="provider secret"),
            ModelResult("ok", {"decision": "accept"}),
        )
        for index, result in enumerate(results):
            with self.subTest(index=index):
                decision = Moderator(FakeModelClient(result)).assess_report(
                    subtask(f"subtask-{index}"), report(f"subtask-{index}"), []
                )
                self.assertEqual(decision.decision, "verify")
                self.assertNotIn("provider secret", decision.reason)

    def test_model_error_never_accepts(self):
        client = FakeModelClient([])
        decision = Moderator(client).assess_report(subtask(), report(), [])
        self.assertEqual(decision.decision, "verify")


class ReconcileTests(unittest.TestCase):
    def test_distinct_urls_merge_without_gaps(self):
        first = report("a")
        second = report("b")
        decision = Moderator().reconcile(plan("a", "b"), [first, second])
        action = decision.next_action
        self.assertEqual(decision.decision, "merged")
        self.assertEqual(len(action["findings"]), 2)
        self.assertEqual(action["gaps"], [])
        self.assertEqual(action["conflicts"], [])
        supplied = first.findings + second.findings
        self.assertTrue(all(item in supplied for item in action["findings"]))

    def test_model_tags_shared_url_conflict(self):
        earlier = report(
            "a",
            records=[
                {
                    "title": "Old",
                    "url": "https://example.invalid/shared",
                    "source_observation_id": "observation-000.png",
                }
            ],
        )
        later = report(
            "b",
            records=[
                {
                    "title": "New",
                    "url": "https://example.invalid/shared",
                    "source_observation_id": "observation-000.png",
                }
            ],
        )
        client = FakeModelClient(
            ModelResult(
                "ok",
                {
                    "conflicts": [
                        {"conflict_index": 0, "resolution": "resolve_from_evidence"}
                    ]
                },
            )
        )
        action = (
            Moderator(client).reconcile(plan("a", "b"), [earlier, later]).next_action
        )
        self.assertEqual(action["findings"], later.findings)
        self.assertEqual(action["conflicts"][0]["resolution"], "resolve_from_evidence")
        self.assertEqual(
            action["gaps"][0]["record"], "url=https://example.invalid/shared"
        )

    def test_invalid_conflict_output_becomes_gap(self):
        earlier = report(
            "a",
            records=[
                {
                    "title": "Old",
                    "url": "https://example.invalid/shared",
                    "source_observation_id": "observation-000.png",
                }
            ],
        )
        later = copy.deepcopy(earlier)
        later.subtask_id = "b"
        later.findings[0]["title"] = "New"
        action = (
            Moderator(FakeModelClient(ModelResult("invalid")))
            .reconcile(plan("a", "b"), [earlier, later])
            .next_action
        )
        self.assertEqual(action["conflicts"][0]["resolution"], "report_as_gap")
        self.assertEqual(action["gaps"][0]["resolution"], "report_as_gap")
        self.assertEqual(action["gaps"][0]["conflict"]["fields"], ["title"])


class SynthesisTests(unittest.TestCase):
    def records(self):
        return [
            {
                "title": "A",
                "salary": 100,
                "remote": True,
                "source_observation_id": "o1",
            },
            {
                "title": "B",
                "salary": 300,
                "remote": False,
                "source_observation_id": "o2",
            },
            {
                "title": "C",
                "salary": 200,
                "remote": True,
                "source_observation_id": "o3",
            },
        ]

    def test_filter_rank_limit_preserve_original_indices(self):
        criteria = [
            Criterion("remote", "filter", "remote", None, 1.0),
            Criterion("highest", "rank", "salary", None, 1.0),
            Criterion("one", "limit", 1, None, 1.0),
        ]
        selection = Moderator().synthesize(
            interpreted(criteria), self.records(), {}, [], []
        )
        self.assertIsInstance(selection, AnswerSelection)
        self.assertEqual(selection.record_indices, [2])
        self.assertEqual([claim.record_index for claim in selection.claims], [0])

    def test_invalid_model_output_adds_fallback_note(self):
        selection = Moderator(FakeModelClient(ModelResult("invalid"))).synthesize(
            interpreted([]), self.records(), {}, [], []
        )
        self.assertEqual(selection.record_indices, [0, 1, 2])
        self.assertIn("synthesis_fallback", [note.kind for note in selection.notes])

    def test_model_may_reorder_and_choose_fields_only(self):
        client = FakeModelClient(
            ModelResult(
                "ok",
                {
                    "records": [
                        {"record_index": 2, "fields": ["title", "salary"]},
                        {"record_index": 1, "fields": ["title"]},
                        {"record_index": 0, "fields": ["title", "remote"]},
                    ]
                },
            )
        )
        selection = Moderator(client).synthesize(
            interpreted([]), self.records(), {}, [], []
        )
        self.assertEqual(selection.record_indices, [2, 1, 0])
        self.assertEqual(selection.claims[0].fields, ["title", "salary"])

    def test_cheapest_per_category(self):
        records = [
            {
                "title": "H1",
                "category": "headphones",
                "price": 100,
                "source_observation_id": "o1",
            },
            {
                "title": "K1",
                "category": "keyboards",
                "price": 40,
                "source_observation_id": "o2",
            },
            {
                "title": "H2",
                "category": "headphones",
                "price": 50,
                "source_observation_id": "o3",
            },
            {
                "title": "K2",
                "category": "keyboards",
                "price": 80,
                "source_observation_id": "o4",
            },
        ]
        selection = Moderator().synthesize(
            interpreted([], "Show the cheapest in each category"), records, {}, [], []
        )
        self.assertEqual(selection.record_indices, [2, 1])
        self.assertEqual([claim.record_index for claim in selection.claims], [0, 1])


class ObserverAndProtocolTests(unittest.TestCase):
    def test_protocols(self):
        value = Moderator()
        self.assertIsInstance(value, interfaces.Moderator)
        self.assertIsInstance(value, interfaces.ProgressObserver)

    def test_flag_stop_and_continue(self):
        moderator = Moderator(stall_seconds=120)
        stalled = moderator.observe_progress(
            {"elapsed_seconds": 5},
            event("stalled", subtask_id="a", seconds_since_last_action=121),
        )
        self.assertEqual(stalled.decision, "flag")

        stopped = moderator.observe_progress(
            {"elapsed_seconds": 11},
            event("tick", subtask_id="b", max_seconds=10),
        )
        self.assertEqual(stopped.decision, "stop_subtask")
        self.assertEqual(stopped.next_action["subtask_id"], "b")

        continued = moderator.observe_progress(
            {"elapsed_seconds": 1}, event("subtask_started", subtask_id="c")
        )
        self.assertEqual(continued.decision, "continue")


class EndToEndTests(unittest.TestCase):
    def run_controller(self, request, *, plan_fn=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        controller = Controller(
            FakeToolbox(),
            Moderator(None),
            FakeGhost(),
            JsonStore(directory.name),
            **({"plan": plan_fn} if plan_fn is not None else {}),
        )
        return controller.run(request, request.request_id)

    def test_registry_fixture_matches_stub_outcome(self):
        request = InterpretedRequest.from_dict(copy.deepcopy(REGISTRY))
        result = self.run_controller(request)
        self.assertEqual(result.status, "succeeded", result.error)
        self.assertEqual(
            [record["title"] for record in result.answer.records],
            ["Studio headphones", "Travel headphones"],
        )
        self.assertEqual(len(result.answer.claims), 2)
        self.assertEqual(result.validation["status"], "passed")

    def test_salary_chain_matches_stub_outcome(self):
        request = InterpretedRequest.from_dict(copy.deepcopy(SALARY))
        client = FakePlannerClient(copy.deepcopy(CHAIN))
        result = self.run_controller(
            request, plan_fn=partial(planner.plan, client=client)
        )
        self.assertEqual(result.status, "succeeded", result.error)
        records = result.answer.records
        self.assertTrue(records)
        self.assertLessEqual(len(records), 10)
        self.assertTrue(all(record["remote"] is True for record in records))
        salaries = [record["salary"] for record in records]
        self.assertEqual(salaries, sorted(salaries, reverse=True))
        self.assertEqual(len({record["url"] for record in records}), len(records))


if __name__ == "__main__":
    unittest.main()
