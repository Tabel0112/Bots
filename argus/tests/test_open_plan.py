"""Offline open-plan and suggested-site controller checks."""

from __future__ import annotations

import tempfile
import unittest

from argus.contracts import (
    MAX_OPEN_SUBTASKS,
    ContractError,
    Criterion,
    Intent,
    InterpretedRequest,
    ParameterOrigin,
)
from argus.controller import Controller
from argus.fakes import FakeGhost, FakeModelClient, FakeToolbox, StubModerator
from argus.model_client import ModelResult
from argus.planner import SUCCESS_CONDITIONS
from argus.runtime import open_plan
from argus.site_suggestion import suggest_sites
from argus.store import JsonStore


def open_intent(
    index: int = 0,
    *,
    domain: str | None = "example.com",
    query: str | None = "Toronto condos",
    expected: list[str] | None = None,
    criteria: list[Criterion] | None = None,
) -> Intent:
    parameters = (
        {"query": ParameterOrigin(query, "structured", 0.9)}
        if query is not None
        else {}
    )
    return Intent(
        site_id=f"open:{domain}" if domain else "",
        operation="search",
        parameters=parameters,
        confidence=0.9,
        kind="open",
        target_domain=domain,
        goal=f"Find Toronto condos {index}",
        criteria=list(criteria or []),
        expected_record_shape=list(expected or []),
    )


def interpreted(intents: list[Intent], request_id="request-open") -> InterpretedRequest:
    return InterpretedRequest(
        request_id=request_id,
        raw_text="Give me the best condos in Toronto",
        intents=intents,
        model="fake-interpreter",
        interpreted_at="2026-09-13T00:00:00Z",
    )


class OpenPlanTests(unittest.TestCase):
    def test_one_independent_open_search_per_intent(self):
        plan = open_plan(
            interpreted([open_intent(0), open_intent(1, domain="realtor.ca")]),
            "plan-open",
        )
        self.assertEqual(len(plan.subtasks), 2)
        self.assertEqual(
            [subtask.site_id for subtask in plan.subtasks],
            ["open:example.com", "open:realtor.ca"],
        )
        self.assertTrue(
            all(subtask.operation == "open_search" for subtask in plan.subtasks)
        )
        self.assertTrue(all(subtask.kind == "open" for subtask in plan.subtasks))
        self.assertTrue(all(not subtask.depends_on for subtask in plan.subtasks))
        self.assertEqual(len({s.concurrency_group for s in plan.subtasks}), 2)

    def test_query_falls_back_to_goal_and_criterion_fields_join_default_shape(self):
        rank = Criterion("highest salary", "rank", "salary", None, 0.9)
        plan = open_plan(
            interpreted([open_intent(query=None, criteria=[rank])]), "plan-open"
        )
        subtask = plan.subtasks[0]
        self.assertEqual(subtask.parameters["query"], subtask.goal)
        self.assertEqual(subtask.expected_record_shape, ["title", "url", "salary"])
        self.assertEqual(subtask.output_schema_id, "open-records.v1")
        self.assertEqual(subtask.preferred_tool, "dom")

    def test_open_plan_enforces_the_controller_cap(self):
        request = interpreted(
            [
                open_intent(index, domain=f"site{index}.example.com")
                for index in range(5)
            ]
        )
        with self.assertRaises(ContractError) as caught:
            open_plan(request, "plan-too-large")
        self.assertEqual(caught.exception.code, "PLAN_TOO_LARGE")
        self.assertEqual(MAX_OPEN_SUBTASKS, 4)

    def test_registry_intent_in_a_mixed_plan_keeps_deterministic_defaults(self):
        registry_intent = Intent(
            site_id="demo-catalog",
            operation="search_products",
            parameters={"query": ParameterOrigin("headphones", "structured", 0.9)},
            confidence=0.9,
        )
        plan = open_plan(interpreted([open_intent(), registry_intent]), "plan-mixed")
        registry_subtask = plan.subtasks[1]
        self.assertEqual(registry_subtask.subtask_id, "subtask-1")
        self.assertEqual(registry_subtask.intent_index, 1)
        self.assertEqual(registry_subtask.operation, "search_products")
        self.assertEqual(
            registry_subtask.success_conditions,
            list(SUCCESS_CONDITIONS["search_products"]),
        )
        self.assertEqual(registry_subtask.kind, "registry")

    def test_suggested_site_runs_end_to_end_with_fakes(self):
        model = FakeModelClient(
            ModelResult(
                status="ok",
                parsed={
                    "candidates": [
                        {
                            "domain": "example.com",
                            "reason": "Public read-only site",
                            "confidence": 0.8,
                        }
                    ]
                },
                model="fake-suggester",
            )
        )

        def interpret(_text, request_id):
            request = interpreted(
                [open_intent(domain=None, query="Toronto condos")], request_id
            )
            request.ambiguities = ["intent 0: Which site should ARGUS search?"]
            return suggest_sites(request, model)

        with tempfile.TemporaryDirectory() as root:
            toolbox = FakeToolbox()
            controller = Controller(
                toolbox,
                StubModerator(),
                FakeGhost(),
                JsonStore(root),
                interpret=interpret,
                plan=open_plan,
            )
            result = controller.run(
                "Give me the best condos in Toronto", "request-open", "run-open"
            )
            snapshot = controller.store.run("run-open")

        self.assertEqual(result.status, "succeeded")
        intent = result.interpreted.intents[0]
        self.assertEqual(intent.target_domain, "example.com")
        self.assertEqual(intent.parameters["site_choice"].source, "suggested")
        self.assertEqual(snapshot["plan"]["subtasks"][0]["operation"], "open_search")
        subtask = result.plan.subtasks[0]
        self.assertEqual(toolbox.open_records_for(subtask, ["observation-test"]), [])
        self.assertEqual(model.calls.__len__(), 1)


if __name__ == "__main__":
    unittest.main()


class OpenQueryFromFiltersTest(unittest.TestCase):
    def test_query_comes_from_filter_criteria_not_the_goal(self):
        from argus.contracts import Criterion
        from argus.runtime import open_plan

        filters = [
            Criterion("condos", "filter", None, None, 0.99),
            Criterion("for sale", "filter", None, None, 0.99),
            Criterion("in Toronto", "filter", None, None, 0.99),
        ]
        rank = Criterion("best", "rank", None, None, 0.9)
        plan = open_plan(
            interpreted([open_intent(query=None, criteria=[*filters, rank])]), "plan-q"
        )
        self.assertEqual(
            plan.subtasks[0].parameters["query"], "condos for sale in Toronto"
        )
