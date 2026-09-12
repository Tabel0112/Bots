"""Phase 2: whole runs through the real controller and the shipped fakes.

Every test here builds its request from ``argus/examples/interpreted_request.json``
and runs :class:`argus.controller.Controller` with
:class:`~argus.fakes.FakeToolbox`, :class:`~argus.fakes.StubModerator`,
:class:`~argus.fakes.FakeGhost` and a :class:`~argus.store.JsonStore` in a
temporary directory.  Nothing calls the network: stage 1 is supplied as an
already-interpreted request, which is exactly what ``python -m argus
--interpreted FILE`` does.

Covered: a single-subtask search, a compound request whose two subtasks really
do run at the same time, a clarify that executes nothing, a scripted
``target_not_found`` that retries on the other interpretation tool and then
succeeds, a scripted ``auth_required`` that fails honestly, a thin-evidence
report that the controller verifies before it is accepted, and a validation
failure that the moderator cannot override.  Each dispatching run also asserts
what the store holds afterwards: ``run.json``, ``events.jsonl`` and one report
per subtask.
"""

import copy
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from argus.__main__ import EXIT_NO_TOOLBOX, EXIT_OK, EXIT_USAGE, main
from argus.contracts import InterpretedRequest
from argus.controller import TERMINAL, Controller
from argus.fakes import FakeGhost, FakeToolbox, StubModerator
from argus.store import JsonStore

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
FIXTURE_PATH = EXAMPLES / "interpreted_request.json"
FIXTURE = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

#: Text of the compound request, so the spans below are real spans of it.
COMPOUND_TEXT = "Find headphones under $150 and keyboards under $100 in the demo catalog"


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


class EndToEndCase(unittest.TestCase):
    """Wiring shared by every case, plus the assertions about the store."""

    def run_request(self, request, *, toolbox=None, moderator=None, ghost=None, **controller_kwargs):
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
        self.assertIn(result.status, {"succeeded", "failed", "cancelled", "needs_input"})
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
        self.assertTrue((run_dir / "events.jsonl").is_file(), f"no events.jsonl in {run_dir}")

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
            self.assertNotIn("session_handle", report, "a session handle reached the store")
        self.assertEqual(
            self.store.run_id_for_request(result.interpreted.request_id), result.run_id
        )

    def assert_every_claim_is_cited(self):
        for claim in self.result.answer.claims:
            self.assertTrue(claim.evidence_refs, f"uncited claim: {claim.text!r}")

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
        titles = [record["title"] for record in self.result.answer.records]
        self.assertEqual(
            titles,
            ["Studio headphones", "Travel headphones", "Mechanical keyboard", "Compact keyboard"],
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
        self.assertIn(question, self.result.answer.text)
        self.assertEqual(self.result.answer.unverified, [question])
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
            (self.store.reports_dir(self.result.run_id) / "subtask-1.json").read_text()
        )
        self.assertEqual(stored["outcome"], "succeeded")


class AuthRequiredTest(EndToEndCase):
    """``AUTH_REQUIRED`` is terminal: the run fails and says so unchanged."""

    def setUp(self):
        self.run_request(single(), toolbox=FakeToolbox(script={"subtask-1": "auth_required"}))

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
        self.assertEqual([f.code for f in self.result.answer.failures], ["AUTH_REQUIRED"])
        self.assertTrue(
            any("AUTH_REQUIRED" in note for note in self.result.answer.unverified),
            self.result.answer.unverified,
        )
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
            ["open_session", "run_subtask", "observe", "dom_interpret", "close_session"],
        )
        self.assertIn("subtask_verified", self.event_types())
        self.assertEqual(len(self.toolbox.calls_named("observe")), 1, "verification is capped at one")

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
            self.result.validation["subtasks"]["subtask-1"]["failed_checks"], ["url_on_site"]
        )
        self.assertIn(("synthesize", "request-demo-1", 2), self.moderator.calls)
        self.assertIn(
            "validation failed: records are not validated", self.result.answer.unverified
        )
        self.assertEqual([call[0] for call in self.ghost.calls], ["match", "validate"])
        self.assertEqual(self.store.skills(), [])

    def test_the_store_holds_the_failed_run(self):
        self.assert_run_is_terminal_and_clean()
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

    def test_request_id_can_be_overridden(self):
        status, out, _ = self.call(
            "--interpreted", str(FIXTURE_PATH), "--store", self.store_dir,
            "--request-id", "request-cli-1",
        )
        self.assertEqual(status, EXIT_OK)
        run_id = json.loads(out)["run_id"]
        self.assertEqual(JsonStore(self.store_dir).run_id_for_request("request-cli-1"), run_id)

    def test_without_fake_it_says_no_toolbox_is_connected(self):
        status, out, err = self.call("--no-fake", "Find headphones in the demo catalog")
        self.assertEqual(status, EXIT_NO_TOOLBOX)
        self.assertEqual(out, "")
        self.assertIn("No real toolbox", err)

    def test_text_and_interpreted_together_is_a_usage_error(self):
        status, _, err = self.call("find headphones", "--interpreted", str(FIXTURE_PATH))
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("not both", err)

    def test_nothing_to_run_is_a_usage_error(self):
        status, _, err = self.call("--store", self.store_dir)
        self.assertEqual(status, EXIT_USAGE)
        self.assertIn("nothing to run", err)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
