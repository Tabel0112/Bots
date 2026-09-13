"""ARGUS ``Toolbox`` facade over Tianqi's DOM browser worker (P3-DOM).

The controller is synchronous; the worker is ``async``.  :class:`DomToolbox`
runs one long-lived asyncio loop in a daemon thread and submits each
``Worker.run`` to it with ``run_coroutine_threadsafe``, waiting at most the
subtask's ``max_seconds`` plus a grace period.  A timeout sets the worker's
cancel event and yields a ``BUDGET_EXCEEDED`` report; any exception yields a
failed report that names only the exception class.

Sessions are worker-owned tonight (ARGUS-3 decision 4): ``open_session``
returns a virtual ``worker-owned-N`` handle and ``close_session`` only records
the call.  ``observe``, ``dom_interpret`` and ``vision_interpret`` need the
ARGUS session manager (P3-SESSION), which is not built yet, and refuse with
``PRECONDITION_FAILED``.

The worker's validation, Ghost block, limitations and session disposition are
kept out of the ARGUS report; :meth:`DomToolbox.context_for` hands them to the
Ghost bridge keyed by ``(run_id, subtask_id)``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Callable, Mapping
from typing import Any

from argus.adapters.worker_contracts import (
    WORKER_NAME,
    failed_report,
    prepare_request,
    to_worker_report,
)
from argus.contracts import ContractError, SubtaskInput, TypedError, WorkerReport

__all__ = ["DomToolbox"]

_SESSION_MANAGER_MESSAGE = (
    "{method} needs the ARGUS session manager (P3-SESSION), which is not built yet; "
    "sessions are worker-owned tonight (ARGUS-3 decision 4)"
)


class DomToolbox:
    """Implements :class:`argus.interfaces.Toolbox` by delegating to the DOM worker.

    ``worker_factory`` is called as ``worker_factory(sites=..., settings=...)``
    (``settings`` only when given) once per ``run_subtask`` and must return an
    object with ``async run(request, cancel=asyncio.Event) -> SubtaskReport``.
    It defaults to ``Agents.browser_worker.runner.Worker``, imported lazily so
    this module loads without the worker's browser dependencies.  ``sites``
    defaults to ``Agents.browser_worker.config.load_sites()``, also lazily.

    Calls are recorded in :attr:`calls` the way ``argus.fakes.FakeToolbox`` does,
    so the same assertions work against both.
    """

    def __init__(
        self,
        worker_factory: Callable[..., Any] | None = None,
        sites: Mapping[str, Any] | None = None,
        settings: Any = None,
        *,
        grace_seconds: float = 5.0,
    ) -> None:
        self._worker_factory = worker_factory
        self._sites = dict(sites) if sites is not None else None
        self._settings = settings
        self._grace_seconds = float(grace_seconds)
        #: Every call, in order, as ``(method name, *arguments)``.
        self.calls: list[tuple[Any, ...]] = []
        #: ``handle -> True`` while open, ``False`` once ``close_session`` was called.
        self.sessions: dict[str, bool] = {}
        self._contexts: dict[tuple[str, str], dict[str, Any]] = {}
        self._events: dict[tuple[str, str], asyncio.Event] = {}
        self._session_n = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()

    # -- wiring ------------------------------------------------------------

    @property
    def sites(self) -> dict[str, Any]:
        """The worker site configuration, loaded on first use."""
        if self._sites is None:
            from Agents.browser_worker.config import load_sites

            self._sites = dict(load_sites())
        return self._sites

    def _make_worker(self) -> Any:
        factory = self._worker_factory
        if factory is None:
            from Agents.browser_worker.runner import Worker

            factory = Worker
        if self._settings is not None:
            return factory(sites=self.sites, settings=self._settings)
        return factory(sites=self.sites)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is None or self._loop.is_closed():
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever, name="argus-dom-toolbox", daemon=True
                )
                thread.start()
                self._loop, self._thread = loop, thread
            return self._loop

    def calls_named(self, name: str) -> list[tuple[Any, ...]]:
        """Every recorded call to one method."""
        with self._lock:
            return [call for call in self.calls if call[0] == name]

    def context_for(self, run_id: str, subtask_id: str) -> dict[str, Any] | None:
        """The worker-side context of a finished subtask, or ``None`` when unknown.

        Agreed interface with the Ghost bridge: ``validation`` (the worker's
        check list), ``ghost``, ``limitations``, ``session_disposition`` and
        ``final_url``.
        """
        with self._lock:
            context = self._contexts.get((run_id, subtask_id))
            return dict(context) if context is not None else None

    def cancel(self, run_id: str, subtask_id: str) -> bool:
        """Ask the worker to stop this subtask; True when it was running."""
        with self._lock:
            event = self._events.get((run_id, subtask_id))
        if event is None:
            return False
        loop = self._ensure_loop()
        loop.call_soon_threadsafe(event.set)
        with self._lock:
            self.calls.append(("cancel", run_id, subtask_id))
        return True

    # -- Toolbox -------------------------------------------------------------

    def open_session(self, site_id: str) -> str:
        """Return a virtual ``worker-owned-N`` handle; the worker opens the real session."""
        with self._lock:
            self._session_n += 1
            handle = f"worker-owned-{self._session_n}"
            self.sessions[handle] = True
            self.calls.append(("open_session", site_id, handle))
        return handle

    def close_session(self, handle: str) -> None:
        """Record the call; the worker already released its own session."""
        with self._lock:
            self.calls.append(("close_session", handle))
            if handle in self.sessions:
                self.sessions[handle] = False

    def run_subtask(self, subtask_input: SubtaskInput) -> WorkerReport:
        """Translate, run the worker on the loop thread and translate back."""
        subtask = subtask_input.subtask
        key = (subtask_input.run_id, subtask.subtask_id)
        with self._lock:
            self.calls.append(
                (
                    "run_subtask",
                    subtask.subtask_id,
                    subtask_input.mode,
                    subtask.preferred_tool,
                    subtask_input.session_handle,
                )
            )
        # Contract refusals (unknown operation, unconfigured site) propagate as
        # ContractError before a worker exists; the controller maps them.
        request, limitations = prepare_request(
            subtask_input, self.sites, settings=self._settings
        )
        loop = self._ensure_loop()
        event = asyncio.Event()
        with self._lock:
            self._events[key] = event
        try:
            worker = self._make_worker()
        except Exception:
            with self._lock:
                self._events.pop(key, None)
            raise
        future = asyncio.run_coroutine_threadsafe(
            worker.run(request, cancel=event), loop
        )
        timeout = (
            max(0.0, float(subtask_input.budget.max_seconds)) + self._grace_seconds
        )
        try:
            report = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            loop.call_soon_threadsafe(event.set)
            self._settle(future)
            result = self._timeout_report(subtask_input, timeout)
            context = {"limitations": list(limitations) + ["worker timed out"]}
        except Exception as exc:  # noqa: BLE001 - every worker error becomes a typed report
            result = failed_report(subtask_input, type(exc).__name__)
            context = {
                "limitations": list(limitations)
                + [f"worker raised {type(exc).__name__}"]
            }
        else:
            try:
                result, context = to_worker_report(report, subtask_input)
            except Exception as exc:  # noqa: BLE001 - a malformed worker report is a failure too
                result = failed_report(subtask_input, type(exc).__name__)
                context = {
                    "limitations": list(limitations)
                    + ["worker report not translatable"]
                }
            else:
                context["limitations"] = list(limitations) + list(
                    context.get("limitations", [])
                )
        finally:
            with self._lock:
                self._events.pop(key, None)
        with self._lock:
            self._contexts[key] = context
            # The Ghost bridge's compile step only has the report's request_id, so
            # the same context is reachable under that key as well.
            request_id = subtask_input.request_id or subtask_input.run_id
            if request_id != subtask_input.run_id:
                self._contexts[(request_id, subtask.subtask_id)] = context
        return result

    def observe(self, handle: str) -> str:
        with self._lock:
            self.calls.append(("observe", handle))
        raise ContractError(
            _SESSION_MANAGER_MESSAGE.format(method="observe"),
            code="PRECONDITION_FAILED",
        )

    def dom_interpret(self, handle: str, question: str) -> str:
        with self._lock:
            self.calls.append(("dom_interpret", handle, question))
        raise ContractError(
            _SESSION_MANAGER_MESSAGE.format(method="dom_interpret"),
            code="PRECONDITION_FAILED",
        )

    def vision_interpret(self, handle: str, question: str) -> str:
        with self._lock:
            self.calls.append(("vision_interpret", handle, question))
        raise ContractError(
            _SESSION_MANAGER_MESSAGE.format(method="vision_interpret"),
            code="PRECONDITION_FAILED",
        )

    # -- helpers -------------------------------------------------------------

    def _settle(self, future: concurrent.futures.Future) -> None:
        """Give a cancelled worker a moment to release its session, then drop it."""
        with contextlib.suppress(Exception):  # the result is discarded either way
            future.result(timeout=min(self._grace_seconds, 1.0))
        if not future.done():
            future.cancel()

    @staticmethod
    def _timeout_report(subtask_input: SubtaskInput, timeout: float) -> WorkerReport:
        task = subtask_input.subtask
        failure = TypedError(
            "BUDGET_EXCEEDED",
            f"DOM worker did not finish within {timeout:g}s; cancellation requested.",
            False,
            task.subtask_id,
            [],
        )
        report = failed_report(subtask_input, "TimeoutError")
        return WorkerReport(
            report.schema_version,
            WORKER_NAME,
            report.worker_model,
            report.request_id,
            report.subtask_id,
            report.subtask,
            "failed",
            "The DOM worker subtask exceeded its time budget.",
            report.findings,
            report.actions,
            report.evidence,
            report.metrics,
            report.failures,
            report.session_handle,
            [failure],
        )
