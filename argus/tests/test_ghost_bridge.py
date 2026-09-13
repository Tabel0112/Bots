"""Tests for :mod:`argus.adapters.ghost_bridge` (ARGUS-3, P3-GHOST).

Offline: the lookup preview runs against a ``WorkflowStore`` on a temporary
SQLite file, the worker side is a tiny fake toolbox exposing ``context_for``,
and no HTTP client is ever constructed.
"""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from argus import interfaces
from argus.adapters import ghost_bridge as bridge_module
from argus.adapters.ghost_bridge import GhostBridge
from argus.contracts import Subtask, WorkerReport
from argus.store import JsonStore
from ghostapi.api.storage import WorkflowStore

RUN = "run-1"
OBS = "observation-000.png"


# ----------------------------------------------------------------- builders


class FakeToolbox:
    """Only what the bridge needs: ``context_for(run_id, subtask_id)``."""

    def __init__(self, contexts=None):
        self.contexts = dict(contexts or {})
        self.calls = []

    def context_for(self, run_id, subtask_id):
        self.calls.append((run_id, subtask_id))
        return self.contexts.get((run_id, subtask_id))


def worker_context(
    *,
    checks=None,
    ghost=None,
    final_url="https://demo-catalog.invalid/search?q=headphones",
):
    return {
        "validation": [
            {
                "check_id": check_id,
                "passed": passed,
                "detail": f"{check_id} {'ok' if passed else 'failed'}",
                "evidence_refs": [OBS],
            }
            for check_id, passed in (checks or {}).items()
        ],
        "ghost": dict(ghost or {"mode": "exploration", "candidate": None}),
        "limitations": [],
        "session_disposition": "released",
        "final_url": final_url,
    }


def subtask(subtask_id="subtask-1", **overrides):
    data = {
        "subtask_id": subtask_id,
        "intent_index": 0,
        "site_id": "demo-catalog",
        "operation": "search_products",
        "parameters": {"query": "headphones", "max_price": 150},
        "concurrency_group": "g",
        "output_schema_id": "product-list.v1",
    }
    data.update(overrides)
    return Subtask(**data)


def record(i, observation=OBS):
    return {
        "title": f"Item {i}",
        "price": 10.0 * i,
        "currency": "USD",
        "url": f"https://demo-catalog.invalid/products/{i}",
        "source_observation_id": observation,
    }


def report(subtask_id="subtask-1", request_id=RUN, findings=None):
    return WorkerReport.from_dict(
        {
            "schema_version": "0.1-provisional",
            "worker": "dom",
            "worker_model": "fake",
            "request_id": request_id,
            "subtask_id": subtask_id,
            "subtask": "search_products",
            "outcome": "succeeded",
            "summary": "two results",
            "findings": [record(1), record(2)] if findings is None else findings,
            "actions": [{"step_id": "step-000"}],
            "evidence": {"screenshots": [OBS]},
            "metrics": {"browser_action_count": 2},
            "failures": [],
        }
    )


def context_of(subtask_id="subtask-1", run_id=RUN, empty_state=False):
    return {
        "run_id": run_id,
        "subtask_id": subtask_id,
        "evidence": {"screenshots": [OBS]},
        "empty_state": empty_state,
    }


def qualified_definition():
    """A ``0.2`` definition without a compatibility key, compatible with
    :func:`subtask`'s parameters and the bridge's allowed actions."""
    return {
        "schema_version": "0.2",
        "site_id": "demo-catalog",
        "operation": "search_products",
        "description": "search the demo catalog",
        "input_schema": {
            "query": {"type": "string", "required": True},
            "max_price": {"type": "number", "required": False},
        },
        "required_outputs": ["title", "price"],
        "allowed_actions": list(bridge_module.ALLOWED_ACTIONS),
        "preconditions": [],
        "compatibility_key": None,
        "parameters": {"query": "keyboards", "max_price": 90},
        "steps": [
            {
                "step_id": "s1",
                "action": "navigate",
                "value": "https://demo-catalog.invalid/",
                "expected_state": {},
            },
            {
                "step_id": "s2",
                "action": "fill",
                "target": {"strategy": "semantic", "role": "searchbox", "label": "q"},
                "value": {"parameter": "query"},
                "expected_state": {},
            },
            {"step_id": "s3", "action": "extract", "value": None, "expected_state": {}},
        ],
        "output_schema_id": "product-list.v1",
        "validator_id": "product-list.v1",
    }


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = Path(self.tmp.name) / "ghost.sqlite3"
        self.workflows = WorkflowStore(self.database)

    def bridge(self, toolbox=None, **kwargs):
        kwargs.setdefault("store", self.workflows)
        return GhostBridge(toolbox or FakeToolbox(), **kwargs)


# ---------------------------------------------------------------- protocol


class ProtocolTest(BridgeTestCase):
    def test_is_a_ghost(self):
        self.assertIsInstance(self.bridge(), interfaces.Ghost)

    def test_requires_context_for(self):
        with self.assertRaises(TypeError):
            GhostBridge(object(), store=self.workflows)

    def test_module_never_writes_to_the_registry(self):
        source = Path(bridge_module.__file__).read_text(encoding="utf-8")
        for forbidden in (
            "create_candidate",
            "submit_qualification",
            "report_run",
            "save_candidate",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIsNone(re.search(r"\.event\(", source))
        self.assertNotIn("ghostapi.client", source)

    def test_database_path_default_and_override(self):
        bridge = GhostBridge(FakeToolbox(), database_path=self.database)
        self.assertEqual(bridge.database_path, self.database)
        default = GhostBridge(FakeToolbox()).database_path
        self.assertEqual(default.name, "ghostapi.sqlite3")
        self.assertEqual(default.parent.name, "ghostapi")


# ------------------------------------------------------------------- match


class MatchTest(BridgeTestCase):
    def test_empty_registry_explores_with_the_no_workflow_preview(self):
        result = self.bridge().match(subtask(), [])
        self.assertEqual(
            result,
            {
                "decision": "explore",
                "skill": None,
                "reason": "worker-resolved: no compatible qualified workflow",
            },
        )
        self.assertEqual(self.workflows.activity(), [])

    def test_qualified_workflow_is_previewed_but_still_explored(self):
        skill_id, version = self.workflows.save_candidate(qualified_definition())
        with self.workflows.connection() as db:
            db.execute(
                "UPDATE workflow_versions SET status='qualified' WHERE skill_id=?",
                (skill_id,),
            )
        result = self.bridge().match(subtask(), [{"skill_id": "ignored"}])
        self.assertEqual(result["decision"], "explore")
        self.assertIsNone(result["skill"])
        self.assertEqual(
            result["reason"],
            f"worker-resolved: qualified workflow {skill_id} v{version} available",
        )
        self.assertEqual(self.workflows.activity(), [])

    def test_open_site_prefix_is_stripped_for_the_lookup(self):
        seen = {}
        original = bridge_module.GhostBridge._lookup_request

        def spy(self_, st):
            request = original(self_, st)
            seen["request"] = request
            return request

        bridge_module.GhostBridge._lookup_request = spy
        try:
            open_task = subtask(
                site_id="open:jobs.example.com",
                operation="search",
                kind="open",
                target_domain="jobs.example.com",
                expected_record_shape=["title", "url"],
            )
            result = self.bridge().match(open_task, [])
        finally:
            bridge_module.GhostBridge._lookup_request = original
        self.assertEqual(result["decision"], "explore")
        self.assertEqual(seen["request"].site_id, "jobs.example.com")
        self.assertEqual(seen["request"].required_outputs, ["title", "url"])
        self.assertEqual(seen["request"].schema_version, "0.2")
        self.assertEqual(seen["request"].allowed_actions, bridge_module.ALLOWED_ACTIONS)

    def test_lookup_exception_explores_with_the_class_name(self):
        class BrokenStore:
            def qualified_candidates(self, site_id, operation):
                raise RuntimeError("sqlite said: disk I/O error at /secret/path")

        result = self.bridge(store=BrokenStore()).match(subtask(), [])
        self.assertEqual(
            result,
            {
                "decision": "explore",
                "skill": None,
                "reason": "worker-resolved: lookup preview unavailable (RuntimeError)",
            },
        )

    def test_invalid_site_id_is_a_preview_failure_not_a_crash(self):
        result = self.bridge().match(subtask(site_id="bad site!"), [])
        self.assertEqual(result["decision"], "explore")
        self.assertIn("lookup preview unavailable (", result["reason"])

    def test_store_is_opened_lazily_at_the_database_path(self):
        path = Path(self.tmp.name) / "lazy" / "ghost.sqlite3"
        bridge = GhostBridge(FakeToolbox(), database_path=path)
        self.assertFalse(path.exists())
        bridge.match(subtask(), [])
        self.assertTrue(path.exists())


# ---------------------------------------------------------------- validate


class ValidateTest(BridgeTestCase):
    def test_passed_when_binding_and_worker_checks_pass(self):
        toolbox = FakeToolbox(
            {(RUN, "subtask-1"): worker_context(checks={"records_match_schema": True})}
        )
        result = self.bridge(toolbox).validate(
            subtask(), [record(1), record(2)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["failed_checks"], [])
        self.assertEqual(result["scope"], "worker verifier + argus binding")
        self.assertEqual(
            result["checks"],
            {
                "records_are_objects": True,
                "records_present": True,
                "records_cite_observations": True,
                "report_bound_to_subtask": True,
                "report_bound_to_run": True,
                "worker:records_match_schema": True,
            },
        )
        self.assertEqual(toolbox.calls, [(RUN, "subtask-1")])

    def test_failed_when_a_worker_check_failed(self):
        toolbox = FakeToolbox(
            {
                (RUN, "subtask-1"): worker_context(
                    checks={"records_match_schema": True, "price_within_limit": False}
                )
            }
        )
        result = self.bridge(toolbox).validate(
            subtask(), [record(1)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["worker:price_within_limit"])

    def test_failed_when_a_record_cites_an_unknown_observation(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        result = self.bridge(toolbox).validate(
            subtask(),
            [record(1), record(2, observation="observation-999.png")],
            [OBS],
            report_context=context_of(),
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["records_cite_observations"])

    def test_failed_when_a_record_is_not_an_object(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        result = self.bridge(toolbox).validate(
            subtask(), [record(1), "not a record"], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "failed")
        self.assertIn("records_are_objects", result["failed_checks"])

    def test_failed_when_the_context_names_another_subtask_or_run(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        bridge = self.bridge(toolbox)
        other_subtask = bridge.validate(
            subtask(), [record(1)], [OBS], report_context=context_of("subtask-2")
        )
        self.assertEqual(other_subtask["status"], "failed")
        self.assertIn("report_bound_to_subtask", other_subtask["failed_checks"])

        bridge.set_run(RUN)
        other_run = bridge.validate(
            subtask(), [record(1)], [OBS], report_context=context_of(run_id="run-9")
        )
        self.assertEqual(other_run["status"], "failed")
        self.assertIn("report_bound_to_run", other_run["failed_checks"])

    def test_inconclusive_when_no_worker_context(self):
        result = self.bridge(FakeToolbox()).validate(
            subtask(), [record(1)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "worker verification unavailable")
        self.assertEqual(result["failed_checks"], [])
        self.assertNotIn("worker:ok", result["checks"])

    def test_inconclusive_when_the_worker_ran_no_checks(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={})})
        result = self.bridge(toolbox).validate(
            subtask(), [record(1)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "worker verification unavailable")

    def test_inconclusive_when_records_are_empty_without_an_empty_state(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        result = self.bridge(toolbox).validate(
            subtask(), [], [OBS], report_context=context_of(empty_state=False)
        )
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["reason"], "no records and no evidenced empty state")
        self.assertEqual(result["failed_checks"], ["records_present"])

    def test_passed_when_records_are_empty_with_an_evidenced_empty_state(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        result = self.bridge(toolbox).validate(
            subtask(), [], [OBS], report_context=context_of(empty_state=True)
        )
        self.assertEqual(result["status"], "passed")
        self.assertIs(result["checks"]["records_present"], True)

    def test_three_argument_form_uses_set_run_for_the_context(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(checks={"ok": True})})
        bridge = self.bridge(toolbox)
        without_run = bridge.validate(subtask(), [record(1)], [OBS])
        self.assertEqual(without_run["status"], "inconclusive")
        self.assertNotIn("report_bound_to_run", without_run["checks"])

        bridge.set_run(RUN)
        with_run = bridge.validate(subtask(), [record(1)], [OBS])
        self.assertEqual(with_run["status"], "passed")
        self.assertEqual(with_run["checks"]["worker:ok"], True)

    def test_context_run_id_wins_over_set_run(self):
        toolbox = FakeToolbox(
            {("run-ctx", "subtask-1"): worker_context(checks={"ok": True})}
        )
        bridge = self.bridge(toolbox)
        bridge.set_run("run-ctx")
        result = bridge.validate(
            subtask(), [record(1)], [OBS], report_context=context_of(run_id="run-ctx")
        )
        self.assertEqual(result["status"], "passed")
        self.assertEqual(toolbox.calls[0], ("run-ctx", "subtask-1"))

    def test_open_subtask_final_url_must_stay_on_the_target_domain(self):
        open_task = subtask(
            site_id="open:jobs.example.com",
            operation="search",
            kind="open",
            target_domain="jobs.example.com",
        )
        on_domain = FakeToolbox(
            {
                (RUN, "subtask-1"): worker_context(
                    checks={"ok": True}, final_url="https://www.jobs.example.com/q"
                )
            }
        )
        self.assertEqual(
            self.bridge(on_domain)
            .validate(open_task, [record(1)], [OBS], report_context=context_of())
            .get("status"),
            "passed",
        )
        off_domain = FakeToolbox(
            {
                (RUN, "subtask-1"): worker_context(
                    checks={"ok": True}, final_url="https://evil.example.net/q"
                )
            }
        )
        result = self.bridge(off_domain).validate(
            open_task, [record(1)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_checks"], ["final_url_on_target_domain"])

    def test_malformed_worker_checks_never_pass(self):
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context()})
        toolbox.contexts[(RUN, "subtask-1")]["validation"] = [
            {"check_id": "string_passed", "passed": "yes"},
            {"detail": "no name"},
        ]
        result = self.bridge(toolbox).validate(
            subtask(), [record(1)], [OBS], report_context=context_of()
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            result["failed_checks"], ["worker:string_passed", "worker:check-1"]
        )


# ----------------------------------------------------------------- compile


def saved_candidate_ghost():
    return {
        "mode": "exploration",
        "candidate": {
            "schema_version": "0.2",
            "skill_id": "demo-catalog.search_products",
            "version": 3,
            "status": "candidate",
        },
        "errors": [],
        "visual_used": False,
    }


class CompileTest(BridgeTestCase):
    def test_saved_candidate_becomes_a_reference_the_json_store_accepts(self):
        toolbox = FakeToolbox(
            {(RUN, "subtask-1"): worker_context(ghost=saved_candidate_ghost())}
        )
        skill = self.bridge(toolbox).compile(report(), subtask())
        self.assertIsNotNone(skill)
        self.assertEqual(skill["skill_id"], "demo-catalog.search_products")
        self.assertEqual(skill["version"], 3)
        self.assertEqual(skill["status"], "candidate")
        self.assertEqual(skill["site_id"], "demo-catalog")
        self.assertEqual(skill["operation"], "search_products")
        self.assertEqual(skill["source_run_ids"], [RUN])
        self.assertEqual(skill["reference"]["ghost"], saved_candidate_ghost())
        self.assertNotIn("session_handle", skill)

        store = JsonStore(Path(self.tmp.name) / "argus-store")
        store.save_skill(skill)
        saved = store.skills()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["skill_id"], "demo-catalog.search_products")
        self.assertEqual(saved[0]["version"], 3)
        self.assertEqual(saved[0]["reference"]["ghost"]["candidate"]["version"], 3)

    def test_candidate_skipped_yields_none(self):
        ghost = {
            "mode": "exploration",
            "candidate": None,
            "errors": [],
            "visual_used": False,
            "candidate_skipped": "The run has no observations.",
        }
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(ghost=ghost)})
        self.assertIsNone(self.bridge(toolbox).compile(report(), subtask()))

    def test_reuse_yields_none(self):
        ghost = {
            "mode": "reuse",
            "candidate": None,
            "errors": [],
            "visual_used": False,
            "workflow": {"skill_id": "demo-catalog.search_products", "version": 1},
            "run_id": "ghost-run-1",
        }
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(ghost=ghost)})
        self.assertIsNone(self.bridge(toolbox).compile(report(), subtask()))

    def test_registry_error_and_missing_context_yield_none(self):
        ghost = {
            "mode": "exploration",
            "candidate": None,
            "errors": ["GHOST_UNAVAILABLE"],
            "visual_used": False,
        }
        toolbox = FakeToolbox({(RUN, "subtask-1"): worker_context(ghost=ghost)})
        bridge = self.bridge(toolbox)
        self.assertIsNone(bridge.compile(report(), subtask()))
        self.assertIsNone(bridge.compile(report(request_id="run-unknown"), subtask()))
        self.assertIsNone(self.bridge(FakeToolbox()).compile(report(), subtask()))

    def test_set_run_resolves_the_context_when_request_id_differs(self):
        toolbox = FakeToolbox(
            {("run-real", "subtask-1"): worker_context(ghost=saved_candidate_ghost())}
        )
        bridge = self.bridge(toolbox)
        self.assertIsNone(bridge.compile(report(request_id="request-1"), subtask()))
        bridge.set_run("run-real")
        skill = bridge.compile(report(request_id="request-1"), subtask())
        self.assertIsNotNone(skill)
        self.assertEqual(skill["source_run_ids"], ["request-1"])
        self.assertEqual(toolbox.calls[-1], ("run-real", "subtask-1"))


if __name__ == "__main__":
    unittest.main()
