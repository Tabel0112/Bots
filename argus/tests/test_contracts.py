"""Phase 0 checks: JSON round-trips, strictness, and the registry rules."""

import json
import unittest
from pathlib import Path

from argus import registry
from argus.contracts import (
    Budget,
    Claim,
    ContractError,
    Criterion,
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
    "gate_open_accept.json": GateDecision,
    "gate_open_reject_domain.json": GateDecision,
    "gate_reject.json": GateDecision,
    "interpreted_request.json": InterpretedRequest,
    "interpreted_request_open.json": InterpretedRequest,
    "interpreted_request_open_salary.json": InterpretedRequest,
    "moderator_decision_accept.json": ModeratorDecision,
    "plan.json": Plan,
    "plan_open_chain.json": Plan,
    "worker_report.json": WorkerReport,
}


def load(name):
    return json.loads((EXAMPLES / name).read_text())


def assert_payload_preserved(test_case, expected, actual, path="payload"):
    """Assert every fixture value survives while permitting nested defaults."""
    if isinstance(expected, dict):
        test_case.assertIsInstance(actual, dict, msg=path)
        for key, value in expected.items():
            test_case.assertIn(key, actual, msg=path)
            assert_payload_preserved(test_case, value, actual[key], f"{path}.{key}")
        return
    if isinstance(expected, list):
        test_case.assertIsInstance(actual, list, msg=path)
        test_case.assertEqual(len(actual), len(expected), msg=path)
        for index, value in enumerate(expected):
            assert_payload_preserved(test_case, value, actual[index], f"{path}[{index}]")
        return
    test_case.assertEqual(actual, expected, msg=path)


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
                assert_payload_preserved(self, payload, produced, name)

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

    def test_open_world_fixtures_round_trip_with_the_new_fields(self):
        interpreted = InterpretedRequest.from_dict(load("interpreted_request_open.json"))
        self.assertEqual(interpreted.intents[0].kind, "open")
        self.assertIsInstance(interpreted.intents[0].criteria[0], Criterion)

        plan = Plan.from_dict(load("plan_open_chain.json"))
        self.assertEqual(plan.planned_by, "model")
        self.assertEqual(plan.subtasks[1].inputs_from["result_urls"]["field"], "url")

    def test_old_fixtures_receive_backward_compatible_defaults(self):
        intent = InterpretedRequest.from_dict(load("interpreted_request.json")).intents[0]
        self.assertEqual(intent.kind, "registry")
        self.assertIsNone(intent.target_domain)
        self.assertEqual(intent.criteria, [])

        plan = Plan.from_dict(load("plan.json"))
        self.assertEqual(plan.planned_by, "deterministic")
        self.assertEqual(plan.caps, {"max_subtasks": 4, "max_depth": 3})
        self.assertEqual(plan.subtasks[0].inputs_from, {})
        self.assertEqual(plan.subtasks[0].kind, "registry")


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

    def test_criterion_span_and_vocab_are_checked(self):
        criterion = Criterion(
            text="top 10", kind="limit", parameter=10, confidence=0.99, span=[5, 11]
        )
        self.assertEqual(criterion.span, (5, 11))
        self.assertEqual(Criterion.from_dict(criterion.to_dict()), criterion)
        with self.assertRaises(ContractError):
            Criterion(
                text="recent", kind="sort", parameter=None, span=None, confidence=0.9
            )
        with self.assertRaises(ContractError):
            Criterion(
                text="best", kind="rank", parameter=None, span=[9, 2], confidence=0.4
            )

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

    def test_domain_allowed_accepts_an_unlisted_domain(self):
        allowed, reason = registry.domain_allowed("Jobs.Example.com.")
        self.assertTrue(allowed)
        self.assertIn("jobs.example.com", reason)

    def test_domain_allowed_blocks_exact_domains_and_subdomains(self):
        for domain in ("checkout.stripe.com", "pay.checkout.stripe.com", "accounts.google.com"):
            with self.subTest(domain=domain):
                allowed, reason = registry.domain_allowed(domain)
                self.assertFalse(allowed)
                self.assertIn("blocked", reason)

    def test_domain_allowed_rejects_missing_and_url_shaped_values(self):
        for domain in ("", "https://example.com", "bad domain.example"):
            with self.subTest(domain=domain):
                allowed, reason = registry.domain_allowed(domain)
                self.assertFalse(allowed)
                self.assertTrue(reason)


class OpenContractValidationTests(unittest.TestCase):
    def test_invalid_caps_are_rejected_at_deserialization(self):
        for caps in (None, {}, {"max_subtasks": -1},
                     {"max_subtasks": True, "max_depth": 3},
                     {"max_subtasks": 4, "max_depth": 0},
                     {"max_subtasks": 4.5, "max_depth": 3},
                     {"max_subtasks": 4, "max_depth": 3, "extra": 1}):
            with self.subTest(caps=caps), self.assertRaises(ContractError):
                Plan.from_dict(load("plan.json") | {"caps": caps})

    def test_caps_cannot_be_raised_above_controller_budgets(self):
        for caps in ({"max_subtasks": 99, "max_depth": 3},
                     {"max_subtasks": 4, "max_depth": 99}):
            with self.subTest(caps=caps), self.assertRaises(ContractError) as caught:
                Plan.from_dict(load("plan_open_chain.json") | {"caps": caps})
            self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")

    def test_open_and_mixed_plans_count_all_subtasks(self):
        for mixed in (False, True):
            payload = load("plan_open_chain.json")
            template = payload["subtasks"][0]
            payload["subtasks"] = [template | {"subtask_id": f"step-{i}"} for i in range(5)]
            if mixed:
                payload["subtasks"][0] = load("plan.json")["subtasks"][0]
            with self.subTest(mixed=mixed), self.assertRaises(ContractError) as caught:
                Plan.from_dict(payload)
            self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")

    def test_registry_only_plans_keep_their_existing_total_work_behavior(self):
        payload = load("plan.json")
        payload["subtasks"] = [payload["subtasks"][0] | {"subtask_id": f"step-{i}"} for i in range(5)]
        from argus.planner import validate_plan
        validate_plan(Plan.from_dict(payload))

    def test_mutated_plan_budget_is_rechecked_by_planner(self):
        from argus.planner import validate_plan
        plan = Plan.from_dict(load("plan.json"))
        plan.caps["max_subtasks"] = 99
        with self.assertRaises(ContractError) as caught:
            validate_plan(plan)
        self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")

    def test_malformed_dependency_inputs_are_rejected(self):
        payload = load("plan_open_chain.json")["subtasks"][1]
        for source in (None, [], {"urls": {"wrong_key": "step-1"}},
                       {"urls": {"subtask_id": "subtask-open-search", "field": ""}},
                       {"urls": {"subtask_id": "undeclared", "field": "url"}},
                       {"urls": {"subtask_id": "subtask-open-search", "field": "url", "extra": 1}}):
            with self.subTest(source=source), self.assertRaises(ContractError):
                Subtask.from_dict(payload | {"inputs_from": source})

    def test_invalid_confidence_and_spans_fail_with_contract_error(self):
        payload = {"text": "best", "kind": "rank", "parameter": None, "span": None, "confidence": 0.4}
        for confidence in ("not-a-number", None, True, -0.1, 1.1, float("nan"), float("inf")):
            with self.subTest(confidence=confidence), self.assertRaises(ContractError):
                Criterion.from_dict(payload | {"confidence": confidence})
        for span in (7, "0,4", [True, 4], [-1, 4], [4, 1]):
            with self.subTest(span=span), self.assertRaises(ContractError):
                Criterion.from_dict(payload | {"span": span})

    def test_open_context_survives_the_worker_input_boundary(self):
        subtask = Plan.from_dict(load("plan_open_chain.json")).subtasks[1]
        message = SubtaskInput("run-open", subtask, "fake-session", Budget(30, 120), "explore")
        restored = SubtaskInput.from_dict(message.to_dict()).subtask
        self.assertEqual(restored.target_domain, "jobs.example.com")
        self.assertIn("sequentially", restored.goal)
        self.assertEqual(restored.criteria[0].parameter, "salary")
        self.assertIn("salary", restored.expected_record_shape)

    def test_optional_open_context_is_checked_without_requiring_a_domain(self):
        payload = load("interpreted_request_open.json")["intents"][0]
        self.assertIsNone(Intent.from_dict(payload | {"target_domain": None}).target_domain)
        for patch in ({"target_domain": 7}, {"goal": []}, {"expected_record_shape": "title"},
                      {"expected_record_shape": [""]}, {"criteria": None}):
            with self.subTest(patch=patch), self.assertRaises(ContractError):
                Intent.from_dict(payload | patch)

    def test_open_defaults_do_not_share_mutable_containers(self):
        first, second = (Plan.from_dict(load("plan.json")) for _ in range(2))
        first.caps["max_subtasks"] = 1
        first.subtasks[0].expected_record_shape.append("salary")
        self.assertEqual(second.caps["max_subtasks"], 4)
        self.assertEqual(second.subtasks[0].expected_record_shape, [])


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
