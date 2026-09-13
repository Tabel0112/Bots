"""Planner checks: one subtask per intent, grouping, the plan rule check, and
the model-planned open-world path driven through an injected fake client."""

import json
import unittest
from pathlib import Path

from argus import planner
from argus.contracts import (
    ContractError,
    Criterion,
    Intent,
    InterpretedRequest,
    ParameterOrigin,
    Plan,
    Subtask,
)
from argus.fakes import FakeModelClient, FakePlannerClient
from argus.model_client import ModelResult

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


CHAIN = load("plan_open_chain.json")
SALARY = InterpretedRequest.from_dict(load("interpreted_request_open_salary.json"))
OPEN_SHAPE = ["title", "company", "url", "salary", "remote"]


def open_intent(query="software engineering", domain="jobs.example.com", shape=OPEN_SHAPE):
    return Intent(
        site_id=domain, operation="find_jobs",
        parameters={"query": ParameterOrigin(query, "text_span", 0.9, (0, len(query)))},
        confidence=0.9, kind="open", target_domain=domain, goal="Find jobs",
        criteria=[Criterion("10", "limit", 10, None, 1.0)], expected_record_shape=list(shape),
    )


def step(subtask_id, intent_index=0, operation="search", depends_on=(), inputs_from=None, **overrides):
    """One planner step in Plan JSON form (inputs_from as the contract's mapping)."""
    data = {
        "subtask_id": subtask_id, "intent_index": intent_index, "operation": operation,
        "depends_on": list(depends_on), "inputs_from": dict(inputs_from or {}),
        "concurrency_group": f"group-{subtask_id}",
        "success_conditions": ["results present or explicit empty state"],
        "preferred_tool": "dom",
    }
    data.update(overrides)
    return data


def bind(parameter, subtask_id, field):
    return {parameter: {"subtask_id": subtask_id, "field": field}}


def payload(*steps):
    return {"subtasks": list(steps)}


def as_bindings(inputs_from):
    """The Step schema's list form of a contract inputs_from mapping."""
    return [dict(parameter=name, **source) for name, source in inputs_from.items()]


def intent(query, max_price=None, site_id="demo-catalog", operation="search_products"):
    parameters = {"query": ParameterOrigin(query, "text_span", 0.9, (0, len(query)))}
    if max_price is not None:
        parameters["max_price"] = ParameterOrigin(max_price, "text_span", 0.9, (0, 3))
    parameters["currency"] = ParameterOrigin("USD", "default", 1.0)
    return Intent(site_id=site_id, operation=operation, parameters=parameters, confidence=0.9)


def interpreted(*intents, request_id="request-t"):
    return InterpretedRequest(
        request_id=request_id, raw_text="x", intents=list(intents),
        model="test", interpreted_at="2026-09-12T00:00:00Z",
    )


def subtask(subtask_id, depends_on=(), operation="search_products", site_id="demo-catalog"):
    return Subtask(
        subtask_id=subtask_id, intent_index=0, site_id=site_id, operation=operation,
        parameters={"query": "q"}, concurrency_group="g", output_schema_id="product-list.v1",
        depends_on=list(depends_on),
    )


def make_plan(*subtasks):
    return Plan(plan_id="p", request_id="r", subtasks=list(subtasks), created_at="t")


class PlanTests(unittest.TestCase):
    def test_fixture_request_plans_to_fixture_plan(self):
        request = InterpretedRequest.from_dict(load("interpreted_request.json"))
        expected = Plan.from_dict(load("plan.json"))
        built = planner.plan(request, expected.plan_id, created_at=expected.created_at)
        self.assertEqual(built, expected)

    def test_defaults_are_filled_and_preferred_tool_is_dom(self):
        built = planner.plan(interpreted(intent("mice")), "p")
        [st] = built.subtasks
        self.assertEqual(st.parameters, {"query": "mice", "currency": "USD", "max_results": 5})
        self.assertEqual(st.preferred_tool, "dom")
        self.assertEqual(st.output_schema_id, "product-list.v1")
        self.assertEqual(st.success_conditions, list(planner.SUCCESS_CONDITIONS["search_products"]))
        self.assertTrue(built.created_at.endswith("Z"))

    def test_independent_intents_get_distinct_groups_and_no_dependencies(self):
        built = planner.plan(interpreted(intent("mice", 40), intent("keyboards", 120)), "p")
        self.assertEqual([s.subtask_id for s in built.subtasks], ["subtask-1", "subtask-2"])
        self.assertEqual([s.intent_index for s in built.subtasks], [0, 1])
        self.assertEqual([s.depends_on for s in built.subtasks], [[], []])
        self.assertEqual([s.concurrency_group for s in built.subtasks], ["group-1", "group-2"])

    def test_intents_sharing_a_supplied_parameter_share_a_group(self):
        built = planner.plan(
            interpreted(intent("mice", 40), intent("keyboards"), intent("mice", 90)), "p"
        )
        groups = [s.concurrency_group for s in built.subtasks]
        self.assertEqual(groups, ["group-1", "group-2", "group-1"])

    def test_shared_defaults_do_not_group(self):
        """Every search shares currency=USD by default; that must not serialise them."""
        built = planner.plan(interpreted(intent("a"), intent("b")), "p")
        self.assertNotEqual(built.subtasks[0].concurrency_group, built.subtasks[1].concurrency_group)

    def test_plan_is_deterministic(self):
        request = interpreted(intent("a"), intent("b"))
        one = planner.plan(request, "p", created_at="t")
        two = planner.plan(request, "p", created_at="t")
        self.assertEqual(one, two)

    def test_unsupported_operation_is_rejected(self):
        with self.assertRaises(ContractError):
            planner.plan(interpreted(intent("a", operation="delete_products")), "p")
        with self.assertRaises(ContractError):
            planner.plan(interpreted(intent("a", site_id="example-shop")), "p")


class ValidatePlanTests(unittest.TestCase):
    def test_valid_chain_passes(self):
        planner.validate_plan(make_plan(subtask("a"), subtask("b", ["a"]), subtask("c", ["a", "b"])))

    def test_duplicate_ids(self):
        with self.assertRaisesRegex(ContractError, "duplicate"):
            planner.validate_plan(make_plan(subtask("a"), subtask("a")))

    def test_unknown_operation(self):
        with self.assertRaisesRegex(ContractError, "unsupported"):
            planner.validate_plan(make_plan(subtask("a", operation="buy_products")))

    def test_unknown_dependency(self):
        with self.assertRaisesRegex(ContractError, "unknown subtask"):
            planner.validate_plan(make_plan(subtask("a", ["zz"])))

    def test_cycle(self):
        with self.assertRaisesRegex(ContractError, "cycle"):
            planner.validate_plan(make_plan(subtask("a", ["c"]), subtask("b", ["a"]), subtask("c", ["b"])))

    def test_self_dependency_is_a_cycle(self):
        with self.assertRaisesRegex(ContractError, "cycle"):
            planner.validate_plan(make_plan(subtask("a", ["a"])))


class PlanOpenTests(unittest.TestCase):
    """``plan_open`` through ``plan``: the model owns scheduling and nothing else."""

    def plan(self, request, client, **kwargs):
        return planner.plan(request, "plan-open-t", client=client, created_at="t", **kwargs)

    # -- the chain ---------------------------------------------------------

    def test_chain_fixture_binds_result_urls_to_the_search_urls(self):
        client = FakePlannerClient(CHAIN)
        built = self.plan(SALARY, client)
        self.assertEqual(built.planned_by, "model")
        self.assertEqual(len(client.calls), 1)
        search, details = built.subtasks
        self.assertEqual((search.operation, details.operation), ("search", "open_results"))
        self.assertEqual(search.depends_on, [])
        self.assertEqual(details.depends_on, ["subtask-open-search"])
        self.assertEqual(
            details.inputs_from,
            {"result_urls": {"subtask_id": "subtask-open-search", "field": "url"}},
        )
        self.assertEqual(search.inputs_from, {})
        planner.validate_plan(built)
        self.assertEqual(Plan.from_dict(built.to_dict()), built)

    def test_every_open_subtask_carries_the_requests_context_not_the_models(self):
        [intent] = SALARY.intents
        built = self.plan(SALARY, FakePlannerClient(CHAIN))
        for st in built.subtasks:
            with self.subTest(subtask=st.subtask_id):
                self.assertEqual(st.kind, "open")
                self.assertEqual(st.site_id, intent.site_id)
                self.assertEqual(st.target_domain, intent.target_domain)
                self.assertEqual(st.goal, intent.goal)
                self.assertEqual(st.criteria, intent.criteria)
                self.assertEqual(st.expected_record_shape, intent.expected_record_shape)
                self.assertEqual(st.parameters, {"query": "software engineering"})
                self.assertEqual(st.output_schema_id, "open-records.v1")
        # The fixture carried its own context; none of it reached the plan.
        fixture_search, fixture_details = CHAIN["subtasks"]
        self.assertNotEqual(built.subtasks[0].goal, fixture_search["goal"])
        self.assertNotEqual(built.subtasks[1].expected_record_shape, fixture_details["expected_record_shape"])
        self.assertNotIn("max_results", built.subtasks[0].parameters)
        self.assertEqual(built.caps, {"max_subtasks": 4, "max_depth": 3})
        self.assertEqual(built.request_id, SALARY.request_id)

    def test_the_planner_makes_one_bounded_call_with_the_whole_request(self):
        client = FakePlannerClient(CHAIN)
        self.plan(SALARY, client)
        [call] = client.calls
        self.assertEqual(call["max_tokens"], planner.PLAN_MAX_TOKENS)
        self.assertEqual(json.loads(call["user"]), SALARY.to_dict())
        self.assertTrue(hasattr(call["output_model"], "model_validate"))
        self.assertIn("Never login, pay, or submit", call["system"])

    # -- model outcomes ------------------------------------------------------

    def test_a_refusal_is_model_refused(self):
        client = FakePlannerClient(CHAIN, status="refusal")
        with self.assertRaises(ContractError) as caught:
            self.plan(SALARY, client)
        self.assertEqual(caught.exception.code, "MODEL_REFUSED")
        self.assertEqual(len(client.calls), 1)

    def test_truncated_and_invalid_are_extraction_failed(self):
        for status in ("truncated", "invalid"):
            with self.subTest(status=status):
                with self.assertRaises(ContractError) as caught:
                    self.plan(SALARY, FakePlannerClient(CHAIN, status=status))
                self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    def test_model_output_cannot_change_controller_owned_fields(self):
        """Any field beyond scheduling is rejected by the strict schema, so the
        model cannot alter target_domain, goal, criteria, expected_record_shape,
        parameters or caps even if a backend let one through."""
        clean = step("s")
        tampered = {
            "target_domain": dict(clean, target_domain="evil.example.org"),
            "goal": dict(clean, goal="log in and pay"),
            "criteria": dict(clean, criteria=[]),
            "expected_record_shape": dict(clean, expected_record_shape=["password"]),
            "parameters": dict(clean, parameters={"query": "anything"}),
            "kind": dict(clean, kind="registry"),
            "output_schema_id": dict(clean, output_schema_id="x"),
            "site_id": dict(clean, site_id="demo-catalog"),
        }
        cases = {name: payload(raw) for name, raw in tampered.items()}
        cases["caps"] = dict(payload(clean), caps={"max_subtasks": 99, "max_depth": 9})
        cases["planned_by"] = dict(payload(clean), planned_by="deterministic")
        for name, parsed in cases.items():
            with self.subTest(field=name):
                parsed = json.loads(json.dumps(parsed))
                for raw in parsed["subtasks"]:
                    raw["inputs_from"] = as_bindings(raw["inputs_from"])
                client = FakeModelClient(ModelResult(status="ok", parsed=parsed))
                with self.assertRaises(ContractError) as caught:
                    self.plan(interpreted(open_intent()), client)
                self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    def test_a_wrong_operation_is_extraction_failed(self):
        client = FakePlannerClient(payload(step("s", operation="submit_form")))
        with self.assertRaises(ContractError) as caught:
            self.plan(interpreted(open_intent()), client)
        self.assertEqual(caught.exception.code, "EXTRACTION_FAILED")

    # -- rule checks after the call -----------------------------------------

    def test_a_dependency_cycle_is_rejected(self):
        client = FakePlannerClient(payload(step("a", depends_on=["b"]), step("b", depends_on=["a"])))
        with self.assertRaisesRegex(ContractError, "cycle"):
            self.plan(interpreted(open_intent()), client)

    def test_five_steps_are_plan_too_large(self):
        client = FakePlannerClient(payload(*(step(f"s{i}") for i in range(5))))
        with self.assertRaises(ContractError) as caught:
            self.plan(interpreted(open_intent()), client)
        self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")

    def test_four_steps_are_within_the_budget(self):
        built = self.plan(interpreted(open_intent()), FakePlannerClient(payload(*(step(f"s{i}") for i in range(4)))))
        self.assertEqual(len(built.subtasks), 4)

    def test_depth_four_is_plan_too_large(self):
        client = FakePlannerClient(payload(
            step("a"), step("b", depends_on=["a"]), step("c", depends_on=["b"]), step("d", depends_on=["c"]),
        ))
        with self.assertRaises(ContractError) as caught:
            self.plan(interpreted(open_intent()), client)
        self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")

    def test_depth_three_is_allowed(self):
        client = FakePlannerClient(payload(
            step("a"), step("b", depends_on=["a"]), step("c", depends_on=["b"]),
        ))
        self.assertEqual(len(self.plan(interpreted(open_intent()), client).subtasks), 3)

    def test_intent_index_must_name_an_accepted_open_intent(self):
        mixed = interpreted(intent("mice", 40), open_intent())
        for index in (0, 2, -1):
            with self.subTest(intent_index=index):
                client = FakePlannerClient(payload(step("s", intent_index=index)))
                with self.assertRaisesRegex(ContractError, "accepted open intent"):
                    self.plan(mixed, client)

    def test_a_binding_cannot_overwrite_an_accepted_parameter(self):
        client = FakePlannerClient(payload(
            step("a"), step("b", depends_on=["a"], inputs_from=bind("query", "a", "url")),
        ))
        with self.assertRaisesRegex(ContractError, "cannot overwrite"):
            self.plan(interpreted(open_intent()), client)

    def test_a_binding_field_must_be_in_the_source_record_shape(self):
        client = FakePlannerClient(payload(
            step("a"), step("b", depends_on=["a"], inputs_from=bind("result_urls", "a", "rating")),
        ))
        with self.assertRaisesRegex(ContractError, "expected_record_shape"):
            self.plan(interpreted(open_intent()), client)

    def test_an_omitted_open_intent_is_rejected(self):
        client = FakePlannerClient(payload(step("s", intent_index=0)))
        with self.assertRaisesRegex(ContractError, "omitted"):
            self.plan(interpreted(open_intent(), open_intent(query="data")), client)

    def test_open_steps_require_success_conditions(self):
        client = FakePlannerClient(payload(step("s", success_conditions=[])))
        with self.assertRaisesRegex(ContractError, "success conditions"):
            self.plan(interpreted(open_intent()), client)

    # -- mixed and registry-only requests -----------------------------------

    def test_a_mixed_request_plans_the_registry_intent_deterministically(self):
        client = FakePlannerClient(payload(step("s", intent_index=1)))
        built = self.plan(interpreted(intent("mice", 40), open_intent()), client)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(built.planned_by, "model")
        registry_task, open_task = built.subtasks
        self.assertEqual(registry_task.subtask_id, "registry-1")
        self.assertEqual(registry_task.kind, "registry")
        self.assertEqual(registry_task.intent_index, 0)
        self.assertEqual(registry_task.operation, "search_products")
        self.assertEqual(registry_task.parameters, {"query": "mice", "max_price": 40, "currency": "USD", "max_results": 5})
        self.assertEqual(registry_task.success_conditions, list(planner.SUCCESS_CONDITIONS["search_products"]))
        self.assertEqual(open_task.intent_index, 1)
        self.assertEqual(open_task.kind, "open")
        self.assertNotEqual(registry_task.concurrency_group, open_task.concurrency_group)

    def test_a_registry_only_request_never_touches_the_client(self):
        client = FakePlannerClient(CHAIN)
        built = self.plan(interpreted(intent("mice", 40), intent("keyboards")), client)
        self.assertEqual(client.calls, [])
        self.assertEqual(built.planned_by, "deterministic")
        self.assertEqual(len(built.subtasks), 2)


if __name__ == "__main__":
    unittest.main()
