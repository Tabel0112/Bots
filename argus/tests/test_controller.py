"""Controller checks against small in-test fakes of the interfaces.

The fakes here implement the protocols in ``argus.interfaces`` directly so the
controller is exercised through its injection points only; ``argus.fakes`` is
deliberately not imported.
"""

import json
import threading
import time
import unittest
from pathlib import Path

from argus import interfaces
from argus.contracts import (
    Claim,
    FinalAnswer,
    GateDecision,
    Intent,
    InterpretedRequest,
    ModeratorDecision,
    ParameterOrigin,
    Plan,
    Subtask,
    TypedError,
    WorkerReport,
)
from argus.controller import TERMINAL, Controller

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
REPORT_FIXTURE = json.loads((EXAMPLES / "worker_report.json").read_text())
SECRET = "provider said: quota exhausted for key sk-live-123"


# ----------------------------------------------------------------- builders

def intent(query, max_price=None):
    parameters = {"query": ParameterOrigin(query, "text_span", 0.9, (0, len(query)))}
    if max_price is not None:
        parameters["max_price"] = ParameterOrigin(max_price, "text_span", 0.9, (0, 3))
    return Intent("demo-catalog", "search_products", parameters, 0.9)


def interpreted(*intents, request_id="request-t"):
    return InterpretedRequest(
        request_id=request_id, raw_text="find things", intents=list(intents or [intent("headphones", 150)]),
        model="test", interpreted_at="2026-09-12T00:00:00Z",
    )


def subtask(subtask_id, depends_on=(), group=None, query="headphones"):
    return Subtask(
        subtask_id=subtask_id, intent_index=0, site_id="demo-catalog",
        operation="search_products", parameters={"query": query, "currency": "USD", "max_results": 5},
        concurrency_group=group or f"group-{subtask_id}", output_schema_id="product-list.v1",
        depends_on=list(depends_on),
        success_conditions=["results present or explicit empty state"],
    )


def plan_of(*subtasks):
    return lambda _interpreted, plan_id: Plan(
        plan_id=plan_id, request_id="request-t", subtasks=list(subtasks), created_at="t"
    )


def accept_gate(_interpreted):
    return GateDecision("accept", "G0", "ok")


def record(i, observation="observation-000.png"):
    return {"title": f"Item {i}", "price": 10.0 * i, "currency": "USD",
            "url": f"https://demo-catalog.invalid/products/{i}", "source_observation_id": observation}


def report_for(subtask_input, *, outcome="succeeded", records=None, actions=1,
               typed_failures=(), screenshots=("observation-000.png",)):
    data = json.loads(json.dumps(REPORT_FIXTURE))
    data.update(
        request_id=subtask_input.run_id, subtask_id=subtask_input.subtask.subtask_id,
        subtask=subtask_input.subtask.operation, outcome=outcome,
        findings=records if records is not None else [record(1), record(2)],
    )
    data["evidence"]["screenshots"] = list(screenshots)
    data["metrics"]["browser_action_count"] = actions
    report = WorkerReport.from_dict(data)
    report.session_handle = subtask_input.session_handle
    report.typed_failures = list(typed_failures)
    return report


# -------------------------------------------------------------------- fakes

class FakeToolbox:
    """Records sessions and calls; ``behaviour(subtask_input)`` decides the report."""

    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda inp: report_for(inp))
        self.lock = threading.Lock()
        self.open = set()
        self.closed = []
        self.opened = []
        self.inputs = []
        self.open_during_run = []
        self.observe_calls = []
        self.interpret_calls = []
        self.timeline = []
        self.counter = 0
        self.active = 0
        self.max_active = 0

    def open_session(self, site_id):
        with self.lock:
            self.counter += 1
            handle = f"session-{self.counter}"
            self.open.add(handle)
            self.opened.append(handle)
            self.timeline.append(("open", handle))
        return handle

    def close_session(self, handle):
        with self.lock:
            if handle not in self.open:
                raise AssertionError(f"{handle} closed twice or never opened")
            self.open.remove(handle)
            self.closed.append(handle)
            self.timeline.append(("close", handle))

    def run_subtask(self, subtask_input):
        with self.lock:
            self.inputs.append(subtask_input)
            self.open_during_run.append(set(self.open))
            assert subtask_input.session_handle in self.open, "worker got a closed session"
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            return self.behaviour(subtask_input)
        finally:
            with self.lock:
                self.active -= 1

    def observe(self, handle):
        with self.lock:
            self.observe_calls.append(handle)
            return f"observation-verify-{len(self.observe_calls)}"

    def dom_interpret(self, handle, question):
        self.interpret_calls.append(("dom", handle, question))
        return "confirmed"

    def vision_interpret(self, handle, question):
        self.interpret_calls.append(("vision", handle, question))
        return "confirmed"


class FakeModerator:
    """``script`` maps subtask_id to the assess decisions to return in order;
    the last one repeats.  Unscripted subtasks are accepted."""

    def __init__(self, script=None, claims_without_evidence=False, reconcile_decision="merged"):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.claims_without_evidence = claims_without_evidence
        self.reconcile_decision = reconcile_decision
        self.assess_calls = []
        self.reconcile_calls = []
        self.synthesize_calls = []
        self.lock = threading.Lock()

    def assess_report(self, subtask, report, success_conditions):
        with self.lock:
            self.assess_calls.append((subtask.subtask_id, report, list(success_conditions)))
            queue = self.script.get(subtask.subtask_id)
            decision = "accept" if not queue else (queue.pop(0) if len(queue) > 1 else queue[0])
        return ModeratorDecision("assess", decision, f"scripted {decision}", ["observation-000.png"])

    def reconcile(self, plan, reports):
        self.reconcile_calls.append((plan, reports))
        findings = [r for report in reports for r in report.findings]
        return ModeratorDecision(
            "reconcile", self.reconcile_decision, "merged in test",
            next_action={"findings": findings, "gaps": ["second page not checked"], "conflicts": []},
        )

    def synthesize(self, interpreted, records, validation, evidence, failures):
        self.synthesize_calls.append((interpreted, records, validation, evidence, failures))
        claims = [
            Claim(f"{r['title']} costs {r['price']}", [] if self.claims_without_evidence else [r["source_observation_id"]])
            for r in records
        ]
        text = f"{len(records)} records; validation {validation.get('status')}; {len(failures)} failure(s)"
        return FinalAnswer(text=text, claims=claims, records=list(records), failures=list(failures))


class FakeGhost:
    def __init__(self, validation="passed"):
        self.validation = validation
        self.validate_calls = []
        self.compile_calls = []
        self.match_calls = []

    def match(self, subtask, skills):
        self.match_calls.append((subtask.subtask_id, skills))
        return {"decision": "explore", "reason": "no qualified skills", "skill": None}

    def validate(self, subtask, records, evidence):
        self.validate_calls.append((subtask.subtask_id, records, evidence))
        return {"status": self.validation, "checks": [{"check_id": "c1", "status": self.validation}]}

    def compile(self, report, subtask):
        self.compile_calls.append((report, subtask))
        return {"skill_id": f"skill-{subtask.subtask_id}", "status": "candidate",
                "operation": subtask.operation}


class MemoryStore:
    def __init__(self):
        self.runs = {}
        self.event_log = {}
        self.reports = {}
        self.saved_skills = []
        self.index = {}
        self.lock = threading.Lock()

    def create_run(self, run_id, request_id, snapshot):
        with self.lock:
            self.runs[run_id] = snapshot
            self.event_log[run_id] = []
            self.reports[run_id] = {}
            self.index[request_id] = run_id

    def save_snapshot(self, run_id, snapshot):
        with self.lock:
            self.runs[run_id] = snapshot

    def append_event(self, run_id, event):
        with self.lock:
            self.event_log[run_id].append(event.to_dict())

    def save_report(self, run_id, report):
        with self.lock:
            self.reports[run_id][report.subtask_id] = report.to_dict()

    def save_evidence_file(self, run_id, observation_id, source_path):
        return source_path

    def save_skill(self, skill):
        self.saved_skills.append(skill)

    def skills(self):
        return list(self.saved_skills)

    def run(self, run_id):
        return self.runs[run_id]

    def events(self, run_id):
        return list(self.event_log[run_id])

    def run_id_for_request(self, request_id):
        return self.index.get(request_id)


class ObservingModerator(FakeModerator):
    def __init__(self, decision="continue", **kwargs):
        super().__init__(**kwargs)
        self.observe_decision = decision
        self.observed = []

    def observe_progress(self, snapshot, event):
        self.observed.append(event.type)
        if event.type == "subtask_started":
            return ModeratorDecision("observe", self.observe_decision, "observer says so")
        return ModeratorDecision("observe", "continue", "fine")


# -------------------------------------------------------------------- tests

class ControllerTestCase(unittest.TestCase):
    def build(self, toolbox=None, moderator=None, ghost=None, plan=None, gate=accept_gate, **kwargs):
        self.toolbox = toolbox or FakeToolbox()
        self.moderator = moderator or FakeModerator()
        self.ghost = ghost or FakeGhost()
        self.store = MemoryStore()
        self.controller = Controller(
            self.toolbox, self.moderator, self.ghost, self.store, gate=gate, plan=plan, **kwargs
        )
        return self.controller

    def execute(self, request=None, **kwargs):
        result = self.controller.run(request or interpreted(), "request-t", **kwargs)
        self.events = self.store.events(result.run_id)
        self.assert_event_stream_is_sound(result)
        self.assert_no_secret_leaked(result)
        return result

    def assert_event_stream_is_sound(self, result):
        sequences = [e["sequence"] for e in self.events]
        self.assertEqual(sequences, list(range(1, len(sequences) + 1)), "sequence not strictly increasing")
        self.assertTrue(all(e["run_id"] == result.run_id for e in self.events))
        terminal = [e for e in self.events if e["stage"] in TERMINAL]
        self.assertEqual(len(terminal), 1, "exactly one terminal event")
        self.assertIs(terminal[0], self.events[-1])
        self.assertEqual(terminal[0]["type"], f"run_{terminal[0]['stage']}")
        self.assertEqual(terminal[0]["data"]["status"], result.status)
        self.assertEqual(self.store.run(result.run_id), result.to_dict(), "result written with terminal event")
        self.assertEqual(self.toolbox.open, set(), "every session closed")
        closes = [i for i, e in enumerate(self.events) if e["type"] == "session_closed"]
        self.assertTrue(all(i < len(self.events) - 1 for i in closes), "sessions closed before terminal event")
        for report in result.reports:
            self.assertIsNone(report.session_handle)
        for saved in self.store.reports[result.run_id].values():
            self.assertIsNone(saved["session_handle"])

    def assert_no_secret_leaked(self, result):
        blob = json.dumps(result.to_dict()) + json.dumps(self.events)
        self.assertNotIn(SECRET, blob)
        self.assertNotIn("Traceback", blob)
        for handle in self.toolbox.opened:
            self.assertNotIn(handle, json.dumps(self.events), "session handle in events")


class SingleSubtaskTests(ControllerTestCase):
    def test_success_end_to_end(self):
        self.build()
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertIsNone(result.error)
        self.assertEqual(len(result.plan.subtasks), 1)
        self.assertEqual(len(result.reports), 1)
        self.assertEqual(result.validation["status"], "passed")
        self.assertEqual(len(result.answer.claims), 2)
        self.assertEqual(result.metrics["subtasks"], {"subtask-1": "accepted"})
        self.assertEqual(self.toolbox.closed, ["session-1"])
        self.assertEqual(len(self.moderator.reconcile_calls), 0, "no reconcile for one subtask")
        self.assertEqual(len(self.ghost.compile_calls), 1)
        self.assertEqual(self.store.saved_skills[0]["status"], "candidate")
        stages = [e["message"] for e in self.events if e["type"] == "stage_changed"]
        self.assertEqual(stages, ["interpreting", "gating", "planning", "matching", "dispatching",
                                  "validating", "synthesizing", "publishing"])
        [inp] = self.toolbox.inputs
        self.assertEqual(inp.budget.max_actions, 30)
        self.assertEqual(inp.mode, "explore")
        self.assertEqual(inp.session_handle, "session-1")

    def test_clarify_ends_as_needs_input_without_executing(self):
        self.build(gate=lambda _i: GateDecision("clarify", "G4", "query missing", ["Which product?"]))
        result = self.execute()
        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.error.code, "NEEDS_INPUT")
        self.assertEqual(result.gate.questions, ["Which product?"])
        self.assertEqual(self.toolbox.inputs, [])
        self.assertIsNone(result.plan)

    def test_reject_ends_as_invalid_input(self):
        self.build(gate=lambda _i: GateDecision("reject", "G2", "unknown site"))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "INVALID_INPUT")
        self.assertIn("G2", result.error.message)

    def test_moderator_fail_carries_the_typed_failure_unchanged(self):
        failure = TypedError("AUTH_REQUIRED", "login wall", False, "step-000", ["observation-000.png"])
        toolbox = FakeToolbox(lambda inp: report_for(inp, outcome="failed", records=[], typed_failures=[failure]))
        self.build(toolbox, FakeModerator({"subtask-1": ["fail"]}))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, failure)
        self.assertEqual(result.answer.failures, [failure], "synthesize still explains the failure")
        self.assertEqual(self.ghost.validate_calls, [])
        self.assertEqual(self.ghost.compile_calls, [])

    def test_progress_observer_can_stop_a_subtask(self):
        self.build(moderator=ObservingModerator("stop_subtask"))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "CANCELLED")
        self.assertEqual(self.toolbox.inputs, [], "stopped before the worker ran")
        self.assertIn("subtask_started", self.moderator.observed)

    def test_progress_observer_flag_forces_one_verification(self):
        self.build(moderator=ObservingModerator("flag"))
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(self.toolbox.observe_calls), 1)


class ConcurrencyTests(ControllerTestCase):
    def test_two_independent_subtasks_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=2)

        def behaviour(inp):
            barrier.wait()  # times out unless both workers are inside run_subtask together
            return report_for(inp)

        self.build(FakeToolbox(behaviour))
        result = self.execute(interpreted(intent("mice", 40), intent("keyboards", 120)))
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(self.toolbox.inputs), 2)
        self.assertEqual(self.toolbox.max_active, 2, "both workers ran at the same time")
        self.assertTrue(any(len(open_set) == 2 for open_set in self.toolbox.open_during_run),
                        "both sessions open before either closed")
        first_close = self.toolbox.timeline.index(("close", self.toolbox.closed[0]))
        opens_before = [t for t in self.toolbox.timeline[:first_close] if t[0] == "open"]
        self.assertEqual(len(opens_before), 2)
        self.assertEqual(len(self.moderator.reconcile_calls), 1)
        self.assertEqual(len(result.answer.records), 4, "reconciled findings used")
        self.assertIn("gap: second page not checked", result.answer.unverified)
        self.assertEqual(len(self.ghost.validate_calls), 2)

    def test_same_group_subtasks_are_serialised(self):
        self.build(plan=plan_of(subtask("a", group="g"), subtask("b", group="g")))
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.toolbox.max_active, 1, "same group never runs together")
        self.assertEqual(len(self.toolbox.inputs), 2)

    def test_max_concurrency_bounds_open_sessions(self):
        self.build(plan=plan_of(subtask("a"), subtask("b"), subtask("c")), max_concurrency=2)
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertLessEqual(self.toolbox.max_active, 2)
        self.assertEqual(len(self.toolbox.inputs), 3)

    def test_dependent_subtask_is_cancelled_when_dependency_fails(self):
        failure = TypedError("PRECONDITION_FAILED", "catalog offline", False)
        toolbox = FakeToolbox(lambda inp: report_for(
            inp, outcome="failed" if inp.subtask.subtask_id == "a" else "succeeded",
            typed_failures=[failure] if inp.subtask.subtask_id == "a" else [],
        ))
        self.build(toolbox, FakeModerator({"a": ["fail"]}),
                   plan=plan_of(subtask("a"), subtask("b", ["a"]), subtask("c")))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, failure)
        self.assertEqual(result.metrics["subtasks"], {"a": "failed", "b": "cancelled", "c": "accepted"})
        self.assertEqual(sorted(i.subtask.subtask_id for i in self.toolbox.inputs), ["a", "c"])
        cancelled = [e for e in self.events if e["type"] == "subtask_cancelled"]
        self.assertEqual(cancelled[0]["data"]["subtask_id"], "b")
        codes = sorted(f.code for f in result.answer.failures)
        self.assertEqual(codes, ["CANCELLED", "PRECONDITION_FAILED"])
        self.assertEqual(len(result.answer.records), 2, "independent sibling's records kept")

    def test_dependent_subtask_starts_only_after_dependency_accepted(self):
        order = []
        toolbox = FakeToolbox(lambda inp: (order.append(inp.subtask.subtask_id), report_for(inp))[1])
        self.build(toolbox, plan=plan_of(subtask("a"), subtask("b", ["a"])))
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(order, ["a", "b"])


class IntakeTests(ControllerTestCase):
    def test_verify_then_accept(self):
        self.build(moderator=FakeModerator({"subtask-1": ["verify", "accept"]}))
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(self.toolbox.observe_calls, ["session-1"])
        [(tool, handle, question)] = self.toolbox.interpret_calls
        self.assertEqual((tool, handle), ("dom", "session-1"))
        self.assertIn("results present or explicit empty state", question)
        self.assertEqual(len(self.moderator.assess_calls), 2)
        verification = self.moderator.assess_calls[1][1].evidence["verifications"][0]
        self.assertEqual(verification["observation_id"], "observation-verify-1")
        self.assertIn("observation-verify-1", result.validation["evidence"])

    def test_verify_is_capped_at_one(self):
        self.build(moderator=FakeModerator({"subtask-1": ["verify", "verify", "accept"]}))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("verification cap", result.error.message)
        self.assertEqual(len(self.toolbox.observe_calls), 1, "verified exactly once")
        self.assertEqual(len(self.moderator.assess_calls), 2)

    def test_retry_other_path_reissues_once_on_the_same_session(self):
        calls = []

        def behaviour(inp):
            calls.append(inp)
            if inp.subtask.preferred_tool == "dom":
                return report_for(inp, outcome="failed", records=[],
                                  typed_failures=[TypedError("TARGET_NOT_FOUND", "no search box", True)])
            return report_for(inp)

        self.build(FakeToolbox(behaviour), FakeModerator({"subtask-1": ["retry_other_path", "accept"]}))
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertEqual([c.subtask.preferred_tool for c in calls], ["dom", "vision"])
        self.assertEqual({c.session_handle for c in calls}, {"session-1"})
        self.assertEqual(self.toolbox.opened, ["session-1"], "no second session for the retry")
        self.assertEqual(result.metrics["browser_action_count"], 2)

    def test_retry_other_path_is_capped_at_one(self):
        failure = TypedError("TARGET_NOT_FOUND", "no search box", True)
        toolbox = FakeToolbox(lambda inp: report_for(inp, outcome="failed", records=[], typed_failures=[failure]))
        self.build(toolbox, FakeModerator({"subtask-1": ["retry_other_path"]}))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, failure, "the report's typed failure is carried")
        self.assertEqual(len(self.toolbox.inputs), 2, "one retry, then the cap")
        self.assertEqual(len(self.moderator.assess_calls), 2)

    def test_schema_invalid_report_fails_the_subtask(self):
        self.build(FakeToolbox(lambda inp: {"schema_version": "x", "outcome": "succeeded"}))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("schema", result.error.message)

    def test_report_for_another_subtask_is_rejected(self):
        def behaviour(inp):
            report = report_for(inp)
            report.subtask_id = "someone-else"
            return report
        self.build(FakeToolbox(behaviour))
        result = self.execute()
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")


class ValidationAndSynthesisTests(ControllerTestCase):
    def test_validation_failure_cannot_be_overridden(self):
        self.build(ghost=FakeGhost(validation="failed"))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "VALIDATION_FAILED")
        self.assertEqual(result.validation["status"], "failed")
        self.assertEqual(len(self.moderator.synthesize_calls), 1, "synthesize explains the failure")
        self.assertIsNotNone(result.answer)
        self.assertEqual(self.moderator.synthesize_calls[0][2]["status"], "failed")
        self.assertTrue(any("not validated" in u for u in result.answer.unverified))
        self.assertEqual(self.ghost.compile_calls, [], "no compile after failed validation")
        self.assertEqual(self.store.saved_skills, [])

    def test_inconclusive_validation_is_not_success(self):
        self.build(ghost=FakeGhost(validation="inconclusive"))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "VALIDATION_FAILED")
        self.assertTrue(result.error.retryable)
        self.assertEqual(self.ghost.compile_calls, [])

    def test_claim_without_evidence_fails_the_run(self):
        self.build(moderator=FakeModerator(claims_without_evidence=True))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("no evidence reference", result.error.message)
        self.assertEqual(self.ghost.compile_calls, [])

    def test_compile_failure_does_not_change_the_result(self):
        class BrokenCompileGhost(FakeGhost):
            def compile(self, report, subtask):
                raise RuntimeError(SECRET)
        self.build(ghost=BrokenCompileGhost())
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        failed = [e for e in self.events if e["type"] == "compile_failed"]
        self.assertEqual(failed[0]["data"]["exception"], "RuntimeError")


class BudgetTests(ControllerTestCase):
    def test_subtask_action_budget_breach(self):
        self.build(FakeToolbox(lambda inp: report_for(inp, actions=31)), max_actions=30)
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "BUDGET_EXCEEDED")
        self.assertEqual(result.error.step_id, "subtask-1")
        self.assertEqual(self.moderator.assess_calls, [], "breach decided by the controller, not the moderator")
        self.assertEqual(result.metrics["subtasks"], {"subtask-1": "failed"})

    def test_retry_shares_the_action_budget(self):
        toolbox = FakeToolbox(lambda inp: report_for(
            inp, actions=20, outcome="failed", records=[],
            typed_failures=[TypedError("TARGET_NOT_FOUND", "nope", True)]))
        self.build(toolbox, FakeModerator({"subtask-1": ["retry_other_path", "accept"]}), max_actions=30)
        result = self.execute()
        self.assertEqual(result.error.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(self.toolbox.inputs), 2)
        self.assertEqual(self.toolbox.inputs[1].budget.max_actions, 10, "retry gets the remainder")

    def test_run_wall_clock_breach(self):
        def slow(inp):
            time.sleep(0.15)
            return report_for(inp)
        self.build(FakeToolbox(slow), max_seconds=0.05)
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "BUDGET_EXCEEDED")
        self.assertIsNone(result.error.step_id, "whole-run breach")
        self.assertTrue(any(e["type"] == "budget_exceeded" for e in self.events))
        self.assertEqual(self.moderator.synthesize_calls, [])

    def test_run_wall_clock_breach_cancels_pending_subtasks(self):
        def slow(inp):
            time.sleep(0.15)
            return report_for(inp)
        self.build(FakeToolbox(slow), plan=plan_of(subtask("a"), subtask("b"), subtask("c")),
                   max_seconds=0.05, max_concurrency=1)
        result = self.execute()
        self.assertEqual(result.error.code, "BUDGET_EXCEEDED")
        self.assertEqual(result.metrics["subtasks"]["c"], "cancelled")
        self.assertEqual(len(self.toolbox.inputs), 1)


class RobustnessTests(ControllerTestCase):
    def test_sessions_closed_when_run_subtask_raises(self):
        def boom(inp):
            raise RuntimeError(SECRET)
        self.build(FakeToolbox(boom))
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("RuntimeError", result.error.message)
        self.assertEqual(self.toolbox.closed, ["session-1"])

    def test_sessions_closed_when_moderator_raises(self):
        class ExplodingModerator(FakeModerator):
            def assess_report(self, subtask, report, success_conditions):
                raise ValueError(SECRET)
        self.build(moderator=ExplodingModerator())
        result = self.execute()
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("ValueError", result.error.message)
        self.assertEqual(self.toolbox.closed, ["session-1"])

    def test_sessions_closed_when_a_later_stage_raises(self):
        class ExplodingGhost(FakeGhost):
            def validate(self, subtask, records, evidence):
                raise KeyError(SECRET)
        self.build(ghost=ExplodingGhost())
        result = self.execute()
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("KeyError", result.error.message)
        self.assertEqual(self.toolbox.closed, ["session-1"])

    def test_close_failure_does_not_break_the_terminal_result(self):
        class StickyToolbox(FakeToolbox):
            def close_session(self, handle):
                super().close_session(handle)
                raise OSError(SECRET)
        self.build(StickyToolbox())
        result = self.execute()
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(any(e["type"] == "session_close_failed" for e in self.events))

    def test_invalid_moderator_decision_stage_fails_the_subtask(self):
        class WrongStageModerator(FakeModerator):
            def assess_report(self, subtask, report, success_conditions):
                return ModeratorDecision("reconcile", "merged", "wrong stage")
        self.build(moderator=WrongStageModerator())
        result = self.execute()
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("moderator decision invalid", result.error.message)

    def test_cancel_before_dispatch(self):
        def cancel_on_start(_i):
            self.controller.cancel("run-x")  # unknown run: no effect
            self.controller.cancel("run-cancel-me")
            return GateDecision("accept", "G0", "ok")
        self.build(gate=cancel_on_start)
        result = self.execute(run_id="run-cancel-me")
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.error.code, "CANCELLED")
        self.assertEqual(self.toolbox.inputs, [])

    def test_missing_interpreter_module_is_a_typed_failure(self):
        self.build(interpret=None)
        original = __import__("importlib").import_module

        def fake_import(name, *args, **kwargs):
            if name == "argus.interpreter":
                raise ImportError("no module")
            return original(name, *args, **kwargs)

        import argus.controller as controller_module
        controller_module.importlib.import_module = fake_import
        try:
            result = self.execute("find headphones")
        finally:
            controller_module.importlib.import_module = original
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error.code, "EXTRACTION_FAILED")
        self.assertIn("argus.interpreter", result.error.message)

    def test_injected_interpreter_is_used_for_text(self):
        seen = []

        def interpret(text, request_id):
            seen.append((text, request_id))
            return interpreted()
        self.build(interpret=interpret)
        result = self.execute("find headphones under $150")
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(seen, [("find headphones under $150", "request-t")])

    def test_events_sequence_and_single_terminal_on_every_path(self):
        """The harness asserts the stream on every run above; check it explicitly here."""
        self.build()
        result = self.execute()
        types = [e["type"] for e in self.events]
        self.assertEqual(types[0], "run_created")
        self.assertEqual(types[-1], "run_completed")
        self.assertEqual(types.count("run_completed"), 1)
        self.assertEqual(self.store.run_id_for_request("request-t"), result.run_id)


if __name__ == "__main__":
    unittest.main()
