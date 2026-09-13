"""Tests for :mod:`argus.store`.

Every test runs inside a fresh :class:`tempfile.TemporaryDirectory`, so the
suite never touches the repository or a shared path, and no test needs the
network.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from argus import store as store_module
from argus.contracts import ContractError, Event, RunResult, WorkerReport
from argus.interfaces import Store
from argus.store import JsonStore


def _snapshot(run_id: str = "run-1", status: str = "succeeded") -> RunResult:
    return RunResult(run_id=run_id, status=status)


def _event(run_id: str, sequence: int, message: str = "stage entered") -> Event:
    return Event(
        run_id=run_id,
        sequence=sequence,
        timestamp=f"2026-09-12T00:00:{sequence % 60:02d}Z",
        type="stage_entered",
        stage="dispatch",
        message=message,
        data={"sequence": sequence},
    )


def _report(subtask_id: str = "subtask-1", **overrides) -> WorkerReport:
    data = {
        "schema_version": "0.1-provisional",
        "worker": "visual",
        "worker_model": "fake",
        "request_id": "request-1",
        "subtask_id": subtask_id,
        "subtask": "search the demo catalog",
        "outcome": "succeeded",
        "summary": "two results",
        "findings": [{"title": "Widget", "price": 9.5, "currency": "USD"}],
        "actions": [{"step_id": "step-000"}],
        "evidence": {"screenshots": ["observation-000.png"]},
        "metrics": {"browser_action_count": 2},
        "failures": [],
    }
    data.update(overrides)
    return WorkerReport.from_dict(data)


class StoreProtocolTest(unittest.TestCase):
    """Phase 0 fact: ``JsonStore`` is the implementation of ``interfaces.Store``."""

    def test_jsonstore_is_a_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsInstance(JsonStore(tmp), Store)

    def test_layout_is_created_eagerly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            self.assertTrue(store.runs_dir.is_dir())
            self.assertTrue(store.skills_dir.is_dir())


class CreateAndReadTest(unittest.TestCase):
    def test_create_then_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot(status="needs_input"))

            self.assertEqual(store.run("run-1")["status"], "needs_input")
            self.assertEqual(store.run("run-1")["run_id"], "run-1")
            self.assertEqual(store.run_id_for_request("request-1"), "run-1")
            self.assertIsNone(store.run_id_for_request("request-missing"))
            self.assertEqual(store.events("run-1"), [])
            self.assertTrue(store.reports_dir("run-1").is_dir())
            self.assertTrue(store.evidence_dir("run-1").is_dir())
            self.assertEqual(
                json.loads((Path(tmp) / "index.json").read_text(encoding="utf-8")), {"request-1": "run-1"}
            )

    def test_create_accepts_plain_dict_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", {"run_id": "run-1", "status": "failed"})
            self.assertEqual(store.run("run-1")["status"], "failed")

    def test_snapshot_overwrites_and_index_survives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot(status="needs_input"))
            store.save_snapshot("run-1", _snapshot(status="succeeded"))
            self.assertEqual(store.run("run-1")["status"], "succeeded")
            self.assertEqual(store.run_id_for_request("request-1"), "run-1")

    def test_two_runs_share_one_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot("run-1"))
            store.create_run("run-2", "request-2", _snapshot("run-2"))
            self.assertEqual(store.run_id_for_request("request-1"), "run-1")
            self.assertEqual(store.run_id_for_request("request-2"), "run-2")

    def test_duplicate_run_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            with self.assertRaises(ContractError):
                store.create_run("run-1", "request-2", _snapshot())

    def test_missing_run_raises_clear_key_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            with self.assertRaises(KeyError) as run_error:
                store.run("run-nope")
            self.assertIn("run-nope", str(run_error.exception))
            with self.assertRaises(KeyError) as events_error:
                store.events("run-nope")
            self.assertIn("run-nope", str(events_error.exception))

    def test_ids_cannot_escape_the_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            for bad in ("..", "", "a/b", "."):
                with self.assertRaises(ContractError):
                    store.create_run(bad, "request-1", _snapshot())


class EventTest(unittest.TestCase):
    def test_events_are_one_json_line_each_in_append_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            for sequence in range(3):
                store.append_event("run-1", _event("run-1", sequence))

            lines = (store.run_dir("run-1") / "events.jsonl").read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 3)
            events = store.events("run-1")
            self.assertEqual([item["sequence"] for item in events], [0, 1, 2])
            self.assertEqual(events[0]["run_id"], "run-1")
            self.assertEqual(events[0]["data"], {"sequence": 0})

    def test_event_ordering_after_concurrent_appends_from_two_threads(self) -> None:
        """Two writers, one file: no lost, duplicated, torn or reordered lines."""
        per_thread = 60
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            start = threading.Barrier(2)
            counter = iter(range(2 * per_thread))
            counter_lock = threading.Lock()
            errors: list[BaseException] = []

            def writer(worker: str) -> None:
                try:
                    start.wait(timeout=5)
                    for index in range(per_thread):
                        with counter_lock:
                            sequence = next(counter)
                        event = _event("run-1", sequence, message=f"{worker}-{index}")
                        store.append_event("run-1", event)
                except BaseException as error:  # pragma: no cover - surfaced below
                    errors.append(error)

            threads = [
                threading.Thread(target=writer, args=(name,)) for name in ("a", "b")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)
            self.assertEqual(errors, [])

            events = store.events("run-1")
            # Every append survived exactly once and every line parsed.
            self.assertEqual(len(events), 2 * per_thread)
            self.assertEqual(
                sorted(item["sequence"] for item in events), list(range(2 * per_thread))
            )
            # Each writer's own events stay in the order it wrote them.
            for worker in ("a", "b"):
                own = [
                    item["message"]
                    for item in events
                    if item["message"].startswith(f"{worker}-")
                ]
                self.assertEqual(own, [f"{worker}-{index}" for index in range(per_thread)])
            # Sorting by sequence is the caller's job and is always possible.
            ordered = sorted(events, key=lambda item: item["sequence"])
            self.assertEqual([item["sequence"] for item in ordered], list(range(2 * per_thread)))

    def test_append_event_accepts_a_plain_dict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            store.append_event("run-1", _event("run-1", 0).to_dict())
            self.assertEqual(len(store.events("run-1")), 1)

    def test_multiline_message_stays_on_one_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            store.append_event("run-1", _event("run-1", 0, message="two\nlines"))
            path = store.run_dir("run-1") / "events.jsonl"
            self.assertEqual(len(path.read_text(encoding="utf-8").splitlines()), 1)
            self.assertEqual(store.events("run-1")[0]["message"], "two\nlines")


class AtomicWriteTest(unittest.TestCase):
    def test_atomic_overwrite_never_exposes_a_partial_file(self) -> None:
        """A reader racing an overwrite sees one whole snapshot or the other."""
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            filler_a = ["a" * 400 for _ in range(400)]
            filler_b = ["b" * 400 for _ in range(400)]
            store.create_run(
                "run-1", "request-1", RunResult("run-1", "succeeded", metrics={"filler": filler_a})
            )
            stop = threading.Event()
            seen: list[str] = []
            errors: list[BaseException] = []

            def writer() -> None:
                try:
                    for index in range(40):
                        filler = filler_a if index % 2 else filler_b
                        status = "succeeded" if index % 2 else "failed"
                        store.save_snapshot(
                            "run-1", RunResult("run-1", status, metrics={"filler": filler})
                        )
                except BaseException as error:  # pragma: no cover - surfaced below
                    errors.append(error)
                finally:
                    stop.set()

            def reader() -> None:
                try:
                    while not stop.is_set():
                        snapshot = store.run("run-1")
                        seen.append(snapshot["status"])
                        self.assertEqual(len(snapshot["metrics"]["filler"]), 400)
                except BaseException as error:  # pragma: no cover - surfaced below
                    errors.append(error)

            threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            self.assertEqual(errors, [])
            self.assertTrue(seen)
            self.assertTrue(set(seen) <= {"succeeded", "failed"})
            self.assertEqual(store.run("run-1")["status"], "succeeded")

    def test_no_temporary_files_are_left_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            store.save_snapshot("run-1", _snapshot(status="failed"))
            store.save_report("run-1", _report())
            store.save_skill({"skill_id": "search-demo", "version": 1})
            source = Path(tmp) / "shot.png"
            source.write_bytes(b"png-bytes")
            store.save_evidence_file("run-1", "observation-000", str(source))

            leftovers = [
                str(path)
                for path in Path(tmp).rglob("*")
                if path.name.endswith(".tmp") or path.name.startswith(".")
            ]
            self.assertEqual(leftovers, [])
            self.assertEqual(
                sorted(os.listdir(store.run_dir("run-1"))),
                ["evidence", "reports", "run.json"],
            )


def _leftovers(root: str) -> list[str]:
    return [
        str(path) for path in Path(root).rglob("*")
        if path.name.endswith(".tmp") or path.name.startswith(".")
    ]


class ReplaceRetryTest(unittest.TestCase):
    """``os.replace`` is retried on ``PermissionError`` (a reader holding the
    file open on Windows) and gives up loudly after the last attempt."""

    def test_replace_is_retried_on_permission_error(self) -> None:
        real_replace = os.replace
        attempts: list[str] = []

        def flaky(src, dst):
            attempts.append(str(dst))
            if len(attempts) <= 2:
                raise PermissionError(32, "The process cannot access the file")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            with mock.patch.object(store_module, "_REPLACE_DELAY_SECONDS", 0), \
                    mock.patch("os.replace", side_effect=flaky):
                store.create_run("run-1", "request-1", _snapshot())
            # run.json took three attempts, index.json one.
            self.assertEqual(len(attempts), 4)
            self.assertTrue(attempts[0].endswith("run.json"))
            self.assertEqual(store.run("run-1")["status"], "succeeded")
            self.assertEqual(store.run_id_for_request("request-1"), "run-1")
            self.assertEqual(_leftovers(tmp), [])

    def test_replace_gives_up_after_the_last_attempt_with_the_original_error(self) -> None:
        attempts: list[str] = []

        def stuck(src, dst):
            attempts.append(str(dst))
            raise PermissionError(32, "The process cannot access the file")

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            with mock.patch.object(store_module, "_REPLACE_DELAY_SECONDS", 0), \
                    mock.patch("os.replace", side_effect=stuck):
                with self.assertRaises(PermissionError) as caught:
                    store.create_run("run-1", "request-1", _snapshot())
            self.assertEqual(len(attempts), store_module._REPLACE_ATTEMPTS)
            self.assertEqual(store_module._REPLACE_ATTEMPTS, 50)
            self.assertEqual(caught.exception.errno, 32)
            self.assertEqual(_leftovers(tmp), [], "the temporary file was left behind")
            with self.assertRaises(KeyError):
                store.run("run-1")

    def test_other_errors_are_not_retried(self) -> None:
        attempts: list[str] = []

        def full(src, dst):
            attempts.append(str(dst))
            raise OSError(28, "No space left on device")

        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            with mock.patch.object(store_module, "_REPLACE_DELAY_SECONDS", 0), \
                    mock.patch("os.replace", side_effect=full):
                with self.assertRaises(OSError) as caught:
                    store.create_run("run-1", "request-1", _snapshot())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(caught.exception.errno, 28)
            self.assertEqual(_leftovers(tmp), [])


class LockTest(unittest.TestCase):
    """In-process readers and writers of the same path share the store's lock."""

    def test_a_snapshot_read_waits_for_the_lock_a_writer_holds(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            seen: list[str] = []
            finished = threading.Event()

            def reader() -> None:
                seen.append(store.run("run-1")["status"])
                finished.set()

            store._lock.acquire()  # what a writer holds during _write_atomic
            try:
                thread = threading.Thread(target=reader)
                thread.start()
                self.assertFalse(finished.wait(0.2), "the read did not wait for the writer")
                self.assertEqual(seen, [])
            finally:
                store._lock.release()
            self.assertTrue(finished.wait(5))
            thread.join(5)
            self.assertEqual(seen, ["succeeded"])

    def test_the_lock_is_reentrant_for_index_updates(self) -> None:
        # create_run writes index.json while holding the lock, through the
        # same locked _write_json; a plain Lock would deadlock here.
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            store.create_run("run-2", "request-2", _snapshot("run-2"))
            self.assertEqual(store.run_id_for_request("request-2"), "run-2")


class ReportTest(unittest.TestCase):
    def test_session_handle_is_stripped_from_a_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            report = _report(session_handle="steel-session-secret")
            store.save_report("run-1", report)

            path = store.reports_dir("run-1") / "subtask-1.json"
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("session_handle", raw)
            self.assertNotIn("steel-session-secret", raw)
            stored = json.loads(raw)
            self.assertNotIn("session_handle", stored)
            self.assertEqual(stored["subtask_id"], "subtask-1")
            # The stored report still satisfies the contract.
            self.assertIsNone(WorkerReport.from_dict(stored).session_handle)
            # The caller's own report object is untouched.
            self.assertEqual(report.session_handle, "steel-session-secret")

    def test_session_handle_is_stripped_from_a_dict_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            payload = _report().to_dict()
            payload["session_handle"] = "steel-session-secret"
            store.save_report("run-1", payload)
            stored = json.loads((store.reports_dir("run-1") / "subtask-1.json").read_text(encoding="utf-8"))
            self.assertNotIn("session_handle", stored)
            # The caller's dict is not mutated.
            self.assertEqual(payload["session_handle"], "steel-session-secret")

    def test_session_handle_is_stripped_from_snapshot_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            snapshot = RunResult(
                run_id="run-1",
                status="succeeded",
                reports=[_report(session_handle="steel-session-secret")],
            )
            store.create_run("run-1", "request-1", snapshot)
            raw = (store.run_dir("run-1") / "run.json").read_text(encoding="utf-8")
            self.assertNotIn("steel-session-secret", raw)
            self.assertNotIn("session_handle", raw)
            self.assertEqual(len(store.run("run-1")["reports"]), 1)

    def test_report_without_subtask_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            with self.assertRaises(ContractError):
                store.save_report("run-1", {"outcome": "succeeded"})

    def test_one_file_per_subtask(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            store.save_report("run-1", _report("subtask-1"))
            store.save_report("run-1", _report("subtask-2"))
            store.save_report("run-1", _report("subtask-1", outcome="failed"))
            self.assertEqual(
                sorted(os.listdir(store.reports_dir("run-1"))),
                ["subtask-1.json", "subtask-2.json"],
            )
            stored = json.loads((store.reports_dir("run-1") / "subtask-1.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["outcome"], "failed")


class EvidenceTest(unittest.TestCase):
    def test_evidence_file_is_copied_under_the_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            source = Path(tmp) / "outside.png"
            source.write_bytes(b"screenshot-bytes")

            stored = store.save_evidence_file("run-1", "observation-000", str(source))

            self.assertEqual(Path(stored).parent, store.evidence_dir("run-1"))
            self.assertEqual(Path(stored).name, "observation-000.png")
            self.assertEqual(Path(stored).read_bytes(), b"screenshot-bytes")
            # The source is copied, not moved.
            self.assertTrue(source.is_file())

    def test_observation_id_that_already_has_the_extension_is_not_doubled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            source = Path(tmp) / "outside.png"
            source.write_bytes(b"x")
            stored = store.save_evidence_file("run-1", "observation-000.png", str(source))
            self.assertEqual(Path(stored).name, "observation-000.png")

    def test_missing_source_raises_file_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.create_run("run-1", "request-1", _snapshot())
            with self.assertRaises(FileNotFoundError):
                store.save_evidence_file("run-1", "observation-000", str(Path(tmp) / "gone.png"))


class SkillTest(unittest.TestCase):
    def test_skill_versions_are_listed_in_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            for version in (10, 2, 1, 3):
                store.save_skill(
                    {"skill_id": "search-demo", "version": version, "status": "candidate"}
                )
            store.save_skill({"skill_id": "another-skill", "version": 1})

            listed = store.skills()
            self.assertEqual(
                [(item["skill_id"], item["version"]) for item in listed],
                [("another-skill", 1), ("search-demo", 1), ("search-demo", 2),
                 ("search-demo", 3), ("search-demo", 10)],
            )
            self.assertEqual(
                sorted(os.listdir(store.skills_dir / "search-demo")),
                ["v1.json", "v10.json", "v2.json", "v3.json"],
            )

    def test_dotted_versions_sort_numerically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            for version in ("1.10", "1.2", "2.0"):
                store.save_skill({"skill_id": "search-demo", "version": version})
            self.assertEqual(
                [item["version"] for item in store.skills()], ["1.2", "1.10", "2.0"]
            )

    def test_version_defaults_to_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.save_skill({"skill_id": "search-demo", "status": "candidate"})
            self.assertEqual([item["version"] for item in store.skills()], [1])
            self.assertTrue((store.skills_dir / "search-demo" / "v1.json").is_file())

    def test_session_handle_is_stripped_from_a_skill(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = JsonStore(tmp)
            store.save_skill(
                {"skill_id": "search-demo", "version": 1, "session_handle": "steel-secret"}
            )
            raw = (store.skills_dir / "search-demo" / "v1.json").read_text(encoding="utf-8")
            self.assertNotIn("steel-secret", raw)
            self.assertEqual(store.skills(), [{"skill_id": "search-demo", "version": 1}])

    def test_empty_store_has_no_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(JsonStore(tmp).skills(), [])

    def test_skill_without_id_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ContractError):
                JsonStore(tmp).save_skill({"version": 1})


class ReopenTest(unittest.TestCase):
    def test_a_second_store_reads_what_the_first_wrote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = JsonStore(tmp)
            first.create_run("run-1", "request-1", _snapshot())
            first.append_event("run-1", _event("run-1", 0))
            first.save_report("run-1", _report())
            first.save_skill({"skill_id": "search-demo", "version": 1})

            second = JsonStore(tmp)
            self.assertEqual(second.run("run-1")["run_id"], "run-1")
            self.assertEqual(len(second.events("run-1")), 1)
            self.assertEqual(second.run_id_for_request("request-1"), "run-1")
            self.assertEqual(len(second.skills()), 1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
