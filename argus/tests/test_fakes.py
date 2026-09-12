"""Phase D checks: the fakes satisfy their protocols and behave as specified."""

import json
import unittest
from pathlib import Path

from argus import interfaces
from argus.contracts import (
    Budget,
    ContractError,
    FinalAnswer,
    InterpretedRequest,
    Plan,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)
from argus.fakes import CATALOG, SCRIPTED_OUTCOMES, FakeGhost, FakeToolbox, StubModerator

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "argus" / "examples"
ORIGIN = "https://demo-catalog.invalid"


def subtask(
    subtask_id="subtask-1",
    *,
    query="headphones",
    max_price=150,
    preferred_tool="dom",
    **overrides,
):
    parameters = {"query": query, "max_results": 5, "currency": "USD"}
    if max_price is not None:
        parameters["max_price"] = max_price
    fields = {
        "subtask_id": subtask_id,
        "intent_index": 0,
        "site_id": "demo-catalog",
        "operation": "search_products",
        "parameters": parameters,
        "concurrency_group": "group-1",
        "output_schema_id": "product-list.v1",
        "depends_on": [],
        "success_conditions": [
            "results present or explicit empty state",
            "every record has title, price, currency, url",
            "price within max_price when given",
            "query visibly applied",
        ],
        "preferred_tool": preferred_tool,
    }
    fields.update(overrides)
    return Subtask(**fields)


def subtask_input(task=None, *, handle="fake-session-1", run_id="run-1", mode="explore"):
    return SubtaskInput(
        run_id=run_id,
        subtask=task or subtask(),
        session_handle=handle,
        budget=Budget(max_actions=30, max_seconds=120.0),
        mode=mode,
    )


def run(toolbox, task=None, *, run_id="run-1"):
    """Open a session the way the controller does, run the subtask, return the report."""
    task = task or subtask()
    handle = toolbox.open_session(task.site_id)
    return toolbox.run_subtask(subtask_input(task, handle=handle, run_id=run_id))


def interpreted():
    data = json.loads((EXAMPLES / "interpreted_request.json").read_text())
    return InterpretedRequest.from_dict(data)


class ProtocolTests(unittest.TestCase):
    def test_each_fake_satisfies_its_protocol(self):
        self.assertIsInstance(FakeToolbox(), interfaces.Toolbox)
        self.assertIsInstance(StubModerator(), interfaces.Moderator)
        self.assertIsInstance(FakeGhost(), interfaces.Ghost)

    def test_the_stub_moderator_does_not_offer_live_monitoring(self):
        self.assertNotIsInstance(StubModerator(), interfaces.ProgressObserver)


class SessionTests(unittest.TestCase):
    def test_handles_are_numbered_and_recorded_as_open(self):
        toolbox = FakeToolbox()
        first = toolbox.open_session("demo-catalog")
        second = toolbox.open_session("demo-catalog")
        self.assertEqual([first, second], ["fake-session-1", "fake-session-2"])
        self.assertEqual(toolbox.open_sessions, [first, second])
        self.assertEqual(toolbox.closed_sessions, [])

    def test_close_marks_the_session_closed(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        toolbox.close_session(handle)
        self.assertEqual(toolbox.open_sessions, [])
        self.assertEqual(toolbox.closed_sessions, [handle])

    def test_closing_twice_raises(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        toolbox.close_session(handle)
        with self.assertRaises(ContractError) as caught:
            toolbox.close_session(handle)
        self.assertIn("already closed", str(caught.exception))

    def test_closing_an_unknown_handle_raises(self):
        with self.assertRaises(ContractError):
            FakeToolbox().close_session("fake-session-99")

    def test_interpretation_needs_an_open_session(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        toolbox.close_session(handle)
        with self.assertRaises(ContractError):
            toolbox.observe(handle)
        with self.assertRaises(ContractError):
            toolbox.dom_interpret(handle, "is the query applied?")
        with self.assertRaises(ContractError):
            toolbox.vision_interpret(handle, "is the query applied?")

    def test_every_call_is_recorded(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        toolbox.run_subtask(subtask_input(handle=handle))
        toolbox.observe(handle)
        toolbox.dom_interpret(handle, "q")
        toolbox.vision_interpret(handle, "q")
        toolbox.close_session(handle)
        self.assertEqual(
            [call[0] for call in toolbox.calls],
            [
                "open_session",
                "run_subtask",
                "observe",
                "dom_interpret",
                "vision_interpret",
                "close_session",
            ],
        )


class ObservationTests(unittest.TestCase):
    def test_observe_returns_numbered_observation_ids(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        self.assertEqual(toolbox.observe(handle), "observation-fake-1")
        self.assertEqual(toolbox.observe(handle), "observation-fake-2")

    def test_interpretation_echoes_the_question(self):
        toolbox = FakeToolbox()
        handle = toolbox.open_session("demo-catalog")
        question = "is the query visibly applied?"
        self.assertIn(question, toolbox.dom_interpret(handle, question))
        self.assertIn(question, toolbox.vision_interpret(handle, question))
        self.assertTrue(toolbox.dom_interpret(handle, question).startswith("dom:"))
        self.assertTrue(toolbox.vision_interpret(handle, question).startswith("vision:"))


class SuccessfulReportTests(unittest.TestCase):
    def setUp(self):
        self.toolbox = FakeToolbox()
        self.report = run(self.toolbox)

    def test_the_report_is_a_complete_worker_report(self):
        self.assertIsInstance(self.report, WorkerReport)
        fixture_fields = set(json.loads((EXAMPLES / "worker_report.json").read_text()))
        self.assertEqual(len(fixture_fields), 13)
        payload = self.report.to_dict()
        self.assertTrue(fixture_fields <= set(payload))
        self.assertEqual(WorkerReport.from_dict(payload), self.report)

    def test_the_ids_are_the_subtasks(self):
        self.assertEqual(self.report.subtask_id, "subtask-1")
        self.assertEqual(self.report.request_id, "run-1")
        self.assertEqual(self.report.session_handle, "fake-session-1")
        self.assertEqual(self.report.worker, "fake")

    def test_findings_are_filtered_by_query_and_max_price(self):
        titles = [record["title"] for record in self.report.findings]
        self.assertEqual(titles, ["Studio headphones", "Travel headphones"])
        self.assertNotIn("Reference headphones", titles)  # 249 is above max_price 150

    def test_every_record_is_priced_in_usd_on_the_configured_site(self):
        for record in self.report.findings:
            self.assertEqual(record["currency"], "USD")
            self.assertTrue(record["url"].startswith(f"{ORIGIN}/"))
            self.assertLessEqual(record["price"], 150)
            self.assertEqual(record["source_observation_id"], "observation-000.png")

    def test_the_catalog_holds_five_products(self):
        self.assertEqual(len(CATALOG), 5)

    def test_a_query_matching_nothing_returns_no_records(self):
        report = run(FakeToolbox(), subtask(query="no-such-product"))
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.findings, [])

    def test_max_price_is_optional(self):
        report = run(FakeToolbox(), subtask(query="headphones", max_price=None))
        self.assertEqual(len(report.findings), 3)

    def test_evidence_and_metrics_describe_the_synthetic_run(self):
        self.assertEqual(self.report.evidence["screenshots"], ["observation-000.png"])
        self.assertEqual(
            self.report.metrics["browser_action_count"], len(self.report.actions)
        )
        self.assertEqual(self.report.typed_failures, [])
        self.assertEqual(self.report.failures, [])

    def test_the_lent_handle_does_not_leak_into_evidence(self):
        self.assertNotIn("fake-session-1", json.dumps(self.report.evidence))


class ScriptedOutcomeTests(unittest.TestCase):
    def test_an_unknown_scripted_outcome_is_rejected(self):
        with self.assertRaises(ContractError):
            FakeToolbox({"subtask-1": "explode"})

    def test_target_not_found(self):
        report = run(FakeToolbox({"subtask-1": "target_not_found"}))
        self.assertEqual(report.outcome, "failed")
        self.assertIsNone(report.findings)
        failure = report.typed_failures[0]
        self.assertEqual(failure.code, "TARGET_NOT_FOUND")
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.evidence_refs, ["observation-000.png"])
        self.assertEqual(report.failures[0]["step_id"], "step-002")

    def test_auth_required(self):
        report = run(FakeToolbox({"subtask-1": "auth_required"}))
        self.assertEqual(report.outcome, "failed")
        failure = report.typed_failures[0]
        self.assertEqual(failure.code, "AUTH_REQUIRED")
        self.assertFalse(failure.retryable)

    def test_budget(self):
        report = run(FakeToolbox({"subtask-1": "budget"}))
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(report.typed_failures[0].code, "BUDGET_EXCEEDED")
        self.assertGreater(report.metrics["browser_action_count"], 30)
        self.assertGreater(report.metrics["elapsed_ms"], 120_000)

    def test_raise(self):
        toolbox = FakeToolbox({"subtask-1": "raise"})
        handle = toolbox.open_session("demo-catalog")
        with self.assertRaises(RuntimeError):
            toolbox.run_subtask(subtask_input(handle=handle))
        self.assertEqual(toolbox.calls_named("run_subtask")[0][1], "subtask-1")

    def test_empty(self):
        report = run(FakeToolbox({"subtask-1": "empty"}))
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.findings, [])
        self.assertEqual(report.evidence["screenshots"], [])

    def test_every_documented_outcome_is_covered(self):
        covered = {
            name.removeprefix("test_")
            for name in dir(self)
            if name.startswith("test_")
        }
        self.assertTrue(set(SCRIPTED_OUTCOMES) <= covered)

    def test_the_script_only_touches_the_named_subtask(self):
        toolbox = FakeToolbox({"subtask-2": "auth_required"})
        self.assertEqual(run(toolbox, subtask("subtask-1")).outcome, "succeeded")
        self.assertEqual(run(toolbox, subtask("subtask-2")).outcome, "failed")

    def test_a_scripted_sequence_is_consumed_one_call_at_a_time(self):
        toolbox = FakeToolbox({"subtask-1": ["target_not_found"]})
        self.assertEqual(run(toolbox).outcome, "failed")
        self.assertEqual(run(toolbox).outcome, "succeeded")


class StubModeratorAssessTests(unittest.TestCase):
    def test_accept_when_succeeded_with_a_screenshot(self):
        task = subtask()
        report = run(FakeToolbox(), task)
        decision = StubModerator().assess_report(task, report, task.success_conditions)
        self.assertEqual((decision.stage, decision.decision), ("assess", "accept"))
        self.assertEqual(decision.evidence_refs, ["observation-000.png"])

    def test_verify_when_succeeded_without_evidence(self):
        task = subtask()
        report = run(FakeToolbox({"subtask-1": "empty"}), task)
        decision = StubModerator().assess_report(task, report, task.success_conditions)
        self.assertEqual(decision.decision, "verify")
        self.assertIn("question", decision.next_action)
        self.assertEqual(decision.next_action["tool"], "vision")

    def test_fail_carries_the_first_typed_failure(self):
        task = subtask()
        report = run(FakeToolbox({"subtask-1": "auth_required"}), task)
        decision = StubModerator().assess_report(task, report, task.success_conditions)
        self.assertEqual(decision.decision, "fail")
        self.assertIn("AUTH_REQUIRED", decision.reason)
        self.assertEqual(decision.next_action["failure"]["code"], "AUTH_REQUIRED")

    def test_fail_on_a_failed_report_with_no_typed_failure(self):
        task = subtask()
        report = run(FakeToolbox(), task)
        broken = WorkerReport.from_dict(dict(report.to_dict(), outcome="failed"))
        decision = StubModerator().assess_report(task, broken, task.success_conditions)
        self.assertEqual(decision.decision, "fail")
        self.assertIn("EXTRACTION_FAILED", decision.reason)

    def test_retry_other_path_only_when_opted_in(self):
        task = subtask()
        report = run(FakeToolbox({"subtask-1": "target_not_found"}), task)
        default = StubModerator().assess_report(task, report, task.success_conditions)
        self.assertEqual(default.decision, "fail")
        opted_in = StubModerator(retry_codes=("TARGET_NOT_FOUND",)).assess_report(
            task, report, task.success_conditions
        )
        self.assertEqual(opted_in.decision, "retry_other_path")

    def test_an_unknown_retry_code_is_rejected(self):
        with self.assertRaises(ContractError):
            StubModerator(retry_codes=("NOT_A_CODE",))


class StubModeratorReconcileTests(unittest.TestCase):
    def setUp(self):
        self.tasks = [subtask("subtask-1"), subtask("subtask-2", query="keyboard")]
        self.toolbox = FakeToolbox()
        self.reports = [run(self.toolbox, task) for task in self.tasks]
        self.plan = Plan(
            plan_id="plan-1",
            request_id="request-1",
            subtasks=self.tasks,
            created_at="2026-09-12T17:00:01Z",
        )

    def test_merged_lists_the_subtask_ids_and_no_conflicts(self):
        decision = StubModerator().reconcile(self.plan, self.reports)
        self.assertEqual((decision.stage, decision.decision), ("reconcile", "merged"))
        self.assertEqual(
            decision.next_action["subtask_ids"], ["subtask-1", "subtask-2"]
        )
        self.assertEqual(decision.next_action["conflicts"], [])
        self.assertEqual(decision.next_action["gaps"], [])
        self.assertEqual(
            len(decision.next_action["findings"]),
            sum(len(report.findings) for report in self.reports),
        )

    def test_a_subtask_without_a_report_is_named_as_a_gap(self):
        decision = StubModerator().reconcile(self.plan, self.reports[:1])
        self.assertEqual(
            [gap["subtask_id"] for gap in decision.next_action["gaps"]], ["subtask-2"]
        )
        self.assertEqual(decision.next_action["conflicts"], [])


class StubModeratorSynthesizeTests(unittest.TestCase):
    def setUp(self):
        self.task = subtask()
        self.report = run(FakeToolbox(), self.task)
        self.records = self.report.findings
        self.validation = FakeGhost().validate(self.task, self.records, ["observation-000.png"])

    def synthesize(self, records, *, evidence=("observation-000.png",), failures=()):
        return StubModerator().synthesize(
            interpreted(), list(records), self.validation, list(evidence), list(failures)
        )

    def test_one_claim_per_record_citing_its_own_observation(self):
        answer = self.synthesize(self.records)
        self.assertIsInstance(answer, FinalAnswer)
        self.assertEqual(len(answer.claims), len(self.records))
        for claim, record in zip(answer.claims, self.records):
            self.assertEqual(claim.evidence_refs, [record["source_observation_id"]])
            self.assertIn(record["title"], claim.text)

    def test_no_claim_is_emitted_without_an_evidence_ref(self):
        anonymous = [dict(record, source_observation_id=None) for record in self.records]
        cases = [
            (self.records, ()),
            (anonymous, ("observation-000.png",)),
            (anonymous, ()),
            ([{"title": "Loose record"}, "not even an object"], ()),
            ([], ()),
        ]
        for records, evidence in cases:
            with self.subTest(records=len(records), evidence=len(evidence)):
                answer = self.synthesize(records, evidence=evidence)
                for claim in answer.claims:
                    self.assertTrue(claim.evidence_refs)
                    self.assertTrue(all(claim.evidence_refs))

    def test_a_record_without_evidence_falls_back_then_becomes_unverified(self):
        anonymous = [dict(record, source_observation_id=None) for record in self.records]
        fell_back = self.synthesize(anonymous, evidence=("observation-000.png",))
        self.assertEqual(
            [claim.evidence_refs for claim in fell_back.claims],
            [["observation-000.png"]] * len(anonymous),
        )
        uncited = self.synthesize(anonymous, evidence=())
        self.assertEqual(uncited.claims, [])
        self.assertEqual(len(uncited.unverified), len(anonymous))

    def test_failures_are_listed_verbatim(self):
        failure = TypedError(
            code="AUTH_REQUIRED",
            message="the catalog asked for a sign-in",
            retryable=False,
            step_id="step-002",
            evidence_refs=["observation-000.png"],
        )
        answer = self.synthesize(self.records, failures=(failure,))
        self.assertEqual(answer.failures, [failure])
        self.assertIn("AUTH_REQUIRED: the catalog asked for a sign-in", answer.unverified)

    def test_records_are_passed_through_and_the_answer_round_trips(self):
        answer = self.synthesize(self.records)
        self.assertEqual(answer.records, self.records)
        self.assertEqual(FinalAnswer.from_dict(answer.to_dict()), answer)

    def test_a_failed_validation_is_named_unverified(self):
        answer = StubModerator().synthesize(
            interpreted(),
            list(self.records),
            {"status": "failed", "checks": {}},
            ["observation-000.png"],
            [],
        )
        self.assertIn("Validation status is 'failed', not 'passed'.", answer.unverified)


class FakeGhostMatchTests(unittest.TestCase):
    def test_match_always_explores(self):
        ghost = FakeGhost()
        decision = ghost.match(subtask(), [{"skill_id": "demo-catalog.search-products"}])
        self.assertEqual(decision["decision"], "explore")
        self.assertEqual(decision["reason"], "no qualified skills in fake")
        self.assertIsNone(decision["skill"])
        self.assertEqual(ghost.calls, [("match", "subtask-1", 1)])


class FakeGhostValidateTests(unittest.TestCase):
    def setUp(self):
        self.ghost = FakeGhost()
        self.task = subtask()
        self.records = run(FakeToolbox(), self.task).findings

    def validate(self, records, task=None):
        return self.ghost.validate(task or self.task, records, ["observation-000.png"])

    def test_passes_on_the_fakes_own_records(self):
        result = self.validate(self.records)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["failed_checks"], [])
        self.assertTrue(all(result["checks"].values()))

    def test_fails_with_named_checks(self):
        cases = {
            "currency_usd": dict(self.records[0], currency="CAD"),
            "url_on_site": dict(self.records[0], url="https://example-shop.invalid/p/1"),
            "price_within_max": dict(self.records[0], price=249.0),
            "title_present": dict(self.records[0], title=""),
            "price_present": dict(self.records[0], price="129.00"),
        }
        for check, record in cases.items():
            with self.subTest(check=check):
                result = self.validate([record])
                self.assertEqual(result["status"], "failed")
                self.assertIn(check, result["failed_checks"])
                self.assertFalse(result["checks"][check])

    def test_a_record_that_is_not_an_object_fails(self):
        result = self.validate(["Studio headphones"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("records_are_objects", result["failed_checks"])

    def test_price_within_max_is_skipped_when_no_max_price_was_asked_for(self):
        task = subtask(max_price=None)
        records = run(FakeToolbox(), task).findings
        result = self.validate(records, task)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["checks"]["price_within_max"])

    def test_the_scope_of_the_check_is_stated(self):
        self.assertIn("no live website", self.validate(self.records)["scope"])


class FakeGhostCompileTests(unittest.TestCase):
    def test_compile_returns_a_candidate_skill(self):
        task = subtask()
        report = run(FakeToolbox(), task)
        skill = FakeGhost().compile(report, task)
        self.assertEqual(skill["status"], "candidate")
        self.assertEqual(skill["operation"], "search_products")
        self.assertEqual(skill["skill_id"], "demo-catalog.search-products")
        self.assertEqual(
            [step["action"] for step in skill["definition"]["steps"]],
            ["open_url", "type", "extract", "finished"],
        )
        self.assertEqual(
            skill["definition"]["output_schema_id"], "product-list.v1"
        )
        json.dumps(skill)  # a skill must be storable as JSON

    def test_compile_stores_no_session_handle(self):
        task = subtask()
        report = run(FakeToolbox(), task)
        self.assertEqual(report.session_handle, "fake-session-1")
        self.assertNotIn("fake-session-1", json.dumps(FakeGhost().compile(report, task)))

    def test_compile_returns_none_without_actions(self):
        task = subtask()
        report = run(FakeToolbox(), task)
        actionless = WorkerReport.from_dict(dict(report.to_dict(), actions=[]))
        self.assertIsNone(FakeGhost().compile(actionless, task))


if __name__ == "__main__":
    unittest.main()
