"""Phase 0 checks: JSON round-trips, strictness, and the registry rules."""

import json
import unittest
from pathlib import Path

from argus import registry
from argus.contracts import (
    Budget,
    Claim,
    ContractError,
    Event,
    FinalAnswer,
    GateDecision,
    Intent,
    InterpretedRequest,
    MissingParameter,
    ModeratorDecision,
    ParameterOrigin,
    Plan,
    RunResult,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = REPO_ROOT / "argus" / "examples"
WORKER_REPORT = REPO_ROOT / "workers" / "visual" / "examples" / "hn-top-story" / "report.json"

#: Every fixture file and the message it must load as.  A new fixture without an
#: entry here fails ``test_every_fixture_is_mapped``.
FIXTURES = {
    "final_answer.json": FinalAnswer,
    "gate_clarify.json": GateDecision,
    "gate_reject.json": GateDecision,
    "interpreted_request.json": InterpretedRequest,
    "moderator_decision_accept.json": ModeratorDecision,
    "plan.json": Plan,
    "worker_report.json": WorkerReport,
}


def load(name):
    return json.loads((EXAMPLES / name).read_text())


class FixtureTests(unittest.TestCase):
    def test_every_fixture_is_mapped(self):
        found = sorted(path.name for path in EXAMPLES.glob("*.json"))
        self.assertEqual(found, sorted(FIXTURES))

    def test_every_fixture_round_trips(self):
        for name, message in FIXTURES.items():
            with self.subTest(fixture=name):
                loaded = message.from_dict(load(name))
                self.assertEqual(message.from_dict(loaded.to_dict()), loaded)

    def test_fixture_payload_survives_a_round_trip(self):
        """to_dict adds only the fields the fixture left at their default."""
        for name, message in FIXTURES.items():
            with self.subTest(fixture=name):
                payload = load(name)
                produced = message.from_dict(payload).to_dict()
                for key, value in payload.items():
                    self.assertEqual(produced[key], value, msg=f"{name}:{key}")

    def test_fixtures_use_supported_sites_and_operations(self):
        for intent in InterpretedRequest.from_dict(load("interpreted_request.json")).intents:
            self.assertTrue(registry.site_supports(intent.site_id, intent.operation))
        for subtask in Plan.from_dict(load("plan.json")).subtasks:
            self.assertTrue(registry.site_supports(subtask.site_id, subtask.operation))
            self.assertEqual(registry.validate_parameters(subtask.operation, subtask.parameters), [])

    def test_text_spans_match_the_raw_text(self):
        interpreted = InterpretedRequest.from_dict(load("interpreted_request.json"))
        for intent in interpreted.intents:
            for name, origin in intent.parameters.items():
                with self.subTest(parameter=name):
                    if origin.source == "text_span":
                        start, end = origin.span
                        self.assertEqual(
                            interpreted.raw_text[start:end], str(origin.value)
                        )
                    else:
                        self.assertIsNone(origin.span)

    def test_every_claim_in_the_final_answer_cites_evidence(self):
        answer = FinalAnswer.from_dict(load("final_answer.json"))
        self.assertTrue(answer.claims)
        for claim in answer.claims:
            self.assertTrue(claim.evidence_refs)


class WorkerReportTests(unittest.TestCase):
    def test_accepts_the_real_visual_worker_report_unchanged(self):
        payload = json.loads(WORKER_REPORT.read_text())
        report = WorkerReport.from_dict(payload)
        self.assertEqual(report.schema_version, "0.1-provisional")
        self.assertEqual(report.worker, "visual")
        self.assertEqual(report.outcome, "succeeded")
        self.assertIsNone(report.findings)
        self.assertEqual(report.metrics["browser_action_count"], 1)
        self.assertEqual(report.evidence["screenshots"], ["observation-000.png"])
        self.assertEqual(len(report.actions), 2)
        self.assertEqual(report.failures, [])

    def test_argus_additions_default_when_absent(self):
        report = WorkerReport.from_dict(json.loads(WORKER_REPORT.read_text()))
        self.assertIsNone(report.session_handle)
        self.assertEqual(report.typed_failures, [])

    def test_argus_additions_round_trip_when_present(self):
        payload = json.loads(WORKER_REPORT.read_text())
        payload["session_handle"] = "session-7"
        payload["typed_failures"] = [
            {
                "code": "EXTRACTION_FAILED",
                "message": "The points value was not visible.",
                "retryable": True,
                "step_id": "step-001",
                "evidence_refs": ["observation-000.png"],
            }
        ]
        report = WorkerReport.from_dict(payload)
        self.assertEqual(report.session_handle, "session-7")
        self.assertIsInstance(report.typed_failures[0], TypedError)
        self.assertEqual(WorkerReport.from_dict(report.to_dict()), report)

    def test_typed_failures_are_not_shared_with_the_payload(self):
        payload = json.loads(WORKER_REPORT.read_text())
        report = WorkerReport.from_dict(payload)
        report.evidence["screenshots"].append("observation-001.png")
        self.assertEqual(payload["evidence"]["screenshots"], ["observation-000.png"])


class StrictnessTests(unittest.TestCase):
    def test_unknown_field_is_rejected(self):
        payload = load("gate_clarify.json") | {"severity": "high"}
        with self.assertRaises(ContractError) as caught:
            GateDecision.from_dict(payload)
        self.assertIn("severity", str(caught.exception))

    def test_missing_required_field_is_rejected(self):
        payload = load("plan.json")
        del payload["created_at"]
        with self.assertRaises(ContractError) as caught:
            Plan.from_dict(payload)
        self.assertIn("created_at", str(caught.exception))

    def test_unknown_field_in_a_nested_message_is_rejected(self):
        payload = load("plan.json")
        payload["subtasks"][0]["retries"] = 2
        with self.assertRaises(ContractError) as caught:
            Plan.from_dict(payload)
        self.assertIn("retries", str(caught.exception))

    def test_non_object_payload_is_rejected(self):
        with self.assertRaises(ContractError):
            GateDecision.from_dict(["accept"])

    def test_nested_list_must_be_a_list(self):
        payload = load("plan.json") | {"subtasks": {"subtask-1": {}}}
        with self.assertRaises(ContractError):
            Plan.from_dict(payload)

    def test_fixed_vocabularies_are_enforced(self):
        cases = [
            (GateDecision, load("gate_clarify.json") | {"decision": "maybe"}),
            (ModeratorDecision, load("moderator_decision_accept.json") | {"stage": "publish"}),
            (
                TypedError,
                {"code": "KABOOM", "message": "boom", "retryable": False},
            ),
            (
                RunResult,
                {"run_id": "run-1", "status": "partially"},
            ),
        ]
        for message, payload in cases:
            with self.subTest(message=message.__name__):
                with self.assertRaises(ContractError):
                    message.from_dict(payload)

    def test_parameter_origin_span_is_normalised_and_checked(self):
        origin = ParameterOrigin(value="headphones", source="text_span", confidence=1.0, span=[5, 15])
        self.assertEqual(origin.span, (5, 15))
        self.assertEqual(origin.to_dict()["span"], [5, 15])
        self.assertEqual(ParameterOrigin.from_dict(origin.to_dict()), origin)
        with self.assertRaises(ContractError):
            ParameterOrigin(value="x", source="text_span", confidence=1.0, span=[9, 2])
        with self.assertRaises(ContractError):
            ParameterOrigin(value="x", source="text_span", confidence=1.0, span=[1, 2, 3])

    def test_contract_error_carries_a_typed_error(self):
        error = ContractError("the model refused", code="MODEL_REFUSED")
        typed = error.typed_error
        self.assertEqual(typed.code, "MODEL_REFUSED")
        self.assertEqual(typed.message, "the model refused")
        self.assertFalse(typed.retryable)
        with self.assertRaises(ValueError):
            ContractError("nope", code="NOT_A_CODE")

    def test_required_fields_are_the_ones_without_defaults(self):
        self.assertEqual(
            WorkerReport.required_fields(),
            (
                "schema_version",
                "worker",
                "worker_model",
                "request_id",
                "subtask_id",
                "subtask",
                "outcome",
                "summary",
                "findings",
                "actions",
                "evidence",
                "metrics",
                "failures",
            ),
        )


class ConstructedMessageTests(unittest.TestCase):
    """The messages with no fixture of their own still round-trip."""

    def _round_trip(self, message):
        self.assertEqual(type(message).from_dict(message.to_dict()), message)

    def test_subtask_input_round_trips(self):
        subtask = Plan.from_dict(load("plan.json")).subtasks[0]
        self._round_trip(
            SubtaskInput(
                run_id="run-1",
                subtask=subtask,
                session_handle="fake-session-1",
                budget=Budget(max_actions=30, max_seconds=120),
                mode="explore",
            )
        )

    def test_event_round_trips(self):
        self._round_trip(
            Event(
                run_id="run-1",
                sequence=1,
                timestamp="2026-09-12T17:00:00Z",
                type="stage_changed",
                stage="exploring",
                message="Searching the catalog",
            )
        )

    def test_run_result_round_trips_with_every_stage_filled(self):
        result = RunResult(
            run_id="run-1",
            status="succeeded",
            interpreted=InterpretedRequest.from_dict(load("interpreted_request.json")),
            gate=GateDecision(decision="accept", rule_id="G0", reason="all rules passed"),
            plan=Plan.from_dict(load("plan.json")),
            reports=[WorkerReport.from_dict(load("worker_report.json"))],
            validation={"status": "passed", "checks": {"price_and_currency": True}},
            answer=FinalAnswer.from_dict(load("final_answer.json")),
            metrics={"elapsed_ms": 20233, "model_call_count": 3},
        )
        self._round_trip(result)

    def test_in_progress_run_result_round_trips(self):
        self._round_trip(RunResult(run_id="run-1", status="needs_input"))

    def test_interpreted_request_with_missing_parameters_round_trips(self):
        self._round_trip(
            InterpretedRequest(
                request_id="request-demo-2",
                raw_text="search the demo catalog",
                intents=[
                    Intent(
                        site_id="demo-catalog",
                        operation="search_products",
                        parameters={},
                        confidence=0.5,
                    )
                ],
                model="claude-opus-5",
                interpreted_at="2026-09-12T17:05:00Z",
                missing_required=[
                    MissingParameter(
                        intent_index=0,
                        parameter="query",
                        question="What product should I search for?",
                    )
                ],
                ambiguities=["No product named."],
            )
        )

    def test_final_answer_with_failures_round_trips(self):
        self._round_trip(
            FinalAnswer(
                text="The catalog required a sign-in, so nothing was searched.",
                claims=[Claim(text="Sign-in was required.", evidence_refs=["observation-002.png"])],
                failures=[
                    TypedError(
                        code="AUTH_REQUIRED",
                        message="The catalog asked for a sign-in.",
                        retryable=False,
                    )
                ],
                unverified=["Whether any product matches the query."],
            )
        )


class RegistryTests(unittest.TestCase):
    def test_sites_declare_only_supported_operations(self):
        for site_id, site in registry.SITES.items():
            self.assertEqual(site["site_id"], site_id)
            for operation in site["operations"]:
                self.assertIn(operation, registry.OPERATIONS)
                self.assertEqual(registry.OPERATIONS[operation]["site_id"], site_id)

    def test_required_parameters_and_defaults(self):
        self.assertEqual(registry.required_parameters("search_products"), ["query"])
        self.assertEqual(
            registry.defaults("search_products"), {"max_results": 5, "currency": "USD"}
        )

    def test_unknown_operation_raises_for_lookups(self):
        for lookup in (registry.required_parameters, registry.defaults, registry.operation_spec):
            with self.subTest(lookup=lookup.__name__):
                with self.assertRaises(ContractError):
                    lookup("book_flight")

    def test_site_supports(self):
        self.assertTrue(registry.site_supports("demo-catalog", "search_products"))
        self.assertFalse(registry.site_supports("demo-catalog", "book_flight"))
        self.assertFalse(registry.site_supports("example-shop", "search_products"))

    def test_valid_parameters_have_no_problems(self):
        self.assertEqual(
            registry.validate_parameters(
                "search_products",
                {"query": "headphones", "max_price": 150, "max_results": 5, "currency": "USD"},
            ),
            [],
        )
        self.assertEqual(
            registry.validate_parameters("search_products", {"query": "headphones"}), []
        )

    def test_validate_parameters_reports_each_problem(self):
        cases = {
            "unsupported operation": ("book_flight", {"query": "x"}),
            "unknown parameter 'colour'": ("search_products", {"query": "x", "colour": "red"}),
            "missing required parameter 'query'": ("search_products", {"max_price": 10}),
            "must be a string": ("search_products", {"query": 5}),
            "must be a number": ("search_products", {"query": "x", "max_price": "cheap"}),
            "must be an integer": ("search_products", {"query": "x", "max_results": 2.5}),
            "must be at least 0": ("search_products", {"query": "x", "max_price": -1}),
            "must be at least 1": ("search_products", {"query": "x", "max_results": 0}),
            "is fixed at 'USD'": ("search_products", {"query": "x", "currency": "CAD"}),
            "must not be null": ("search_products", {"query": None}),
        }
        for expected, (operation, params) in cases.items():
            with self.subTest(problem=expected):
                problems = registry.validate_parameters(operation, params)
                self.assertTrue(
                    any(expected in problem for problem in problems),
                    msg=f"{expected!r} not in {problems}",
                )

    def test_booleans_are_not_numbers(self):
        problems = registry.validate_parameters(
            "search_products", {"query": "x", "max_price": True, "max_results": False}
        )
        self.assertEqual(len(problems), 2)

    def test_validate_parameters_never_raises(self):
        self.assertEqual(
            registry.validate_parameters("search_products", ["query"]),
            ["parameters must be an object"],
        )


if __name__ == "__main__":
    unittest.main()


class ModeratorDecisionVocabularyTests(unittest.TestCase):
    def test_decision_must_match_its_stage(self):
        from argus.contracts import ContractError, ModeratorDecision, MODERATOR_DECISIONS
        for stage, allowed in MODERATOR_DECISIONS.items():
            for decision in allowed:
                ModeratorDecision(stage=stage, decision=decision, reason="ok")
        with self.assertRaises(ContractError):
            ModeratorDecision(stage="assess", decision="merged", reason="wrong stage")
        with self.assertRaises(ContractError):
            ModeratorDecision(stage="reconcile", decision="accept", reason="wrong stage")
        with self.assertRaises(ContractError):
            ModeratorDecision(stage="synthesize", decision="accept", reason="not a decision stage")


class RunStatusTests(unittest.TestCase):
    def test_running_is_a_valid_snapshot_status(self):
        from argus.contracts import RunResult
        snapshot = RunResult(run_id="run-x", status="running")
        self.assertEqual(RunResult.from_dict(snapshot.to_dict()), snapshot)
