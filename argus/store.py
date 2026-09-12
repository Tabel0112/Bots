"""JSON storage for ARGUS runs, events, reports, evidence and skills.

:class:`JsonStore` is the only implementation of
:class:`argus.interfaces.Store`.  The controller writes through that protocol,
so every durable artifact of a run lands in one directory tree that a human (or
a later dashboard) can read without the controller running:

.. code-block:: text

    <root>/index.json                                 request_id -> run_id
    <root>/runs/<run_id>/run.json                     RunResult, or an in-progress snapshot
    <root>/runs/<run_id>/events.jsonl                 one Event per line, append only
    <root>/runs/<run_id>/reports/<subtask_id>.json    one WorkerReport per subtask
    <root>/runs/<run_id>/evidence/<observation_id>    files named by observation ID
    <root>/skills/<skill_id>/v<version>.json          one file per skill version

Durability rules, all enforced here rather than by callers:

* Every whole-file write goes to a temporary file in the *same* directory and is
  then moved into place with :func:`os.replace`, so a reader either sees the
  previous file or the new one, never a half-written one.
* A single :class:`threading.Lock` serialises ``index.json`` updates and
  ``events.jsonl`` appends, which are the only writes two threads can race on
  (the dispatcher runs subtasks concurrently, and each one emits events).
* Reads of an unknown run raise :class:`KeyError` naming the run and the root,
  rather than leaking a path-not-found from deeper down.
* Nothing secret is stored: a top-level ``session_handle`` is stripped from
  every report, snapshot report and skill before it is written, because a
  session handle is a live browser credential and the store outlives the run.

Standard library only.  IDs that become path segments are checked, so a crafted
``run_id`` or ``subtask_id`` cannot write outside the root.
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping

from argus.contracts import ContractError

__all__ = ["JsonStore", "SESSION_HANDLE_KEY"]

#: The one key that is never written to disk (see the module docstring).
SESSION_HANDLE_KEY = "session_handle"

_FORBIDDEN_SEGMENTS = {"", ".", ".."}


def _segment(kind: str, value: Any) -> str:
    """Return ``value`` as a safe single path segment, or raise.

    Guards the store against an ID that would escape the root (``..``) or split
    into several directories (``a/b``).
    """
    text = str(value)
    separators = {"/", "\\", "\0", os.sep, os.altsep or os.sep}
    if text in _FORBIDDEN_SEGMENTS or any(sep in text for sep in separators):
        raise ContractError(f"{kind} {value!r} is not usable as a path segment")
    return text


def _version_sort_key(version: Any) -> tuple[int, tuple[int, ...], str]:
    """Order skill versions numerically: ``v2`` before ``v10``.

    Dotted numeric versions (``1.3``) compare component by component; anything
    else falls back to a string comparison after all numeric versions.
    """
    text = str(version)
    parts = text.split(".")
    if parts and all(part.isdigit() for part in parts):
        return (0, tuple(int(part) for part in parts), "")
    return (1, (), text)


def _as_dict(value: Any, kind: str) -> dict[str, Any]:
    """Accept either a contracts dataclass or plain JSON data, return a copy."""
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        data = to_dict()
        if not isinstance(data, dict):
            raise ContractError(f"{kind}.to_dict() did not return an object")
        return data
    if isinstance(value, Mapping):
        return copy.deepcopy(dict(value))
    raise ContractError(f"{kind} must be a message dataclass or a mapping, got {type(value).__name__}")


class JsonStore:
    """Durable JSON storage under ``root_dir``; implements ``interfaces.Store``.

    The directory tree is created eagerly so an empty store is still readable.
    One instance is safe to share between the dispatcher's threads.
    """

    def __init__(self, root_dir: str | os.PathLike[str]) -> None:
        self.root = Path(root_dir)
        self._lock = threading.Lock()
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"JsonStore({str(self.root)!r})"

    # ------------------------------------------------------------------ paths

    @property
    def runs_dir(self) -> Path:
        """Directory holding one subdirectory per run."""
        return self.root / "runs"

    @property
    def skills_dir(self) -> Path:
        """Directory holding one subdirectory per skill ID."""
        return self.root / "skills"

    @property
    def index_path(self) -> Path:
        """The ``request_id -> run_id`` index file."""
        return self.root / "index.json"

    def run_dir(self, run_id: str) -> Path:
        """Directory of one run.  Public so tests and the CLI can show paths."""
        return self.runs_dir / _segment("run_id", run_id)

    def reports_dir(self, run_id: str) -> Path:
        """Where this run's worker reports are written."""
        return self.run_dir(run_id) / "reports"

    def evidence_dir(self, run_id: str) -> Path:
        """Where this run's evidence files are copied."""
        return self.run_dir(run_id) / "evidence"

    def _run_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run.json"

    def _events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "events.jsonl"

    # ------------------------------------------------------------- primitives

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """Write ``text`` to ``path`` as one indivisible replacement."""
        path.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(handle_fd, "w", encoding="utf-8") as handle:
                handle.write(text)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    def _write_json(self, path: Path, payload: Any) -> None:
        self._write_atomic(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    @staticmethod
    def _scrub(data: dict[str, Any]) -> dict[str, Any]:
        """Drop the session handle from a report, snapshot or skill in place."""
        data.pop(SESSION_HANDLE_KEY, None)
        return data

    def _prepare_snapshot(self, snapshot: Any) -> dict[str, Any]:
        data = self._scrub(_as_dict(snapshot, "snapshot"))
        reports = data.get("reports")
        if isinstance(reports, list):
            data["reports"] = [
                self._scrub(item) if isinstance(item, dict) else item for item in reports
            ]
        return data

    def _load_index(self) -> dict[str, str]:
        """Read ``index.json``; caller holds ``self._lock``."""
        try:
            data = self._read_json(self.index_path)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ContractError(f"{self.index_path} is not a request_id -> run_id object")
        return data

    # ------------------------------------------------------------------ runs

    def create_run(self, run_id: str, request_id: str, snapshot: dict[str, Any]) -> None:
        """Create the run's directories, write the first snapshot, index it.

        Raises :class:`ContractError` if the run already exists: a repeated
        ``run_id`` is an ID collision, and silently overwriting a finished run's
        result would lose it.  Use :meth:`save_snapshot` to update a run.
        """
        run_id = _segment("run_id", run_id)
        data = self._prepare_snapshot(snapshot)
        path = self._run_path(run_id)
        if path.exists():
            raise ContractError(f"run {run_id!r} already exists under {self.runs_dir}")
        self.reports_dir(run_id).mkdir(parents=True, exist_ok=True)
        self.evidence_dir(run_id).mkdir(parents=True, exist_ok=True)
        self._write_json(path, data)
        with self._lock:
            index = self._load_index()
            index[str(request_id)] = run_id
            self._write_json(self.index_path, index)

    def save_snapshot(self, run_id: str, snapshot: dict[str, Any]) -> None:
        """Overwrite ``run.json`` atomically with an in-progress or final state.

        Called on every stage transition and once more with the terminal
        :class:`~argus.contracts.RunResult`, so it must never fail on a run whose
        directory is already gone-free: the directory is recreated if needed.
        """
        run_id = _segment("run_id", run_id)
        self._write_json(self._run_path(run_id), self._prepare_snapshot(snapshot))

    def append_event(self, run_id: str, event: Any) -> None:
        """Append one event as a single JSON line, serialised across threads."""
        run_id = _segment("run_id", run_id)
        line = json.dumps(_as_dict(event, "event"), ensure_ascii=False) + "\n"
        path = self._events_path(run_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())

    def save_report(self, run_id: str, report: Any) -> None:
        """Write one worker report, keyed by its ``subtask_id``, handle stripped."""
        run_id = _segment("run_id", run_id)
        data = self._scrub(_as_dict(report, "report"))
        subtask_id = data.get("subtask_id")
        if not subtask_id:
            raise ContractError("report has no subtask_id, so it has no file name")
        name = _segment("subtask_id", subtask_id)
        self._write_json(self.reports_dir(run_id) / f"{name}.json", data)

    def save_evidence_file(self, run_id: str, observation_id: str, source_path: str) -> str:
        """Copy an evidence file into the run and return its stored path.

        The stored name is the observation ID plus the source's extension, so a
        report referring to ``observation-000.png`` finds the file by that name.
        """
        run_id = _segment("run_id", run_id)
        name = _segment("observation_id", observation_id)
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"evidence source {str(source_path)!r} is not a file")
        suffix = source.suffix
        if suffix and not name.endswith(suffix):
            name = f"{name}{suffix}"
        target = self.evidence_dir(run_id) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        handle_fd, tmp_name = tempfile.mkstemp(
            prefix=f".{name}.", suffix=".tmp", dir=str(target.parent)
        )
        os.close(handle_fd)
        try:
            shutil.copyfile(source, tmp_name)
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return str(target)

    # ---------------------------------------------------------------- skills

    def save_skill(self, skill: dict[str, Any]) -> None:
        """Write one skill version.  ``version`` defaults to 1 when absent."""
        data = self._scrub(_as_dict(skill, "skill"))
        skill_id = data.get("skill_id")
        if not skill_id:
            raise ContractError("skill has no skill_id, so it has no directory")
        version = data.get("version")
        if version is None or version == "":
            version = 1
            data["version"] = version
        directory = self.skills_dir / _segment("skill_id", skill_id)
        self._write_json(directory / f"v{_segment('version', version)}.json", data)

    def skills(self) -> list[dict[str, Any]]:
        """Every stored skill version, grouped by skill ID, oldest version first."""
        found: list[dict[str, Any]] = []
        if not self.skills_dir.is_dir():
            return found
        for directory in sorted(self.skills_dir.iterdir(), key=lambda path: path.name):
            if not directory.is_dir():
                continue
            versions: list[tuple[tuple[int, tuple[int, ...], str], dict[str, Any]]] = []
            for path in directory.glob("v*.json"):
                data = self._read_json(path)
                stated = data.get("version", path.stem[1:]) if isinstance(data, dict) else path.stem[1:]
                versions.append((_version_sort_key(stated), data))
            versions.sort(key=lambda item: item[0])
            found.extend(data for _, data in versions)
        return found

    # ----------------------------------------------------------------- reads

    def run(self, run_id: str) -> dict[str, Any]:
        """The run's latest snapshot or terminal result.

        Raises :class:`KeyError` naming the run when it was never created.
        """
        run_id = _segment("run_id", run_id)
        try:
            return self._read_json(self._run_path(run_id))
        except FileNotFoundError:
            raise KeyError(f"no run {run_id!r} under {self.runs_dir}") from None

    def events(self, run_id: str) -> list[dict[str, Any]]:
        """The run's events in the order they were appended.

        An existing run with no events yet returns ``[]``; an unknown run raises
        :class:`KeyError`.  Ordering by ``sequence`` is the caller's job, since
        two threads may append in an order the sequence numbers do not match.
        """
        run_id = _segment("run_id", run_id)
        if not self.run_dir(run_id).is_dir():
            raise KeyError(f"no run {run_id!r} under {self.runs_dir}")
        path = self._events_path(run_id)
        if not path.exists():
            return []
        with path.open(encoding="utf-8") as handle:
            return [json.loads(line) for line in handle if line.strip()]

    def run_id_for_request(self, request_id: str) -> str | None:
        """The run recorded for this request, or ``None`` if there is none."""
        with self._lock:
            return self._load_index().get(str(request_id))
