"""P3-DOM: the ``DomToolbox`` facade over a fake DOM worker.

The fake worker returns real ``SubtaskReport`` objects, raises, or sleeps past
the subtask budget; no browser, model or network is involved.
"""

from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import ClassVar

from Agents.browser_worker.config import load_sites
from Agents.browser_worker.schemas import (
    Failure,
    FailureCode,
    SubtaskReport,
    SubtaskRequest,
)
from argus import interfaces
from argus.adapters.dom_toolbox import DomToolbox
from argus.contracts import ERROR_CODES, Budget, ContractError, WorkerReport
from argus.tests.test_worker_contracts import make_input, make_report

REPO_ROOT = Path(__file__).resolve().parents[2]
SITES = load_sites(REPO_ROOT / "Agents" / "browser_worker" / "sites.json")


class FakeWorker:
    """Records the request it received and answers from a script."""

    instances: ClassVar[list[FakeWorker]] = []

    def __init__(self, *, report=None, error=None, sleep=0.0, **kwargs):
        self.kwargs = kwargs
        self.report = report
        self.error = error
        self.sleep = sleep
        self.request = None
        self.cancel = None
        self.cancelled = False
        FakeWorker.instances.append(self)

    async def run(self, raw, cancel=None, on_status=None):
        self.request = raw
        self.cancel = cancel
        if self.sleep:
            waiter = asyncio.create_task(cancel.wait())
            try:
                await asyncio.wait_for(waiter, timeout=self.sleep)
                self.cancelled = True
                return make_report(
                    "cancelled",
                    records=[],
                    failures=[
                        Failure(
                            code=FailureCode.CANCELLED,
                            message="Cancellation requested.",
                        )
                    ],
                )
            except TimeoutError:
                pass
        if self.error is not None:
            raise self.error
        return self.report if self.report is not None else make_report()


def factory(**script):
    """A ``worker_factory`` producing ``FakeWorker`` with a fixed script."""

    def build(**kwargs):
        return FakeWorker(**script, **kwargs)

    return build


class DomToolboxTests(unittest.TestCase):
    def setUp(self):
        FakeWorker.instances.clear()

    def test_satisfies_the_toolbox_protocol(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        self.assertIsInstance(toolbox, interfaces.Toolbox)

    def test_sessions_are_virtual_and_recorded(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        first = toolbox.open_session("demo-catalog")
        second = toolbox.open_session("demo-catalog")
        self.assertEqual((first, second), ("worker-owned-1", "worker-owned-2"))
        toolbox.close_session(first)
        toolbox.close_session("never-opened")  # not an error tonight
        self.assertEqual(
            toolbox.calls,
            [
                ("open_session", "demo-catalog", "worker-owned-1"),
                ("open_session", "demo-catalog", "worker-owned-2"),
                ("close_session", "worker-owned-1"),
                ("close_session", "never-opened"),
            ],
        )
        self.assertEqual(
            toolbox.sessions, {"worker-owned-1": False, "worker-owned-2": True}
        )

    def test_run_subtask_translates_both_ways_and_keeps_the_context(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        handle = toolbox.open_session("demo-catalog")
        subtask_input = make_input(session_handle=handle)
        report = toolbox.run_subtask(subtask_input)

        self.assertIsInstance(report, WorkerReport)
        self.assertEqual(report.outcome, "succeeded")
        self.assertEqual(report.worker, "dom")
        self.assertEqual(report.request_id, subtask_input.request_id)
        self.assertEqual(report.subtask_id, subtask_input.subtask.subtask_id)
        self.assertEqual(report.session_handle, handle)
        self.assertEqual(len(report.findings), 2)
        self.assertEqual(report.findings[0]["source_observation_id"], "obs-2")

        worker = FakeWorker.instances[-1]
        self.assertIsInstance(worker.request, SubtaskRequest)
        self.assertEqual(worker.request.operation, "search_extract")
        self.assertEqual(worker.request.session.ownership, "worker")
        self.assertIsInstance(worker.cancel, asyncio.Event)
        self.assertFalse(worker.cancel.is_set())
        self.assertIs(worker.kwargs["sites"], toolbox.sites)

        context = toolbox.context_for("run-1", "subtask-1")
        self.assertIsNotNone(context)
        self.assertEqual(
            context["ghost"], {"decision": "explore", "candidate_id": "cand-1"}
        )
        self.assertEqual(
            [c["check_id"] for c in context["validation"]],
            ["results_ready", "condition:0:field_lte"],
        )
        self.assertEqual(context["session_disposition"], "released")
        self.assertIn("worker limitation", context["limitations"])
        self.assertTrue(
            any("query visibly applied" in item for item in context["limitations"])
        )
        self.assertIsNone(toolbox.context_for("run-1", "other"))
        self.assertEqual(
            toolbox.calls_named("run_subtask"),
            [("run_subtask", "subtask-1", "explore", "dom", handle)],
        )
        payload = json.dumps(report.to_dict())
        for leaked in ("cand-1", "results_ready", "steel-abc"):
            self.assertNotIn(leaked, payload)

    def test_settings_are_passed_to_the_worker_factory(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES, settings="settings")
        toolbox.run_subtask(make_input())
        self.assertEqual(FakeWorker.instances[-1].kwargs["settings"], "settings")

    def test_unknown_operation_is_refused_before_the_worker_is_constructed(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        with self.assertRaises(ContractError) as caught:
            toolbox.run_subtask(make_input(operation="book_flight"))
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertEqual(FakeWorker.instances, [])
        self.assertIsNone(toolbox.context_for("run-1", "subtask-1"))

    def test_unconfigured_site_is_refused_before_the_worker_is_constructed(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        with self.assertRaises(ContractError) as caught:
            toolbox.run_subtask(make_input(site_id="other-shop"))
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertEqual(FakeWorker.instances, [])

    def test_worker_exception_becomes_a_failed_report_naming_only_the_class(self):
        secret = "sk-provider-secret-text"
        toolbox = DomToolbox(
            worker_factory=factory(error=RuntimeError(secret)), sites=SITES
        )
        report = toolbox.run_subtask(make_input())
        self.assertEqual(report.outcome, "failed")
        self.assertEqual(report.worker, "dom")
        self.assertEqual([f.code for f in report.typed_failures], ["EXTRACTION_FAILED"])
        self.assertIn("RuntimeError", report.typed_failures[0].message)
        self.assertNotIn(secret, json.dumps(report.to_dict()))
        context = toolbox.context_for("run-1", "subtask-1")
        self.assertIn("worker raised RuntimeError", context["limitations"])

    def test_timeout_yields_budget_exceeded_and_sets_the_cancel_event(self):
        toolbox = DomToolbox(
            worker_factory=factory(sleep=5.0), sites=SITES, grace_seconds=0.05
        )
        subtask_input = make_input(budget=Budget(max_actions=10, max_seconds=0.1))
        report = toolbox.run_subtask(subtask_input)
        self.assertEqual(report.outcome, "failed")
        self.assertEqual([f.code for f in report.typed_failures], ["BUDGET_EXCEEDED"])
        self.assertIn(report.typed_failures[0].code, ERROR_CODES)
        self.assertEqual(report.session_handle, subtask_input.session_handle)
        worker = FakeWorker.instances[-1]
        self.assertTrue(worker.cancel.is_set())
        self.assertIn(
            "worker timed out", toolbox.context_for("run-1", "subtask-1")["limitations"]
        )

    def test_cancel_sets_the_event_for_a_running_subtask(self):
        toolbox = DomToolbox(
            worker_factory=factory(sleep=5.0), sites=SITES, grace_seconds=5.0
        )
        results: dict[str, object] = {}

        import threading

        def run():
            results["report"] = toolbox.run_subtask(
                make_input(budget=Budget(max_actions=10, max_seconds=5))
            )

        thread = threading.Thread(target=run)
        thread.start()
        deadline = 100
        while deadline and not (
            FakeWorker.instances and FakeWorker.instances[-1].cancel
        ):
            thread.join(0.02)
            deadline -= 1
        self.assertFalse(toolbox.cancel("run-1", "nope"))
        self.assertTrue(toolbox.cancel("run-1", "subtask-1"))
        thread.join(3.0)
        self.assertFalse(thread.is_alive())
        report = results["report"]
        self.assertTrue(FakeWorker.instances[-1].cancelled)
        self.assertEqual([f.code for f in report.typed_failures], ["CANCELLED"])
        self.assertIn(("cancel", "run-1", "subtask-1"), toolbox.calls)
        self.assertFalse(toolbox.cancel("run-1", "subtask-1"))

    def test_malformed_worker_report_becomes_a_failed_report(self):
        toolbox = DomToolbox(worker_factory=factory(report="not a report"), sites=SITES)
        report = toolbox.run_subtask(make_input())
        self.assertEqual(report.outcome, "failed")
        self.assertEqual([f.code for f in report.typed_failures], ["EXTRACTION_FAILED"])

    def test_needs_visual_from_the_worker_reaches_the_controller_as_a_failure(self):
        toolbox = DomToolbox(
            worker_factory=factory(report=make_report("needs_visual", records=[])),
            sites=SITES,
        )
        report = toolbox.run_subtask(make_input())
        self.assertEqual(report.outcome, "failed")
        self.assertEqual([f.code for f in report.typed_failures], ["TARGET_NOT_FOUND"])

    def test_interpretation_methods_name_the_missing_session_manager(self):
        toolbox = DomToolbox(worker_factory=factory(), sites=SITES)
        handle = toolbox.open_session("demo-catalog")
        for call in (
            lambda: toolbox.observe(handle),
            lambda: toolbox.dom_interpret(handle, "is the query applied?"),
            lambda: toolbox.vision_interpret(handle, "is the query applied?"),
        ):
            with self.assertRaises(ContractError) as caught:
                call()
            self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
            self.assertIn("P3-SESSION", str(caught.exception))
        self.assertEqual(
            [call[0] for call in toolbox.calls],
            ["open_session", "observe", "dom_interpret", "vision_interpret"],
        )

    def test_module_imports_without_constructing_the_real_worker(self):
        toolbox = DomToolbox()
        self.assertEqual(toolbox.calls, [])
        self.assertIn("demo-catalog", toolbox.sites)
        report = SubtaskReport(request_id="r", run_id="r", subtask_id="s")
        self.assertEqual(report.outcome, "failed")


if __name__ == "__main__":
    unittest.main()


class SessionRetryTests(unittest.TestCase):
    def test_session_start_failure_is_retried_once_with_a_fresh_worker(self):
        import os
        from unittest.mock import patch

        from argus.adapters.dom_toolbox import DomToolbox

        failing = make_report(
            "failed",
            records=[],
            failures=[
                Failure(
                    code=FailureCode.SESSION_UNAVAILABLE,
                    message="Could not initialize the browser session.",
                )
            ],
        )
        failing.action_trace = []
        scripts = [{"report": failing}, {"report": make_report()}]

        def build(**kwargs):
            return FakeWorker(**scripts.pop(0), **kwargs)

        with patch.dict(os.environ, {"ARGUS_SESSION_RETRY_DELAY": "0"}):
            toolbox = DomToolbox(worker_factory=build, sites=SITES)
            result = toolbox.run_subtask(make_input())
        self.assertEqual(result.outcome, "succeeded")
        self.assertIn(("session_retry", "subtask-1", 1), [c[:3] for c in toolbox.calls])
        self.assertIn(
            "browser session start failed once and was retried",
            result.evidence["limitations"],
        )

    def test_session_start_failure_is_not_retried_when_disabled(self):
        import os
        from unittest.mock import patch

        from argus.adapters.dom_toolbox import DomToolbox

        failing = make_report(
            "failed",
            records=[],
            failures=[
                Failure(
                    code=FailureCode.SESSION_UNAVAILABLE,
                    message="Could not initialize the browser session.",
                )
            ],
        )
        failing.action_trace = []
        with patch.dict(os.environ, {"ARGUS_SESSION_RETRY": "0"}):
            toolbox = DomToolbox(worker_factory=factory(report=failing), sites=SITES)
            result = toolbox.run_subtask(make_input())
        self.assertEqual(result.outcome, "failed")
        self.assertFalse([c for c in toolbox.calls if c[0] == "session_retry"])
