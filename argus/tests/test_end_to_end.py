"""Phase 2 and 1b: whole runs through the real controller and the shipped fakes.

Every test here builds its request from one of the fixtures in
``argus/examples/`` and runs :class:`argus.controller.Controller` with
:class:`~argus.fakes.FakeToolbox`, :class:`~argus.fakes.StubModerator`,
:class:`~argus.fakes.FakeGhost` and a :class:`~argus.store.JsonStore` in a
temporary directory.  Nothing calls the network: stage 1 is supplied as an
already-interpreted request, which is exactly what ``python -m argus
--interpreted FILE`` does, and open-world planning is supplied by
:class:`~argus.fakes.FakePlannerClient`, which is what ``--plan-fixture`` does.

Registry path: a single-subtask search, a compound request whose two subtasks
really do run at the same time, a clarify that executes nothing, a scripted
``target_not_found`` that retries on the other interpretation tool and then
succeeds, a scripted ``auth_required`` that fails honestly, a thin-evidence
report that the controller verifies before it is accepted, and a validation
failure that the moderator cannot override.

Open-world path: a vague ranking that stops at the gate, the clarified salary
request planned as a two-subtask chain whose second subtask is fed the first
one's URLs, a private target rejected before anything opens, and a plan over the
four-subtask cap rejected before anything opens.

Each dispatching run also asserts what the store holds afterwards: ``run.json``,
``events.jsonl`` and one report per subtask.
"""

import copy
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from functools import partial
from pathlib import Path
from unittest import mock

from argus import planner
from argus.__main__ import (
    EXIT_NO_TOOLBOX,
    EXIT_OK,
    EXIT_RUN_NOT_SUCCEEDED,
    EXIT_USAGE,
    main,
)
from argus.contracts import InterpretedRequest, Note
from argus.controller import TERMINAL, Controller
from argus.fakes import FakeGhost, FakePlannerClient, FakeToolbox, StubModerator
from argus.gate import RANK_QUESTION
from argus.store import JsonStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIXTURE_PATH = EXAMPLES / "interpreted_request.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

#: The open-world fixtures: the ambiguous "best 10 jobs" request, the same
#: request once "best" has been clarified, and the chain a planner returns for it.
OPEN_PATH = EXAMPLES / "interpreted_request_open.json"
OPEN_FIXTURE = json.loads(OPEN_PATH.read_text(encoding="utf-8"))
SALARY_PATH = EXAMPLES / "interpreted_request_open_salary.json"
SALARY_FIXTURE = json.loads(SALARY_PATH.read_text(encoding="utf-8"))
CHAIN_PATH = EXAMPLES / "plan_open_chain.json"
CHAIN = json.loads(CHAIN_PATH.read_text(encoding="utf-8"))

#: The clarification the gate's S4 asks for the fixture's "best".
S4_QUESTION = RANK_QUESTION.format(text="best")

#: Text of the compound request, so the spans below are real spans of it.
COMPOUND_TEXT = (
    "Find headphones under $150 and keyboards under $100 in the demo catalog"
)


# --------------------------------------------------------------- requests


def single(request_id="request-demo-1"):
    """The shipped fixture: one intent, headphones under $150."""
    data = copy.deepcopy(FIXTURE)
    data["request_id"] = request_id
    return InterpretedRequest.from_dict(data)


def _span(needle):
    start = COMPOUND_TEXT.index(needle)
    return [start, start + len(needle)]


def compound(request_id="request-demo-compound"):
    """Two intents with different queries, so the planner gives distinct groups."""
    data = copy.deepcopy(FIXTURE)
    data["request_id"] = request_id
    data["raw_text"] = COMPOUND_TEXT
    first = data["intents"][0]
    first["parameters"]["query"].update(value="headphones", span=_span("headphones"))
    first["parameters"]["max_price"].update(value=150, span=_span("150"))
    second = copy.deepcopy(first)
    second["parameters"]["query"].update(value="keyboard", span=_span("keyboards"))
    second["parameters"]["max_price"].update(value=100, span=_span("100"))
    data["intents"].append(second)
    return InterpretedRequest.from_dict(data)


def missing_query(request_id="request-demo-clarify"):
    """The request the gate has to ask about: no required ``query`` at all."""
    data = copy.deepcopy(FIXTURE)
    data["request_id"] = request_id
    data["raw_text"] = "Find something in the demo catalog"
    del data["intents"][0]["parameters"]["query"]
    data["missing_required"] = [
        {
            "intent_index": 0,
            "parameter": "query",
            "question": "What product should I search the demo catalog for?",
        }
    ]
    return InterpretedRequest.from_dict(data)


def open_request(request_id="request-open-jobs-1", *, target_domain=None):
    """The shipped open fixture: "best 10 jobs", where "best" means nothing yet.

    ``target_domain`` replaces the intent's domain, which is how the two
    blocked-target cases are built without editing the fixture on disk.
    """
    data = copy.deepcopy(OPEN_FIXTURE)
    data["request_id"] = request_id
    if target_domain is not None:
        data["intents"][0]["target_domain"] = target_domain
    return InterpretedRequest.from_dict(data)


def salary_request(request_id="request-open-jobs-salary"):
    """The same request after "best" was clarified: rank by salary, remote only."""
    data = copy.deepcopy(SALARY_FIXTURE)
    data["request_id"] = request_id
    return InterpretedRequest.from_dict(data)


def five_step_plan():
    """A Plan payload one step over the four-subtask cap, all steps independent."""
    payload = copy.deepcopy(CHAIN)
    template = payload["subtasks"][0]
    payload["subtasks"] = []
    for step_number in range(1, 6):
        step = copy.deepcopy(template)
        step.update(
            subtask_id=f"subtask-open-{step_number}",
            concurrency_group=f"jobs-{step_number}",
            depends_on=[],
            inputs_from={},
        )
        payload["subtasks"].append(step)
    return payload


def injected_planner(plan_payload):
    """What ``--plan-fixture`` builds: ``planner.plan`` with an offline client."""
    client = FakePlannerClient(plan_payload)
    return client, partial(planner.plan, client=client)


# ------------------------------------------------------------------ harness


class BarrierToolbox(FakeToolbox):
    """A :class:`FakeToolbox` whose ``run_subtask`` waits for its sibling.

    Two subtasks can only get past the barrier if the controller really is
    running them at the same time; if it serialises them the barrier times out,
    breaks, and the run fails loudly instead of passing by accident.
    """

    def __init__(self, parties=2, timeout=5.0, **kwargs):
        super().__init__(**kwargs)
        self.barrier = threading.Barrier(parties)
        self.timeout = timeout

    def run_subtask(self, subtask_input):
        self.barrier.wait(timeout=self.timeout)
        return super().run_subtask(subtask_input)


class RecordingToolbox(FakeToolbox):
    """A :class:`FakeToolbox` that keeps every ``SubtaskInput`` it was handed.

    The fake's own ``calls`` record identity and mode but not parameters, and a
    chain has to be checked on exactly that: what the second subtask received
    from the first.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.inputs = []

    def run_subtask(self, subtask_input):
        self.inputs.append(subtask_input)
        return super().run_subtask(subtask_input)

    def input_for(self, subtask_id):
        for subtask_input in self.inputs:
            if subtask_input.subtask.subtask_id == subtask_id:
                return subtask_input
        raise AssertionError(f"{subtask_id} was never dispatched")


class RecordingGhost(FakeGhost):
    """A :class:`FakeGhost` that records how ``validate`` was called.

    Open subtasks must be validated with the ``report_context`` keyword and
    registry subtasks with the original three arguments; nothing else in the
    run makes that visible.
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.validate_kwargs = {}

    def validate(self, subtask, records, evidence, **kwargs):
        self.validate_kwargs[subtask.subtask_id] = kwargs
        return super().validate(subtask, records, evidence, **kwargs)


class EndToEndCase(unittest.TestCase):
    """Wiring shared by every case, plus the assertions about the store."""

    def run_request(
        self, request, *, toolbox=None, moderator=None, ghost=None, **controller_kwargs
    ):
        """Run one request to its terminal result and keep the collaborators."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = JsonStore(directory.name)
        self.toolbox = toolbox if toolbox is not None else FakeToolbox()
        self.moderator = moderator if moderator is not None else StubModerator()
        self.ghost = ghost if ghost is not None else FakeGhost()
        controller = Controller(
            self.toolbox, self.moderator, self.ghost, self.store, **controller_kwargs
        )
        self.result = controller.run(request, request.request_id)
        return self.result

    # -- shared assertions ------------------------------------------------

    def assert_run_is_terminal_and_clean(self):
        """One terminal event, strictly increasing sequences, no session left open."""
        result = self.result
        self.assertIn(
            result.status, {"succeeded", "failed", "cancelled", "needs_input"}
        )
        self.assertEqual(self.toolbox.open_sessions, [])

        events = self.store.events(result.run_id)
        sequences = [event["sequence"] for event in events]
        self.assertEqual(sequences, sorted(set(sequences)))
        terminal = [event for event in events if event["type"].startswith("run_")]
        terminal = [event for event in terminal if event["type"] != "run_created"]
        self.assertEqual(len(terminal), 1, [event["type"] for event in terminal])
        self.assertIn(terminal[0]["stage"], TERMINAL)

    def assert_store_layout(self, expected_subtask_ids):
        """run.json, events.jsonl and exactly one report per dispatched subtask."""
        result = self.result
        run_dir = self.store.run_dir(result.run_id)
        self.assertTrue((run_dir / "run.json").is_file(), f"no run.json in {run_dir}")
        self.assertTrue(
            (run_dir / "events.jsonl").is_file(), f"no events.jsonl in {run_dir}"
        )

        reports = sorted(path.stem for path in (run_dir / "reports").glob("*.json"))
        self.assertEqual(reports, sorted(expected_subtask_ids))

        # run.json is the terminal result itself, minus the session handles the
        # store drops (the key is removed, not set to null).
        stored = self.store.run(result.run_id)
        self.assertEqual(stored["run_id"], result.run_id)
        self.assertEqual(stored["status"], result.status)
        expected = result.to_dict()
        for report in expected["reports"]:
            report.pop("session_handle")
        self.assertEqual(stored, expected)
        for report in stored["reports"]:
            self.assertNotIn(
                "session_handle", report, "a session handle reached the store"
            )
        self.assertEqual(
            self.store.run_id_for_request(result.interpreted.request_id), result.run_id
        )

    def assert_every_claim_is_cited(self):
        for claim in self.result.answer.claims:
            record = self.result.answer.records[claim.record_index]
            self.assertTrue(claim.fields)
            self.assertTrue(all(field in record for field in claim.fields))
            self.assertIn(
                record["source_observation_id"], self.result.validation["evidence"]
            )

    def event_types(self):
        return [event["type"] for event in self.store.events(self.result.run_id)]


# -------------------------------------------------------------------- cases


class SingleSubtaskSearchTest(EndToEndCase):
    """The happy path: one intent, one subtask, one validated, cited answer."""

    def setUp(self):
        self.run_request(single())

    def test_the_run_succeeds_with_cited_records(self):
        self.assertEqual(self.result.status, "succeeded")
        self.assertIsNone(self.result.error)
        self.assertEqual(self.result.gate.decision, "accept")
        self.assertEqual(len(self.result.plan.subtasks), 1)
        self.assertEqual(
            [record["title"] for record in self.result.answer.records],
            ["Studio headphones", "Travel headphones"],
        )
        self.assertEqual(len(self.result.answer.claims), 2)
        self.assert_every_claim_is_cited()
        self.assertEqual(self.result.answer.failures, [])
        self.assertEqual(self.result.validation["status"], "passed")

    def test_the_store_holds_the_run_its_events_and_one_report(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout(["subtask-1"])
        self.assertIn("skill_candidate_created", self.event_types())
        self.assertEqual(
            [skill["status"] for skill in self.store.skills()], ["candidate"]
        )

    def test_the_session_was_lent_once_and_closed(self):
        names = [call[0] for call in self.toolbox.calls]
        self.assertEqual(names, ["open_session", "run_subtask", "close_session"])
        self.assertEqual(len(self.toolbox.closed_sessions), 1)

    def test_every_stored_id_is_the_request_id(self):
        stored = self.store.run(self.result.run_id)
        self.assertEqual(
            self.store.run_id_for_request("request-demo-1"), self.result.run_id
        )
        self.assertEqual(stored["interpreted"]["request_id"], "request-demo-1")
        self.assertEqual(stored["plan"]["request_id"], "request-demo-1")
        self.assertEqual(
            [r["request_id"] for r in stored["reports"]], ["request-demo-1"]
        )
        on_disk = json.loads(
            (self.store.reports_dir(self.result.run_id) / "subtask-1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(on_disk["request_id"], "request-demo-1")


class CompoundRequestTest(EndToEndCase):
    """Two independent subtasks: they run together and are reconciled."""

    def setUp(self):
        self.run_request(compound(), toolbox=BarrierToolbox())

    def test_both_subtasks_ran_at_the_same_time(self):
        self.assertEqual(self.result.status, "succeeded", self.result.error)
        self.assertFalse(
            self.toolbox.barrier.broken,
            "the two subtasks did not reach run_subtask together",
        )
        groups = {task.concurrency_group for task in self.result.plan.subtasks}
        self.assertEqual(len(groups), 2, groups)
        self.assertEqual(len(self.toolbox.calls_named("open_session")), 2)

    def test_the_reports_are_reconciled_into_one_answer(self):
        self.assertIn("reconciled", self.event_types())
        self.assertIn(("reconcile", ("subtask-1", "subtask-2")), self.moderator.calls)
        self.assertEqual(
            {r.request_id for r in self.result.reports}, {"request-demo-compound"}
        )
        titles = [record["title"] for record in self.result.answer.records]
        self.assertEqual(
            titles,
            [
                "Studio headphones",
                "Travel headphones",
                "Mechanical keyboard",
                "Compact keyboard",
            ],
        )
        self.assert_every_claim_is_cited()

    def test_the_store_holds_one_report_per_subtask(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout(["subtask-1", "subtask-2"])


class ClarifyTest(EndToEndCase):
    """A missing required parameter asks a question and executes nothing."""

    def setUp(self):
        self.run_request(missing_query())

    def test_the_run_needs_input_and_nothing_ran(self):
        self.assertEqual(self.result.status, "needs_input")
        self.assertEqual(self.result.gate.decision, "clarify")
        self.assertEqual(self.result.gate.rule_id, "G4")
        self.assertEqual(self.result.error.code, "NEEDS_INPUT")
        self.assertIsNone(self.result.plan)
        self.assertEqual(self.toolbox.calls, [])
        self.assertEqual(self.ghost.calls, [])
        self.assertEqual(self.moderator.calls, [])

    def test_the_question_reaches_the_answer(self):
        question = "What product should I search the demo catalog for?"
        self.assertIn(question, self.result.answer.lines)
        self.assertEqual(
            self.result.answer.notes, [Note("clarification_required", "gate")]
        )
        self.assertEqual(self.result.answer.claims, [])

    def test_the_store_holds_the_run_and_events_but_no_report(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout([])


class RetryOtherPathTest(EndToEndCase):
    """``TARGET_NOT_FOUND`` is retried once on the other tool, then succeeds."""

    def setUp(self):
        self.run_request(
            single(),
            toolbox=FakeToolbox(script={"subtask-1": ["target_not_found", None]}),
            moderator=StubModerator(retry_codes=("TARGET_NOT_FOUND",)),
        )

    def test_the_second_attempt_succeeds(self):
        self.assertEqual(self.result.status, "succeeded", self.result.error)
        self.assertIn("subtask_retried", self.event_types())
        self.assertEqual(len(self.result.answer.claims), 2)
        self.assert_every_claim_is_cited()

    def test_the_retry_used_the_other_interpretation_tool_on_one_session(self):
        attempts = self.toolbox.calls_named("run_subtask")
        self.assertEqual([call[3] for call in attempts], ["dom", "vision"])
        handles = {call[4] for call in attempts}
        self.assertEqual(len(handles), 1, "the retry opened a second session")
        self.assertEqual(len(self.toolbox.calls_named("open_session")), 1)

    def test_the_store_keeps_the_last_report_for_the_subtask(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout(["subtask-1"])
        stored = json.loads(
            (self.store.reports_dir(self.result.run_id) / "subtask-1.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(stored["outcome"], "succeeded")


class AuthRequiredTest(EndToEndCase):
    """``AUTH_REQUIRED`` is terminal: the run fails and says so unchanged."""

    def setUp(self):
        self.run_request(
            single(), toolbox=FakeToolbox(script={"subtask-1": "auth_required"})
        )

    def test_the_run_fails_with_the_workers_own_typed_failure(self):
        self.assertEqual(self.result.status, "failed")
        self.assertEqual(self.result.error.code, "AUTH_REQUIRED")
        self.assertEqual(
            self.result.error.message,
            "the catalog asked for a sign-in before showing results",
        )
        self.assertNotIn("subtask_retried", self.event_types())

    def test_the_answer_is_honest_rather_than_empty(self):
        self.assertEqual(self.result.answer.claims, [])
        self.assertEqual(self.result.answer.records, [])
        self.assertEqual(
            [f.code for f in self.result.answer.failures], ["AUTH_REQUIRED"]
        )
        self.assertIn("Failure: AUTH_REQUIRED.", self.result.answer.lines)
        self.assertEqual(self.store.skills(), [], "a failed run compiled a skill")

    def test_the_store_holds_the_failed_report_and_the_session_was_closed(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout(["subtask-1"])


class ThinEvidenceVerifyTest(EndToEndCase):
    """A succeeded report with no screenshot is verified once, then accepted."""

    def setUp(self):
        self.run_request(single(), toolbox=FakeToolbox(script={"subtask-1": "empty"}))

    def test_the_controller_verified_before_accepting(self):
        self.assertEqual(self.result.status, "succeeded", self.result.error)
        names = [call[0] for call in self.toolbox.calls]
        self.assertEqual(
            names,
            [
                "open_session",
                "run_subtask",
                "observe",
                "dom_interpret",
                "close_session",
            ],
        )
        self.assertIn("subtask_verified", self.event_types())
        self.assertEqual(
            len(self.toolbox.calls_named("observe")), 1, "verification is capped at one"
        )

    def test_the_verification_is_recorded_as_evidence(self):
        report = self.result.reports[0]
        verifications = report.evidence["verifications"]
        self.assertEqual(len(verifications), 1)
        self.assertEqual(verifications[0]["observation_id"], "observation-fake-1")
        self.assertEqual(self.result.answer.records, [])
        self.assert_store_layout(["subtask-1"])


class ValidationFailureTest(EndToEndCase):
    """Ghost fails validation; the accepted report does not rescue the run.

    ``FakeGhost`` is pointed at a different origin than the one the fake worker
    puts in its records, so its ``url_on_site`` check fails on records the
    moderator has already accepted.
    """

    def setUp(self):
        self.run_request(single(), ghost=FakeGhost(origin="https://elsewhere.invalid"))

    def test_the_moderator_accepted_but_the_run_still_failed(self):
        self.assertIn(("assess_report", "subtask-1", "succeeded"), self.moderator.calls)
        self.assertIn("subtask_accepted", self.event_types())
        self.assertEqual(self.result.status, "failed")
        self.assertEqual(self.result.error.code, "VALIDATION_FAILED")
        self.assertFalse(self.result.error.retryable)

    def test_the_failure_is_explained_and_nothing_was_promoted(self):
        self.assertEqual(self.result.validation["status"], "failed")
        self.assertEqual(
            self.result.validation["subtasks"]["subtask-1"]["failed_checks"],
            ["url_on_site"],
        )
        self.assertIn(("synthesize", "request-demo-1", 2), self.moderator.calls)
        self.assertIn(Note("validation_not_passed", "failed"), self.result.answer.notes)
        self.assertIn("Validation status is failed.", self.result.answer.lines)
        self.assertEqual([call[0] for call in self.ghost.calls], ["match", "validate"])
        self.assertEqual(self.store.skills(), [])

    def test_the_store_holds_the_failed_run(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout(["subtask-1"])


# ----------------------------------------------------------- open-world cases


class OpenVagueRankingTest(EndToEndCase):
    """ "The best 10 jobs": S4 asks what "best" means and nothing runs."""

    def setUp(self):
        self.run_request(open_request())

    def test_the_run_needs_input_on_the_ranking_criterion(self):
        self.assertEqual(self.result.status, "needs_input")
        self.assertEqual(self.result.gate.decision, "clarify")
        self.assertEqual(self.result.gate.rule_id, "S4")
        self.assertEqual(self.result.error.code, "NEEDS_INPUT")
        self.assertIsNone(self.result.plan)

    def test_the_question_offers_the_approved_examples(self):
        self.assertEqual(
            S4_QUESTION,
            "What should 'best' mean? For example, highest salary, remote-only "
            "roles, or closest match to your experience.",
        )
        self.assertEqual(self.result.gate.questions, [S4_QUESTION])
        self.assertIn(S4_QUESTION, self.result.answer.lines)
        self.assertEqual(
            self.result.answer.notes, [Note("clarification_required", "gate")]
        )
        self.assertEqual(self.result.answer.claims, [])

    def test_no_session_was_opened_and_the_store_holds_the_run(self):
        self.assertEqual(self.toolbox.calls, [])
        self.assertEqual(self.toolbox.sessions, {})
        self.assertEqual(self.ghost.calls, [])
        self.assertEqual(self.moderator.calls, [])
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout([])


class OpenSalaryChainTest(EndToEndCase):
    """The clarified request: search, then open each result, then one answer."""

    SEARCH = "subtask-open-search"
    DETAILS = "subtask-open-details"

    def setUp(self):
        self.client, plan_fn = injected_planner(CHAIN)
        self.run_request(
            salary_request(),
            toolbox=RecordingToolbox(),
            ghost=RecordingGhost(),
            plan=plan_fn,
        )

    def records(self):
        return self.result.answer.records

    def findings(self, subtask_id):
        report = next(r for r in self.result.reports if r.subtask_id == subtask_id)
        return report.findings

    def test_the_plan_came_from_one_injected_planner_call(self):
        self.assertEqual(self.result.status, "succeeded", self.result.error)
        self.assertEqual(len(self.client.calls), 1, self.client.calls)
        self.assertEqual(self.result.plan.planned_by, "model")
        self.assertEqual(
            [task.subtask_id for task in self.result.plan.subtasks],
            [self.SEARCH, self.DETAILS],
        )
        self.assertEqual(self.result.plan.subtasks[1].depends_on, [self.SEARCH])
        self.assertEqual({task.kind for task in self.result.plan.subtasks}, {"open"})

    def test_the_two_subtasks_ran_in_order_and_the_second_got_the_first_urls(self):
        self.assertEqual(
            [inp.subtask.subtask_id for inp in self.toolbox.inputs],
            [self.SEARCH, self.DETAILS],
        )
        urls = [record["url"] for record in self.findings(self.SEARCH)]
        self.assertTrue(urls)
        received = self.toolbox.input_for(self.DETAILS).subtask.parameters[
            "result_urls"
        ]
        self.assertIsInstance(received, list)
        self.assertEqual(received, urls, "the URLs arrived out of order or incomplete")
        self.assertIn("subtask_inputs_resolved", self.event_types())

    def test_generic_validation_passed_with_the_report_context(self):
        self.assertEqual(self.result.validation["status"], "passed")
        for subtask_id in (self.SEARCH, self.DETAILS):
            checks = self.result.validation["subtasks"][subtask_id]["checks"]
            self.assertTrue(all(checks.values()), (subtask_id, checks))
            self.assertIn("urls_on_target_domain", checks)
            context = self.ghost.validate_kwargs[subtask_id]["report_context"]
            self.assertEqual(context["subtask_id"], subtask_id)
            self.assertEqual(context["run_id"], self.result.run_id)
            self.assertNotIn("session_handle", context["evidence"])
        self.assertIn(
            "completeness",
            " ".join(self.result.validation["subtasks"][self.DETAILS]["unverified"]),
        )

    def test_the_answer_applies_the_criteria_to_one_record_per_job(self):
        records = self.records()
        urls = [record["url"] for record in records]
        self.assertEqual(len(urls), len(set(urls)), urls)
        self.assertLessEqual(len(records), 10)
        self.assertTrue(records)
        self.assertTrue(all(record["remote"] is True for record in records), records)
        salaries = [record["salary"] for record in records]
        self.assertEqual(salaries, sorted(salaries, reverse=True), salaries)
        # The detail subtask is the one that opened each job, so its record wins.
        detail_observations = {
            record["source_observation_id"] for record in self.findings(self.DETAILS)
        }
        self.assertTrue(
            {record["source_observation_id"] for record in records}
            <= detail_observations
        )

    def test_every_claim_cites_an_observation_of_this_run(self):
        claims = self.result.answer.claims
        self.assertEqual(len(claims), len(self.records()))
        self.assert_every_claim_is_cited()
        evidence = set(self.result.validation["evidence"])
        for claim in claims:
            record = self.result.answer.records[claim.record_index]
            self.assertIn(record["source_observation_id"], evidence)

    def test_the_store_holds_one_report_per_subtask(self):
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout([self.SEARCH, self.DETAILS])


class OpenBlockedDomainTest(EndToEndCase):
    """A private or non-public target is rejected before anything is opened."""

    def test_each_blocked_target_fails_with_domain_not_allowed(self):
        for domain in ("127.0.0.1", "intranet.corp"):
            with self.subTest(domain=domain):
                result = self.run_request(
                    open_request(
                        request_id=f"request-open-{domain}", target_domain=domain
                    )
                )
                self.assertEqual(result.status, "failed")
                self.assertEqual(result.gate.decision, "reject")
                self.assertEqual(result.gate.rule_id, "S2")
                self.assertEqual(result.error.code, "DOMAIN_NOT_ALLOWED")
                self.assertIn(domain, result.error.message)
                self.assertIsNone(result.plan)
                self.assertEqual(self.toolbox.calls, [])
                self.assertEqual(result.reports, [])
                self.assert_run_is_terminal_and_clean()


class OpenPlanTooLargeTest(EndToEndCase):
    """Five subtasks are over the cap: the run fails before a session opens."""

    def setUp(self):
        self.client, plan_fn = injected_planner(five_step_plan())
        self.run_request(salary_request(), toolbox=RecordingToolbox(), plan=plan_fn)

    def test_the_plan_is_rejected_with_plan_too_large(self):
        self.assertEqual(self.result.status, "failed")
        self.assertEqual(self.result.error.code, "PLAN_TOO_LARGE")
        self.assertEqual(self.result.gate.decision, "accept")
        self.assertIsNone(self.result.plan)

    def test_nothing_was_opened_or_run(self):
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(
            self.toolbox.calls, [], "the toolbox was used for an over-cap plan"
        )
        self.assertEqual(self.toolbox.inputs, [])
        self.assertEqual(self.toolbox.sessions, {})
        self.assertEqual(self.result.reports, [])
        self.assert_run_is_terminal_and_clean()
        self.assert_store_layout([])


class RegistryPathUnchangedTest(EndToEndCase):
    """The registry fixture still produces exactly what it did before 1b."""

    def setUp(self):
        self.run_request(single(), ghost=RecordingGhost())

    def test_the_result_matches_the_phase_2_expectations(self):
        self.assertEqual(self.result.status, "succeeded", self.result.error)
        self.assertEqual(self.result.gate.rule_id, "G0")
        self.assertEqual(self.result.plan.planned_by, "deterministic")
        self.assertEqual(
            [task.kind for task in self.result.plan.subtasks], ["registry"]
        )
        self.assertEqual(
            [claim.record_index for claim in self.result.answer.claims], [0, 1]
        )
        self.assertTrue(
            all("price" in claim.fields for claim in self.result.answer.claims)
        )
        self.assertIn('title: "Studio headphones"', self.result.answer.lines[1])
        self.assertEqual(self.result.answer.notes, [])

    def test_registry_validation_keeps_the_three_argument_form(self):
        self.assertEqual(self.result.validation["status"], "passed")
        self.assertEqual(self.ghost.validate_kwargs, {"subtask-1": {}})
        self.assertEqual(
            self.result.validation["subtasks"]["subtask-1"]["failed_checks"], []
        )
        self.assert_store_layout(["subtask-1"])


class CommandLineTest(unittest.TestCase):
    """``python -m argus`` end to end, through ``main`` rather than a subprocess."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store_dir = directory.name

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            status = main(argv)
        return status, out.getvalue(), err.getvalue()

    def test_fake_run_with_a_fixture_prints_the_run_result(self):
        status, out, err = self.call(
            "--fake", "--interpreted", str(FIXTURE_PATH), "--store", self.store_dir
        )
        self.assertEqual(status, EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(len(payload["answer"]["claims"]), 2)
        self.assertIn(payload["run_id"], err)
        self.assertTrue(
            (Path(self.store_dir) / "runs" / payload["run_id"] / "run.json").is_file()
        )

    def test_request_id_applies_to_request_text(self):
        # Text input calls the model at stage 1; with no model named the run
        # ends PRECONDITION_FAILED before any call, and is still recorded
        # under the given request ID.
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("ARGUS_MODEL", "OPENAI_API_KEY")
        }
        with mock.patch.dict(os.environ, env, clear=True):
            status, out, _ = self.call(
                "Find headphones in the demo catalog",
                "--store",
                self.store_dir,
                "--request-id",
                "request-cli-1",
            )
        self.assertEqual(status, EXIT_RUN_NOT_SUCCEEDED)
        payload = json.loads(out)
        self.assertEqual(payload["error"]["code"], "PRECONDITION_FAILED")
        self.assertEqual(
            JsonStore(self.store_dir).run_id_for_request("request-cli-1"),
            payload["run_id"],
        )

    def test_request_id_with_interpreted_is_a_usage_error(self):
        status, out, err = self.call(
            "--interpreted",
            str(FIXTURE_PATH),
            "--store",
            self.store_dir,
            "--request-id",
            "request-cli-1",
        )
        self.assertEqual(status, EXIT_USAGE)
        self.assertEqual(out, "")
        self.assertIn("--request-id applies to request text only", err)
        self.assertFalse((Path(self.store_dir) / "runs").exists(), "a run was created")

    def test_the_interpreted_files_request_id_is_the_runs(self):
        status, out, _ = self.call(
            "--interpreted", str(FIXTURE_PATH), "--store", self.store_dir
        )
        self.assertEqual(status, EXIT_OK)
        payload = json.loads(out)
        request_id = FIXTURE["request_id"]
        self.assertEqual(payload["interpreted"]["request_id"], request_id)
        self.assertEqual(payload["plan"]["request_id"], request_id)
        self.assertEqual([r["request_id"] for r in payload["reports"]], [request_id])
        self.assertEqual(
            JsonStore(self.store_dir).run_id_for_request(request_id), payload["run_id"]
        )

    def test_help_states_the_request_id_rule(self):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err), self.assertRaises(SystemExit):
            main(["--help"])
        self.assertIn(
            "Not allowed with --interpreted", " ".join(out.getvalue().split())
        )

    def test_without_fake_it_says_no_toolbox_is_connected(self):
        status, out, err = self.call("--no-fake", "Find headphones in the demo catalog")
        self.assertEqual(status, EXIT_NO_TOOLBOX)
        self.assertEqual(out, "")
        self.assertIn("No real toolbox", err)

    def test_text_and_interpreted_together_is_a_usage_error(self):
        status, _, err = self.call(
            "find headphones", "--interpreted", str(FIXTURE_PATH)
        )
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("not both", err)

    def test_nothing_to_run_is_a_usage_error(self):
        status, _, err = self.call("--store", self.store_dir)
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("nothing to run", err)

    def test_an_open_request_with_a_vague_ranking_asks_and_exits_one(self):
        status, out, err = self.call(
            "--fake", "--interpreted", str(OPEN_PATH), "--store", self.store_dir
        )
        self.assertEqual(status, EXIT_RUN_NOT_SUCCEEDED, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "needs_input")
        self.assertEqual(payload["gate"]["rule_id"], "S4")
        self.assertEqual(payload["gate"]["questions"], [S4_QUESTION])
        self.assertIsNone(payload["plan"])
        self.assertEqual(payload["metrics"]["sessions_opened"], 0)

    def test_the_plan_fixture_runs_the_chain_and_answers_once_per_job(self):
        status, out, err = self.call(
            "--fake",
            "--interpreted",
            str(SALARY_PATH),
            "--plan-fixture",
            str(CHAIN_PATH),
            "--store",
            self.store_dir,
        )
        self.assertEqual(status, EXIT_OK, err)
        payload = json.loads(out)
        self.assertEqual(payload["status"], "succeeded")
        self.assertEqual(payload["plan"]["planned_by"], "model")
        self.assertEqual(len(payload["reports"]), 2)
        urls = [record["url"] for record in payload["answer"]["records"]]
        self.assertEqual(len(urls), len(set(urls)), urls)
        self.assertEqual(len(payload["answer"]["claims"]), len(urls))
        self.assertEqual(payload["validation"]["status"], "passed")

    def test_a_plan_fixture_without_an_interpreted_request_is_a_usage_error(self):
        status, _, err = self.call(
            "--plan-fixture", str(CHAIN_PATH), "Find the best jobs on jobs.example.com"
        )
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("--plan-fixture requires --interpreted", err)

    def test_an_unreadable_plan_fixture_is_a_usage_error(self):
        status, _, err = self.call(
            "--interpreted",
            str(SALARY_PATH),
            "--plan-fixture",
            str(FIXTURE_PATH),
            "--store",
            self.store_dir,
        )
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("is not a Plan", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
