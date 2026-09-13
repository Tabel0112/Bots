"""P3-CONTRACTS: translation between ARGUS messages and the DOM worker's schemas.

Every worker report here is a real ``Agents.browser_worker.schemas.SubtaskReport``
built from the worker's own models; no network, no browser.
"""

from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from Agents.browser_worker.config import SiteConfig, load_sites
from Agents.browser_worker.policy import validate_request
from Agents.browser_worker.schemas import (
    ACTIONS,
    Action,
    ActionRecord,
    Budgets,
    CheckResult,
    ExtractedRecord,
    Failure,
    FailureCode,
    FieldSource,
    Metrics,
    Observation,
    SubtaskReport,
    SubtaskRequest,
    VisualHandoff,
)
from argus.adapters import worker_contracts as wc
from argus.contracts import (
    ERROR_CODES,
    SCHEMA_VERSION,
    Budget,
    ContractError,
    Plan,
    Subtask,
    SubtaskInput,
    WorkerReport,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN = json.loads((REPO_ROOT / "argus" / "examples" / "plan.json").read_text("utf-8"))
SITES: dict[str, SiteConfig] = load_sites(
    REPO_ROOT / "Agents" / "browser_worker" / "sites.json"
)
NOW = datetime(2026, 9, 13, 20, 0, tzinfo=UTC)


def make_input(
    *,
    operation: str = "search_products",
    site_id: str = "demo-catalog",
    parameters: dict | None = None,
    success_conditions: list[str] | None = None,
    goal: str | None = None,
    budget: Budget | None = None,
    request_id: str | None = "request-1",
    session_handle: str | None = "worker-owned-1",
    kind: str = "registry",
    target_domain: str | None = None,
    expected_record_shape: list[str] | None = None,
) -> SubtaskInput:
    base = Plan.from_dict(PLAN).subtasks[0]
    subtask = Subtask(
        subtask_id=base.subtask_id,
        intent_index=0,
        site_id=site_id,
        operation=operation,
        parameters=dict(base.parameters) if parameters is None else parameters,
        concurrency_group="group-1",
        output_schema_id=base.output_schema_id,
        success_conditions=(
            list(base.success_conditions)
            if success_conditions is None
            else success_conditions
        ),
        goal=goal,
        kind=kind,
        target_domain=target_domain,
        expected_record_shape=list(expected_record_shape or []),
    )
    return SubtaskInput(
        run_id="run-1",
        subtask=subtask,
        session_handle=session_handle,
        budget=budget or Budget(max_actions=30, max_seconds=120),
        mode="explore",
        request_id=request_id,
    )


def make_action(step: str, action_type: str, *, url: str | None = None) -> ActionRecord:
    return ActionRecord(
        step_id=step,
        action_type=action_type,
        arguments=Action(
            observation_id="obs-1",
            target_ref="e1" if action_type != "navigate" else None,
            semantic_target="search field",
            value="headphones" if action_type == "fill" else None,
            value_origin=None,
            url=url,
            expected_change="changed",
            reason="typed the query",
            records=[],
            failure_code=None,
            visual_question=None,
        ),
        started_at=NOW,
        completed_at=NOW,
        before_observation_id="obs-1",
        after_observation_id="obs-2",
        outcome="succeeded",
        expected_change_observed=True,
    )


def make_record(
    title: str, price: float, observation: str = "obs-2"
) -> ExtractedRecord:
    return ExtractedRecord(
        data={
            "title": title,
            "price": price,
            "currency": "USD",
            "url": f"http://127.0.0.1:8765/products/{title.lower().replace(' ', '-')}",
        },
        source_observation_id=observation,
        container_ref="c1",
        field_sources=[FieldSource(field="title", element_ref="e9", attribute="text")],
        retrieved_at=NOW,
    )


def make_observation(observation_id: str) -> Observation:
    return Observation(
        observation_id=observation_id,
        execution_id="exec-1",
        run_id="run-1",
        url="http://127.0.0.1:8765/",
        title="Demo catalog",
        content_hash="abc",
        visible_text="Results",
        elements=[],
    )


def make_report(outcome: str = "succeeded", **overrides) -> SubtaskReport:
    payload = {
        "request_id": "request-1",
        "run_id": "run-1",
        "subtask_id": "subtask-1",
        "browser_backend": "local",
        "reasoning_backend": "gpt-5.4",
        "outcome": outcome,
        "summary": "Two products extracted.",
        "records": [
            make_record("Headphones A", 99.0),
            make_record("Headphones B", 129.5),
        ],
        "validation": [
            CheckResult(check_id="results_ready", passed=True, detail="ok"),
            CheckResult(check_id="condition:0:field_lte", passed=True, detail="ok"),
        ],
        "observations": [make_observation("obs-1"), make_observation("obs-2")],
        "action_trace": [
            make_action("step-1", "navigate", url="http://127.0.0.1:8765/"),
            make_action("step-2", "fill"),
        ],
        "evidence_refs": ["obs-1", "obs-2"],
        "metrics": Metrics(actions=2, model_calls=3, retries=0, elapsed_ms=1234),
        "ghost": {"decision": "explore", "candidate_id": "cand-1"},
        "final_url": "http://127.0.0.1:8765/?q=headphones",
        "session_ref": "steel-abc",
        "session_disposition": "released",
        "limitations": ["worker limitation"],
    }
    payload.update(overrides)
    return SubtaskReport(**payload)


class ToSubtaskRequestTests(unittest.TestCase):
    def test_registry_subtask_maps_to_search_extract_and_passes_worker_validation(self):
        request = wc.to_subtask_request(make_input(), SITES)
        self.assertIsInstance(request, SubtaskRequest)
        self.assertEqual(request.schema_version, "0.2")
        self.assertEqual(request.operation, "search_extract")
        self.assertEqual(request.site_id, "demo-catalog")
        self.assertEqual(
            (request.request_id, request.run_id, request.subtask_id),
            ("request-1", "run-1", "subtask-1"),
        )
        self.assertEqual(request.start_url, SITES["demo-catalog"].start_url)
        self.assertEqual(request.output_schema_id, "product-list.v1")
        self.assertEqual(request.allowed_domains, SITES["demo-catalog"].allowed_domains)
        self.assertEqual(
            request.allowed_url_patterns, SITES["demo-catalog"].allowed_url_patterns
        )
        self.assertEqual(request.allowed_actions, list(ACTIONS))
        self.assertEqual(request.required_evidence, wc.REQUIRED_EVIDENCE)
        self.assertEqual(request.parameters, {"query": "headphones", "max_price": 150})
        self.assertIn("search_products", request.objective)
        self.assertIn("headphones", request.objective)
        # The worker's own validator accepts what we built.
        _task, site = validate_request(request.model_dump(mode="json"), SITES)
        self.assertEqual(site.site_id, "demo-catalog")

    def test_open_subtask_builds_a_valid_unconfigured_worker_request(self):
        subtask_input = make_input(
            operation="open_search",
            site_id="open:example.com",
            parameters={"query": "Toronto condos", "rating": 4.5},
            goal="Find Toronto condos",
            kind="open",
            target_domain="example.com",
            expected_record_shape=["title", "url", "price", "rating"],
        )
        request = wc.to_subtask_request(subtask_input, SITES)
        self.assertEqual(request.operation, "open_search")
        self.assertEqual(request.site_id, "open:example.com")
        self.assertEqual(request.start_url, "https://example.com/")
        self.assertEqual(request.parameters, {"query": "Toronto condos", "rating": 4.5})
        self.assertEqual(
            request.expected_record_shape, ["title", "url", "price", "rating"]
        )
        task, site = validate_request(request.model_dump(mode="json"), SITES)
        self.assertEqual(task, request)
        self.assertTrue(site.open_site)
        self.assertEqual(site.output_schema_id, "open-records.v1")

    def test_session_is_worker_owned_without_a_session_ref(self):
        request = wc.to_subtask_request(make_input(), SITES)
        self.assertEqual(request.session.ownership, "worker")
        self.assertIsNone(request.session.session_ref)
        self.assertFalse(request.session.close_on_finish)

    def test_visual_fallback_follows_uitars_base_url(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("UITARS_BASE_URL", None)
            self.assertFalse(
                wc.to_subtask_request(make_input(), SITES).visual_fallback_available
            )
        with mock.patch.dict(
            os.environ, {"UITARS_BASE_URL": "http://127.0.0.1:8080/v1"}
        ):
            self.assertTrue(
                wc.to_subtask_request(make_input(), SITES).visual_fallback_available
            )

    def test_goal_becomes_the_objective_when_present(self):
        request = wc.to_subtask_request(make_input(goal="Find cheap headphones"), SITES)
        self.assertEqual(request.objective, "Find cheap headphones")

    def test_request_id_falls_back_to_run_id(self):
        request = wc.to_subtask_request(make_input(request_id=None), SITES)
        self.assertEqual(request.request_id, "run-1")

    def test_success_conditions_are_typed_where_possible_and_listed_otherwise(self):
        request, limitations = wc.prepare_request(make_input(), SITES)
        kinds = [
            (c.kind, c.field, c.parameter, c.value) for c in request.success_conditions
        ]
        self.assertIn(("field_lte", "price", "max_price", None), kinds)
        self.assertNotIn("max_records", [kind[0] for kind in kinds])
        self.assertIn(("field_equals", "currency", None, "USD"), kinds)
        self.assertEqual(len(limitations), 4)
        self.assertTrue(any("max_results=5" in text for text in limitations))
        for text in (
            "results present or explicit empty state",
            "every record has title, price, currency, url",
            "query visibly applied",
        ):
            self.assertTrue(any(text in item for item in limitations), text)

    def test_price_condition_without_max_price_is_a_limitation(self):
        request, limitations = wc.prepare_request(
            make_input(parameters={"query": "headphones"}), SITES
        )
        self.assertEqual(request.success_conditions, [])
        self.assertTrue(any("max_price not given" in item for item in limitations))

    def test_budgets_are_ceiled_and_clamped(self):
        request = wc.to_subtask_request(
            make_input(budget=Budget(max_actions=12.2, max_seconds=0.4)), SITES
        )
        self.assertEqual(request.budgets.max_actions, 13)
        self.assertEqual(request.budgets.max_runtime_seconds, 1)
        request = wc.to_subtask_request(
            make_input(budget=Budget(max_actions=5000, max_seconds=99999)), SITES
        )
        bounds = Budgets.model_fields
        self.assertEqual(request.budgets.max_actions, 100)
        self.assertEqual(request.budgets.max_runtime_seconds, 600)
        self.assertIn("max_actions", bounds)

    def test_unknown_operation_is_refused_with_precondition_failed(self):
        with self.assertRaises(ContractError) as caught:
            wc.to_subtask_request(make_input(operation="book_flight"), SITES)
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertIn("book_flight", str(caught.exception))

    def test_unconfigured_site_is_refused_with_precondition_failed(self):
        with self.assertRaises(ContractError) as caught:
            wc.to_subtask_request(make_input(site_id="other-shop"), SITES)
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")

    def test_open_prefix_is_stripped_from_the_site_id(self):
        request = wc.to_subtask_request(make_input(site_id="open:demo-catalog"), SITES)
        self.assertEqual(request.site_id, "demo-catalog")

    def test_non_scalar_parameter_is_refused(self):
        with self.assertRaises(ContractError) as caught:
            wc.to_subtask_request(
                make_input(parameters={"query": "headphones", "max_price": [150]}),
                SITES,
            )
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")

    def test_parameter_the_site_does_not_declare_is_a_limitation(self):
        request, limitations = wc.prepare_request(
            make_input(
                parameters={"query": "headphones", "max_price": 150, "colour": "red"}
            ),
            SITES,
        )
        self.assertNotIn("colour", request.parameters)
        self.assertTrue(any("colour" in item for item in limitations))


class ToWorkerReportTests(unittest.TestCase):
    def test_success_with_two_records(self):
        report, context = wc.to_worker_report(make_report(), make_input())
        self.assertIsInstance(report, WorkerReport)
        self.assertEqual(report.schema_version, SCHEMA_VERSION)
        self.assertEqual(report.worker, "dom")
        self.assertEqual(report.worker_model, "gpt-5.4")
        self.assertEqual(report.request_id, "request-1")
        self.assertEqual(report.subtask_id, "subtask-1")
        self.assertEqual(report.session_handle, "worker-owned-1")
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.summary, "Two products extracted.")
        self.assertEqual(len(report.findings), 2)
        for finding in report.findings:
            self.assertEqual(finding["source_observation_id"], "obs-2")
            self.assertEqual(finding["retrieved_at"], "2026-09-13T20:00:00Z")
            self.assertEqual(finding["currency"], "USD")
        self.assertEqual(report.findings[1]["price"], 129.5)
        self.assertEqual(report.evidence["screenshots"], ["obs-1", "obs-2"])
        self.assertEqual(report.evidence["observations"], ["obs-1", "obs-2"])
        self.assertEqual(
            report.evidence["final_url"], "http://127.0.0.1:8765/?q=headphones"
        )
        self.assertEqual(
            report.metrics,
            {"browser_action_count": 2, "model_call_count": 3, "elapsed_ms": 1234},
        )
        self.assertEqual(report.failures, [])
        self.assertEqual(report.typed_failures, [])
        self.assertEqual(report.evidence["limitations"], ["worker limitation"])
        self.assertEqual(
            context["ghost"], {"decision": "explore", "candidate_id": "cand-1"}
        )
        self.assertEqual(
            [c["check_id"] for c in context["validation"]],
            ["results_ready", "condition:0:field_lte"],
        )
        self.assertEqual(context["limitations"], ["worker limitation"])
        self.assertEqual(context["session_disposition"], "released")
        self.assertEqual(context["final_url"], "http://127.0.0.1:8765/?q=headphones")

    def test_actions_carry_the_keys_the_controller_reads(self):
        report, _ = wc.to_worker_report(make_report(), make_input())
        self.assertEqual(len(report.actions), 2)
        first = report.actions[0]
        for key in (
            "step_id",
            "action",
            "semantic_target",
            "observation_before",
            "observation_after",
            "url",
            "outcome",
            "timestamp",
            "worker_reasoning",
        ):
            self.assertIn(key, first)
        self.assertEqual(first["step_id"], "step-1")
        self.assertEqual(first["action"]["name"], "navigate")
        self.assertEqual(first["action"]["input"]["url"], "http://127.0.0.1:8765/")
        self.assertEqual(first["url"], "http://127.0.0.1:8765/")
        self.assertEqual(first["observation_before"], "obs-1")
        self.assertEqual(first["observation_after"], "obs-2")
        self.assertEqual(first["outcome"], "succeeded")
        self.assertEqual(first["worker_reasoning"], "typed the query")
        self.assertEqual(report.actions[1]["action"]["name"], "fill")

    def test_worker_validation_ghost_and_session_fields_stay_out_of_the_report(self):
        report, _ = wc.to_worker_report(make_report(), make_input())
        payload = json.dumps(report.to_dict())
        self.assertNotIn("steel-abc", payload)
        self.assertNotIn("cand-1", payload)
        self.assertNotIn("results_ready", payload)
        self.assertNotIn("released", payload)
        for key in ("validation", "ghost", "session_ref", "session_disposition"):
            self.assertNotIn(key, report.to_dict())

    def test_needs_visual_maps_to_target_not_found_naming_the_question(self):
        handoff = VisualHandoff(
            run_id="run-1",
            subtask_id="subtask-1",
            url="http://127.0.0.1:8765/",
            observation_id="obs-2",
            intended_operation="click",
            unresolved_target="search button",
            candidates=["e3", "e4"],
            attempted_step_ids=["step-2"],
            reason_code="TARGET_AMBIGUOUS",
            remaining_budget={"actions": 10},
            question="Which control submits the search?",
            available=False,
        )
        report, _ = wc.to_worker_report(
            make_report("needs_visual", records=[], visual_handoff=handoff),
            make_input(),
        )
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(len(report.typed_failures), 1)
        failure = report.typed_failures[0]
        self.assertEqual(failure.code, "TARGET_NOT_FOUND")
        self.assertTrue(failure.retryable)
        self.assertIn("Which control submits the search?", failure.message)
        self.assertEqual(failure.evidence_refs, ["obs-2"])

    def test_inconclusive_maps_to_retryable_validation_failed(self):
        report, context = wc.to_worker_report(
            make_report(
                "inconclusive",
                validation=[
                    CheckResult(check_id="applied:query", passed=False, detail="no")
                ],
            ),
            make_input(),
        )
        self.assertEqual(report.outcome, "failed")
        codes = [(f.code, f.retryable) for f in report.typed_failures]
        self.assertEqual(codes, [("VALIDATION_FAILED", True)])
        self.assertIn("applied:query", report.typed_failures[0].message)
        self.assertFalse(context["validation"][0]["passed"])

    def test_budget_exhausted_maps_to_budget_exceeded(self):
        report, _ = wc.to_worker_report(
            make_report(
                "failed",
                records=[],
                failures=[
                    Failure(
                        code=FailureCode.BUDGET_EXHAUSTED, message="Out of actions."
                    )
                ],
            ),
            make_input(),
        )
        self.assertEqual(report.outcome, "failed")
        self.assertEqual([f.code for f in report.typed_failures], ["BUDGET_EXCEEDED"])
        self.assertEqual(report.typed_failures[0].message, "Out of actions.")
        self.assertEqual(report.failures[0]["code"], "BUDGET_EXHAUSTED")

    def test_cancelled_maps_to_cancelled_once(self):
        report, _ = wc.to_worker_report(
            make_report(
                "cancelled",
                records=[],
                failures=[
                    Failure(
                        code=FailureCode.CANCELLED, message="Cancellation requested."
                    )
                ],
            ),
            make_input(),
        )
        self.assertEqual(report.outcome, "failed")
        self.assertEqual([f.code for f in report.typed_failures], ["CANCELLED"])
        report, _ = wc.to_worker_report(
            make_report("cancelled", records=[]), make_input()
        )
        self.assertEqual([f.code for f in report.typed_failures], ["CANCELLED"])

    def test_every_worker_failure_code_maps_into_error_codes(self):
        for code in FailureCode:
            report, _ = wc.to_worker_report(
                make_report(
                    "failed", records=[], failures=[Failure(code=code, message="x")]
                ),
                make_input(),
            )
            for failure in report.typed_failures:
                self.assertIn(failure.code, ERROR_CODES, code)
        self.assertEqual(
            wc.FAILURE_CODE_MAP["ACTION_REJECTED"], "ACTION_CLASS_NOT_ALLOWED"
        )
        for worker_code in (
            "MODEL_TIMEOUT",
            "MODEL_ERROR",
            "NO_PROGRESS",
            "STALE_OBSERVATION",
        ):
            self.assertEqual(wc.FAILURE_CODE_MAP[worker_code], "EXTRACTION_FAILED")
        self.assertEqual(wc.FAILURE_CODE_MAP["AUTH_REQUIRED"], "AUTH_REQUIRED")
        for code in wc.FAILURE_CODE_MAP.values():
            self.assertIn(code, ERROR_CODES)

    def test_failed_without_a_worker_failure_still_carries_a_typed_failure(self):
        report, _ = wc.to_worker_report(make_report("failed", records=[]), make_input())
        self.assertEqual([f.code for f in report.typed_failures], ["EXTRACTION_FAILED"])

    def test_worker_report_round_trips_through_from_dict(self):
        report, _ = wc.to_worker_report(make_report(), make_input())
        payload = json.loads(json.dumps(report.to_dict()))
        self.assertEqual(WorkerReport.from_dict(payload), report)

    def test_failed_report_names_only_the_exception_class(self):
        report = wc.failed_report(make_input(), "RuntimeError")
        self.assertEqual(report.worker, "dom")
        self.assertEqual(report.schema_version, SCHEMA_VERSION)
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(report.request_id, "request-1")
        self.assertEqual(report.session_handle, "worker-owned-1")
        self.assertEqual(len(report.typed_failures), 1)
        failure = report.typed_failures[0]
        self.assertEqual(failure.code, "EXTRACTION_FAILED")
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.message, "DOM worker subtask failed (RuntimeError).")
        self.assertEqual(WorkerReport.from_dict(report.to_dict()), report)


if __name__ == "__main__":
    unittest.main()


class GhostSummaryTests(unittest.TestCase):
    def test_ghost_block_is_summarised_into_evidence(self):
        summary = wc.ghost_summary(
            {
                "mode": "exploration",
                "candidate": None,
                "candidate_skipped": "The run has no observations.",
                "errors": ["GHOST_UNAVAILABLE"],
                "visual_used": False,
                "secret": "never copied",
            }
        )
        self.assertEqual(summary["mode"], "exploration")
        self.assertEqual(summary["candidate_skipped"], "The run has no observations.")
        self.assertEqual(summary["errors"], ["GHOST_UNAVAILABLE"])
        self.assertNotIn("secret", summary)
        self.assertEqual(wc.ghost_summary(None), {})
