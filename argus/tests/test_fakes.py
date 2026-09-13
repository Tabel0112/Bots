"""Phase D checks: the fakes satisfy their protocols and behave as specified."""

import json
import unittest
from pathlib import Path

from argus import interfaces
from argus.contracts import (
    Budget,
    ContractError,
    Criterion,
    FinalAnswer,
    Intent,
    InterpretedRequest,
    ParameterOrigin,
    Plan,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)
from argus.fakes import (
    CATALOG,
    OPEN_DATASET,
    OPEN_RECORD_SHAPE,
    SCRIPTED_OUTCOMES,
    FakeGhost,
    FakePlannerClient,
    FakeToolbox,
    StubModerator,
)
from argus.model_client import ModelClient

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "argus" / "examples"
ORIGIN = "https://demo-catalog.invalid"
DOMAIN = "jobs.example.com"
CHAIN = json.loads((EXAMPLES / "plan_open_chain.json").read_text())
STEP_FIELDS = {
    "subtask_id", "intent_index", "operation", "depends_on", "inputs_from",
    "concurrency_group", "success_conditions", "preferred_tool",
}


def open_subtask(subtask_id="subtask-open-search", *, operation="search",
                 query="software engineering", shape=OPEN_RECORD_SHAPE,
                 target_domain=DOMAIN, **overrides):
    fields = {
        "subtask_id": subtask_id,
        "intent_index": 0,
        "site_id": target_domain,
        "operation": operation,
        "parameters": {"query": query},
        "concurrency_group": "jobs",
        "output_schema_id": "open-records.v1",
        "depends_on": [],
        "success_conditions": ["results present or explicit empty state"],
        "preferred_tool": "dom",
        "kind": "open",
        "target_domain": target_domain,
        "goal": "Find software engineering jobs",
        "expected_record_shape": list(shape),
    }
    fields.update(overrides)
    return Subtask(**fields)


def context_for(report, *, run_id="run-1", empty_state=False):
    """What the controller passes as report_context for an open subtask."""
    evidence = dict(report.evidence)
    evidence.pop("session_handle", None)
    return {
        "run_id": run_id,
        "subtask_id": report.subtask_id,
        "evidence": evidence,
        "empty_state": empty_state,
    }


def open_interpreted(*criteria, request_id="request-open-t"):
    text = "Find the highest salary 10 software engineering jobs, remote only, on jobs.example.com"
    return InterpretedRequest(
        request_id=request_id, raw_text=text, model="test", interpreted_at="2026-09-12T00:00:00Z",
        intents=[Intent(
            site_id=DOMAIN, operation="find_jobs", confidence=0.9, kind="open", target_domain=DOMAIN,
            parameters={"query": ParameterOrigin("software engineering", "text_span", 0.9, (27, 47))},
            goal="Find software engineering jobs", criteria=list(criteria),
            expected_record_shape=list(OPEN_RECORD_SHAPE),
        )],
    )


def criterion(kind, parameter, text=None):
    return Criterion(text or f"{kind} {parameter}", kind, parameter, None, 0.95)


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

    # -- merging by url ---------------------------------------------------

    def report_with(self, subtask_id, records):
        """A schema-valid report for ``subtask_id`` carrying exactly ``records``."""
        return WorkerReport.from_dict(
            dict(self.reports[0].to_dict(), subtask_id=subtask_id, findings=records)
        )

    def merged(self, *reports):
        return StubModerator().reconcile(self.plan, list(reports)).next_action["findings"]

    def test_a_later_subtask_supersedes_the_earlier_record_for_the_same_url(self):
        """The search-then-open_results chain: one entry per job, the detailed one."""
        search = self.report_with(
            "subtask-1",
            [
                {"title": "Staff Software Engineer", "url": f"{ORIGIN}/jobs/3",
                 "source_observation_id": "observation-000.png"},
                {"title": "Senior Software Engineer", "url": f"{ORIGIN}/jobs/1",
                 "source_observation_id": "observation-000.png"},
            ],
        )
        details = self.report_with(
            "subtask-2",
            [
                {"title": "Senior Software Engineer", "url": f"{ORIGIN}/jobs/1",
                 "salary": 185000, "source_observation_id": "observation-002.png"},
                {"title": "Staff Software Engineer", "url": f"{ORIGIN}/jobs/3",
                 "salary": 210000, "source_observation_id": "observation-003.png"},
            ],
        )
        findings = self.merged(search, details)
        self.assertEqual([record["url"] for record in findings],
                         [f"{ORIGIN}/jobs/1", f"{ORIGIN}/jobs/3"])
        self.assertEqual([record["salary"] for record in findings], [185000, 210000])
        self.assertEqual(
            [record["source_observation_id"] for record in findings],
            ["observation-002.png", "observation-003.png"],
            "the earlier, thinner record survived",
        )

    def test_the_merged_order_follows_the_later_report(self):
        earlier = self.report_with(
            "subtask-1",
            [{"url": f"{ORIGIN}/a"}, {"url": f"{ORIGIN}/b"}, {"url": f"{ORIGIN}/c"}],
        )
        later = self.report_with(
            "subtask-2", [{"url": f"{ORIGIN}/c", "seen": 2}, {"url": f"{ORIGIN}/a", "seen": 2}]
        )
        findings = self.merged(earlier, later)
        self.assertEqual(
            [record["url"] for record in findings],
            [f"{ORIGIN}/b", f"{ORIGIN}/c", f"{ORIGIN}/a"],
        )
        self.assertEqual([record.get("seen") for record in findings], [None, 2, 2])

    def test_records_without_a_url_are_kept_as_they_are(self):
        first = self.report_with(
            "subtask-1",
            [{"title": "No link here"}, {"title": "Blank link", "url": "  "},
             "not even an object"],
        )
        second = self.report_with(
            "subtask-2", [{"title": "Also no link"}, {"title": "Blank link", "url": "  "}]
        )
        findings = self.merged(first, second)
        self.assertEqual(
            findings,
            [{"title": "No link here"}, {"title": "Blank link", "url": "  "},
             "not even an object",
             {"title": "Also no link"}, {"title": "Blank link", "url": "  "}],
        )

    def test_two_independent_subtasks_keep_all_their_records(self):
        """Nothing overlaps, so merging is the concatenation it always was."""
        decision = StubModerator().reconcile(self.plan, self.reports)
        findings = decision.next_action["findings"]
        self.assertEqual(
            findings, list(self.reports[0].findings) + list(self.reports[1].findings)
        )
        self.assertEqual(
            len({record["url"] for record in findings}), len(findings), findings
        )
        self.assertNotIn("superseded", decision.reason)


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


class FakePlannerClientTests(unittest.TestCase):
    def test_satisfies_the_model_client_protocol(self):
        self.assertIsInstance(FakePlannerClient(CHAIN), ModelClient)

    def test_returns_only_step_fields_with_bindings_as_a_list(self):
        client = FakePlannerClient(CHAIN)
        result = client.parse_json("system text", "user text", dict, 4096)
        self.assertEqual(result.status, "ok")
        self.assertEqual(set(result.parsed), {"subtasks"})
        search, details = result.parsed["subtasks"]
        for step in (search, details):
            self.assertEqual(set(step), STEP_FIELDS)
        self.assertEqual(search["inputs_from"], [])
        self.assertEqual(
            details["inputs_from"],
            [{"parameter": "result_urls", "subtask_id": "subtask-open-search", "field": "url"}],
        )
        self.assertEqual(details["depends_on"], ["subtask-open-search"])
        self.assertEqual(
            client.calls,
            [{"system": "system text", "user": "user text", "output_model": dict, "max_tokens": 4096}],
        )

    def test_status_override_returns_that_status_with_no_content(self):
        for status in ("refusal", "truncated", "invalid"):
            with self.subTest(status=status):
                client = FakePlannerClient(CHAIN, status=status)
                result = client.parse_json("s", "u", dict, 1)
                self.assertEqual(result.status, status)
                self.assertIsNone(result.parsed)
                self.assertEqual(len(client.calls), 1)

    def test_bad_construction_is_rejected(self):
        with self.assertRaises(ContractError):
            FakePlannerClient(CHAIN, status="done")
        with self.assertRaises(ContractError):
            FakePlannerClient({})
        with self.assertRaises(ContractError):
            FakePlannerClient({"subtasks": [{"subtask_id": "s"}]})  # no intent_index etc.
        with self.assertRaises(ContractError):
            FakePlannerClient({"subtasks": ["not an object"]})

    def test_omitted_optional_scheduling_fields_get_subtask_defaults(self):
        client = FakePlannerClient({"subtasks": [
            {"subtask_id": "s", "intent_index": 0, "operation": "search", "concurrency_group": "g"},
        ]})
        [step] = client.parse_json("s", "u", dict, 1).parsed["subtasks"]
        self.assertEqual(step["depends_on"], [])
        self.assertEqual(step["inputs_from"], [])
        self.assertEqual(step["success_conditions"], [])
        self.assertEqual(step["preferred_tool"], "dom")

    def test_each_call_gets_a_fresh_copy(self):
        client = FakePlannerClient(CHAIN)
        first = client.parse_json("s", "u", dict, 1).parsed
        first["subtasks"].clear()
        second = client.parse_json("s", "u", dict, 1).parsed
        self.assertEqual(len(second["subtasks"]), 2)


class OpenWorldToolboxTests(unittest.TestCase):
    def test_the_dataset_has_at_least_six_complete_jobs(self):
        rows = OPEN_DATASET[DOMAIN]
        self.assertGreaterEqual(len(rows), 6)
        for row in rows:
            self.assertIsInstance(row["title"], str)
            self.assertIsInstance(row["company"], str)
            self.assertIsInstance(row["salary"], (int, float))
            self.assertNotIsInstance(row["salary"], bool)
            self.assertIsInstance(row["remote"], bool)

    def test_search_returns_shaped_cited_records_on_the_domain(self):
        report = run(FakeToolbox(), open_subtask())
        self.assertEqual(report.outcome, "succeeded")
        self.assertGreaterEqual(len(report.findings), 6)
        self.assertEqual(report.evidence["screenshots"], ["observation-000.png"])
        for record in report.findings:
            self.assertEqual(set(record), set(OPEN_RECORD_SHAPE) | {"source_observation_id"})
            self.assertTrue(record["url"].startswith(f"https://{DOMAIN}/"))
            self.assertIsInstance(record["salary"], (int, float))
            self.assertIsInstance(record["remote"], bool)
            self.assertEqual(record["source_observation_id"], "observation-000.png")
        self.assertNotIn("keywords", report.findings[0])
        self.assertEqual(report.worker, "fake")
        self.assertEqual(WorkerReport.from_dict(report.to_dict()), report)

    def test_records_follow_the_subtasks_expected_record_shape(self):
        report = run(FakeToolbox(), open_subtask(shape=("title", "url", "rating")))
        for record in report.findings:
            self.assertEqual(set(record), {"title", "url", "rating", "source_observation_id"})
            self.assertIsNone(record["rating"], "a field the dataset lacks is present but empty")

    def test_every_query_word_must_match(self):
        counts = {}
        for query in ("engineering", "software engineering", "contoso", "no-such-job"):
            counts[query] = len(run(FakeToolbox(), open_subtask(query=query)).findings)
        self.assertEqual(counts["engineering"], len(OPEN_DATASET[DOMAIN]))
        self.assertEqual(counts["software engineering"], 6)
        self.assertEqual(counts["contoso"], 2)
        self.assertEqual(counts["no-such-job"], 0)

    def test_an_empty_result_is_still_a_succeeded_report(self):
        report = run(FakeToolbox(), open_subtask(query="no-such-job"))
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.findings, [])
        self.assertEqual(report.evidence["screenshots"], ["observation-000.png"])

    def test_max_results_caps_the_search(self):
        task = open_subtask(parameters={"query": "software", "max_results": 2})
        self.assertEqual(len(run(FakeToolbox(), task).findings), 2)

    def test_an_unknown_domain_yields_an_explicit_empty_result(self):
        report = run(FakeToolbox(), open_subtask(target_domain="docs.example.org"))
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.findings, [])
        self.assertEqual(report.actions[0]["url"], "https://docs.example.org/search")

    def test_open_results_returns_one_record_per_url_in_order_each_with_its_own_observation(self):
        urls = [f"https://{DOMAIN}/jobs/3", f"https://{DOMAIN}/jobs/1", f"https://{DOMAIN}/jobs/999"]
        task = open_subtask("subtask-open-details", operation="open_results",
                            parameters={"result_urls": urls})
        report = run(FakeToolbox(), task)
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual([r["url"] for r in report.findings], urls)
        self.assertEqual([r["title"] for r in report.findings],
                         ["Staff Software Engineer", "Senior Software Engineer", None])
        observations = [r["source_observation_id"] for r in report.findings]
        self.assertEqual(len(set(observations)), 3, "each record cites its own observation")
        self.assertEqual(report.evidence["screenshots"], ["observation-000.png"] + observations)
        self.assertEqual(
            [a["action"]["name"] for a in report.actions],
            ["open_url", "open_url", "open_url", "open_url", "extract", "finished"],
        )
        self.assertEqual([a["url"] for a in report.actions[1:4]], urls)
        self.assertEqual([a["observation_after"] for a in report.actions[1:4]], observations)

    def test_open_results_without_urls_falls_back_to_a_search(self):
        task = open_subtask("subtask-open-details", operation="open_results")
        self.assertEqual(len(run(FakeToolbox(), task).findings), 6)

    def test_scripted_outcomes_apply_to_open_subtasks(self):
        failed = run(FakeToolbox({"subtask-open-search": "auth_required"}), open_subtask())
        self.assertEqual(failed.outcome, "failed")
        self.assertIsNone(failed.findings)
        self.assertEqual(failed.typed_failures[0].code, "AUTH_REQUIRED")
        self.assertEqual(failed.typed_failures[0].step_id, failed.failures[0]["step_id"])
        empty = run(FakeToolbox({"subtask-open-search": "empty"}), open_subtask())
        self.assertEqual((empty.outcome, empty.findings, empty.evidence["screenshots"]),
                         ("succeeded", [], []))
        with self.assertRaises(RuntimeError):
            run(FakeToolbox({"subtask-open-search": "raise"}), open_subtask())

    def test_open_calls_are_recorded_like_registry_calls(self):
        toolbox = FakeToolbox()
        run(toolbox, open_subtask())
        self.assertEqual([c[0] for c in toolbox.calls], ["open_session", "run_subtask"])
        self.assertEqual(toolbox.calls[1][1:], ("subtask-open-search", "explore", "dom", "fake-session-1"))

    def test_observations_are_numbered_across_open_and_registry_runs(self):
        toolbox = FakeToolbox()
        first = run(toolbox, subtask())
        second = run(toolbox, open_subtask())
        self.assertEqual(first.evidence["screenshots"], ["observation-000.png"])
        self.assertEqual(second.evidence["screenshots"], ["observation-001.png"])


class OpenWorldGhostTests(unittest.TestCase):
    def setUp(self):
        self.ghost = FakeGhost()
        self.task = open_subtask()
        self.report = run(FakeToolbox(), self.task)
        self.records = self.report.findings
        self.context = context_for(self.report)

    def validate(self, records, *, evidence=None, context="default", task=None):
        if context == "default":
            context = self.context
        evidence = self.report.evidence["screenshots"] if evidence is None else evidence
        return self.ghost.validate(task or self.task, records, evidence, report_context=context)

    def test_passes_on_the_fakes_own_open_report(self):
        result = self.validate(self.records)
        self.assertEqual(result["status"], "passed", result["failed_checks"])
        self.assertEqual(
            set(result["checks"]),
            {"records_are_objects", "records_cite_observations", "results_present_or_empty_state",
             "query_visibly_applied", "urls_on_target_domain"},
        )
        self.assertTrue(any("completeness" in note for note in result["unverified"]))
        self.assertIn("generic", result["scope"])
        self.assertEqual(self.ghost.calls, [("validate", "subtask-open-search", len(self.records))])

    def test_the_open_report_passes_without_a_context_too(self):
        result = self.validate(self.records, context=None)
        self.assertEqual(result["status"], "passed", result["failed_checks"])

    def test_an_uncited_record_fails(self):
        uncited = [dict(self.records[0], source_observation_id="observation-999.png")]
        result = self.validate(uncited)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["records_cite_observations"])
        missing = [dict(self.records[0], source_observation_id=None)]
        self.assertIn("records_cite_observations", self.validate(missing)["failed_checks"])

    def test_context_observations_count_as_cited(self):
        verified = [dict(self.records[0], source_observation_id="observation-fake-1")]
        context = dict(self.context, evidence={
            "screenshots": [], "verifications": [{"observation_id": "observation-fake-1"}],
        })
        result = self.validate(verified, evidence=[], context=context)
        self.assertEqual(result["status"], "passed", result["failed_checks"])

    def test_empty_records_need_an_explicit_empty_state(self):
        result = self.validate([], context=dict(self.context, empty_state=False))
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["results_present_or_empty_state"])
        result = self.validate([], context=dict(self.context, empty_state=True))
        self.assertEqual(result["status"], "passed", result["failed_checks"])

    def test_the_query_needs_at_least_one_observation(self):
        bare = dict(self.context, evidence={"screenshots": []}, empty_state=True)
        result = self.validate([], evidence=[], context=bare)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["query_visibly_applied"])

    def test_a_url_off_the_target_domain_fails(self):
        off = [dict(self.records[0], url="https://evil.example.org/jobs/1")]
        result = self.validate(off)
        self.assertEqual(result["failed_checks"], ["urls_on_target_domain"])
        sub = [dict(self.records[0], url=f"https://www.{DOMAIN}/jobs/1")]
        self.assertEqual(self.validate(sub)["status"], "passed")
        for bad in (None, 7, f"https://{DOMAIN}.evil.example.org/x", "not a url"):
            with self.subTest(url=bad):
                self.assertIn("urls_on_target_domain",
                              self.validate([dict(self.records[0], url=bad)])["failed_checks"])

    def test_a_record_that_is_not_an_object_fails(self):
        result = self.validate(["Staff Software Engineer"])
        self.assertIn("records_are_objects", result["failed_checks"])

    def test_registry_validation_is_unchanged_and_takes_no_context(self):
        task = subtask()
        records = run(FakeToolbox(), task).findings
        result = FakeGhost().validate(task, records, ["observation-000.png"])
        self.assertEqual(result["status"], "passed")
        self.assertEqual(
            set(result["checks"]),
            {"records_are_objects", "title_present", "price_present", "currency_usd",
             "url_on_site", "price_within_max"},
        )
        self.assertNotIn("unverified", result)


class StubModeratorCriteriaTests(unittest.TestCase):
    def setUp(self):
        self.task = open_subtask()
        self.report = run(FakeToolbox(), self.task)
        self.records = self.report.findings
        self.validation = FakeGhost().validate(
            self.task, self.records, self.report.evidence["screenshots"],
            report_context=context_for(self.report),
        )

    def synthesize(self, *criteria, records=None):
        return StubModerator().synthesize(
            open_interpreted(*criteria), list(self.records if records is None else records),
            self.validation, ["observation-000.png"], [],
        )

    def test_filter_rank_and_limit_apply_in_that_order(self):
        answer = self.synthesize(
            criterion("limit", 3, "10"),
            criterion("rank", "salary", "highest salary"),
            criterion("filter", "remote", "remote only"),
        )
        self.assertEqual(
            [(r["title"], r["salary"], r["remote"]) for r in answer.records],
            [("Staff Software Engineer", 210000, True),
             ("Senior Software Engineer", 185000, True),
             ("Site Reliability Engineer", 160000, True)],
        )
        self.assertEqual(len(answer.claims), 3)
        for claim, record in zip(answer.claims, answer.records):
            self.assertEqual(claim.evidence_refs, [record["source_observation_id"]])
            self.assertIn(record["title"], claim.text)
        self.assertEqual(answer.unverified, [])
        self.assertEqual(FinalAnswer.from_dict(answer.to_dict()), answer)

    def test_without_criteria_records_are_passed_through(self):
        answer = self.synthesize()
        self.assertEqual(answer.records, self.records)
        self.assertEqual(len(answer.claims), len(self.records))

    def test_a_criterion_whose_field_is_absent_is_named_unverified(self):
        answer = self.synthesize(criterion("rank", "rating", "best rated"))
        self.assertEqual(answer.records, self.records, "nothing was reordered")
        [note] = answer.unverified
        self.assertIn("'best rated'", note)
        self.assertIn("was not applied", note)
        self.assertIn("'rating'", note)

    def test_a_rank_without_a_field_is_unverified(self):
        answer = self.synthesize(Criterion("best", "rank", None, None, 0.4))
        [note] = answer.unverified
        self.assertIn("'best'", note)
        self.assertIn("names no record field", note)
        self.assertEqual(answer.records, self.records)

    def test_a_limit_must_be_a_non_negative_integer(self):
        for bad in ("ten", -1, True, 2.5):
            with self.subTest(limit=bad):
                answer = self.synthesize(criterion("limit", bad, "some"))
                self.assertEqual(len(answer.records), len(self.records))
                self.assertTrue(any("was not applied" in n for n in answer.unverified))
        self.assertEqual(len(self.synthesize(criterion("limit", 0)).records), 0)

    def test_a_filter_can_match_an_explicit_value(self):
        answer = self.synthesize(criterion("filter", {"field": "company", "value": "Contoso"}, "at Contoso"))
        self.assertEqual({r["company"] for r in answer.records}, {"Contoso"})
        self.assertEqual(len(answer.records), 2)

    def test_criteria_are_skipped_when_there_are_no_records(self):
        answer = self.synthesize(criterion("filter", "remote", "remote only"), records=[])
        self.assertEqual(answer.records, [])
        self.assertTrue(any("'remote'" in n for n in answer.unverified))

    def test_job_claims_name_the_fields_without_a_price_sentence(self):
        [claim] = self.synthesize(records=self.records[:1]).claims
        self.assertNotIn("costs", claim.text)
        self.assertIn("salary", claim.text)
        self.assertIn(self.records[0]["url"], claim.text)


if __name__ == "__main__":
    unittest.main()
