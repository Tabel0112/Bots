"""Planner checks: one subtask per intent, grouping, and the plan rule check."""

import json
import unittest
from pathlib import Path

from argus import planner
from argus.contracts import (
    ContractError,
    Intent,
    InterpretedRequest,
    ParameterOrigin,
    Plan,
    Subtask,
)

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


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


if __name__ == "__main__":
    unittest.main()
