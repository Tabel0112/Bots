"""Offline tests for the process-local ARGUS Steel session manager."""

from __future__ import annotations

import base64
import builtins
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from argus.adapters.steel_sessions import SteelSessionManager
from argus.contracts import ContractError

PNG = b"\x89PNG\r\n\x1a\nfixture"


class FakeSessions:
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.released: list[str] = []
        self.computer_calls: list[tuple[str, dict]] = []
        self.release_failures: set[str] = set()

    def create(self, **kwargs):
        self.created.append(kwargs)
        return SimpleNamespace(id=kwargs["session_id"])

    def release(self, handle):
        self.released.append(handle)
        if handle in self.release_failures:
            raise RuntimeError("provider detail")

    def computer(self, handle, **kwargs):
        self.computer_calls.append((handle, kwargs))
        return SimpleNamespace(base64_image=base64.b64encode(PNG).decode("ascii"))


class FakeClient:
    def __init__(self) -> None:
        self.sessions = FakeSessions()


class SteelSessionManagerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.client = FakeClient()
        self.manager = SteelSessionManager(self.client, evidence_dir=self.directory)

    def test_open_then_close_uses_caller_id(self):
        handle = self.manager.open("demo-catalog")
        self.assertEqual(self.client.sessions.created, [{"session_id": handle}])
        self.manager.close(handle)
        self.assertEqual(self.client.sessions.released, [handle])

    def test_double_close_is_a_noop(self):
        handle = self.manager.open("demo-catalog")
        self.manager.close(handle)
        self.manager.close(handle)
        self.assertEqual(self.client.sessions.released, [handle])

    def test_unknown_handle_raises(self):
        with self.assertRaises(ContractError) as caught:
            self.manager.close("missing")
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")

    def test_fifth_open_session_is_refused(self):
        handles = [self.manager.open(f"site-{number}") for number in range(4)]
        self.assertEqual(len(handles), 4)
        with self.assertRaises(ContractError) as caught:
            self.manager.open("site-5")
        self.assertEqual(caught.exception.code, "BUDGET_EXCEEDED")
        self.assertEqual(len(self.client.sessions.created), 4)

    def test_close_all_continues_after_failure(self):
        handles = [self.manager.open(f"site-{number}") for number in range(3)]
        self.client.sessions.release_failures.add(handles[1])
        result = self.manager.close_all()
        self.assertEqual(self.client.sessions.released, handles)
        self.assertEqual(result["released"], [handles[0], handles[2]])
        self.assertEqual(
            result["failures"],
            [{"handle": handles[1], "error": "ContractError"}],
        )

    def test_observe_increments_ids_and_records_paths(self):
        handle = self.manager.open("demo-catalog")
        self.assertEqual(self.manager.observe(handle), "observation-1")
        self.assertEqual(self.manager.observe(handle), "observation-2")
        self.assertEqual(
            self.client.sessions.computer_calls,
            [
                (handle, {"action": "take_screenshot"}),
                (handle, {"action": "take_screenshot"}),
            ],
        )
        paths = self.manager.paths()
        self.assertEqual(set(paths), {"observation-1", "observation-2"})
        self.assertTrue(all(Path(path).read_bytes() == PNG for path in paths.values()))

    def test_observe_rejects_closed_handle(self):
        handle = self.manager.open("demo-catalog")
        self.manager.close(handle)
        with self.assertRaises(ContractError):
            self.manager.observe(handle)

    def test_missing_sdk_is_only_checked_when_constructing_default_client(self):
        original_import = builtins.__import__

        def without_steel(name, *args, **kwargs):
            if name == "steel":
                raise ImportError("not installed")
            return original_import(name, *args, **kwargs)

        with (
            mock.patch("builtins.__import__", side_effect=without_steel),
            self.assertRaises(ContractError) as caught,
        ):
            SteelSessionManager()
        self.assertEqual(caught.exception.code, "PRECONDITION_FAILED")
        self.assertIn("STEEL_API_KEY", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
