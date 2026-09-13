"""The three boundaries the ARGUS controller calls out through.

These are ``typing.Protocol`` classes, so an implementation only has to match
the signatures: the controller imports this module, never a concrete toolbox,
moderator or Ghost.  Tests and the fake implementations satisfy the same
protocols.

Ownership (docs/hackathon/ARGUS.md):

* :class:`Toolbox` (Thomas, Tianqi) performs browser actions and interprets
  pages.  It holds no run state and never retries on its own.
* :class:`Moderator` (Thomas) judges reports, reconciles them and writes the
  final answer.  It returns decisions only; the controller executes them, owns
  budgets, retry caps, sessions and the terminal result.
* :class:`Ghost` (Sting) owns skill meaning: matching, validation and candidate
  compilation.

Every method is expected to be synchronous and to raise rather than return a
half-filled message; the controller maps unexpected exceptions to
``EXTRACTION_FAILED`` without leaking provider text.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from argus.contracts import (
    Event,
    FinalAnswer,
    InterpretedRequest,
    ModeratorDecision,
    Plan,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)

__all__ = ["Toolbox", "Moderator", "ProgressObserver", "Ghost", "Store"]


@runtime_checkable
class Toolbox(Protocol):
    """Browser sessions, subagent execution and page interpretation.

    Used at stage 5 (dispatch) and stage 7 (the controller's own read-only
    verification).  Sessions belong to ARGUS: the toolbox opens and closes them
    on request, and a subagent only ever receives the handle.
    """

    def open_session(self, site_id: str) -> str:
        """Open a browser session on the configured site and return its handle."""

    def close_session(self, handle: str) -> None:
        """Close the session.  Called from the controller's ``finally`` block."""

    def run_subtask(self, subtask_input: SubtaskInput) -> WorkerReport:
        """Run one subtask on the lent session and return its report."""

    def observe(self, handle: str) -> str:
        """Take a fresh read-only observation and return its observation ID."""

    def dom_interpret(self, handle: str, question: str) -> str:
        """Answer a question about the current page from its HTML/DOM."""

    def vision_interpret(self, handle: str, question: str) -> str:
        """Answer a question about the current page from a screenshot."""


@runtime_checkable
class Moderator(Protocol):
    """The model-backed judgement the controller invokes at fixed stages.

    Called at most once per stage per subtask, so model calls stay bounded by
    the plan size.  The moderator cannot change budgets, edit the validator,
    promote a skill or write terminal state.

    ``observe_progress`` (see :class:`ProgressObserver`) is optional; the
    controller checks for it with ``isinstance`` and skips live monitoring when
    an implementation does not provide it.
    """

    def assess_report(
        self,
        subtask: Subtask,
        report: WorkerReport,
        success_conditions: list[str],
    ) -> ModeratorDecision:
        """Stage 7: return ``accept``, ``verify``, ``retry_other_path`` or ``fail``."""

    def reconcile(self, plan: Plan, reports: list[WorkerReport]) -> ModeratorDecision:
        """Stage 8, multi-subtask runs only: merge findings and name gaps and conflicts."""

    def synthesize(
        self,
        interpreted: InterpretedRequest,
        records: list[Any],
        validation: dict[str, Any],
        evidence: list[str],
        failures: list[TypedError],
    ) -> FinalAnswer:
        """Stage 10: write the answer, every claim citing an evidence reference."""


@runtime_checkable
class ProgressObserver(Protocol):
    """Optional live monitoring, if a moderator wants to watch a run.

    The controller calls it on selected events only (subtask started, a stalled
    subtask, every N actions), never on every action, and still owns budgets and
    cancellation.
    """

    def observe_progress(
        self, snapshot: dict[str, Any], event: Event
    ) -> ModeratorDecision:
        """Return ``continue``, ``flag`` or ``stop_subtask`` with a reason."""


@runtime_checkable
class Ghost(Protocol):
    """Skill matching, validation and compilation.

    Sting's demo (``ghostapi/demo/ghost_demo.py``) exposes the same work as
    ``validate_request``/``replay`` (matching and execution), ``verify``
    (validation) and ``qualify`` (promotion).  The phase 3 adapter maps those
    names onto the three methods here; ARGUS does not call his functions
    directly.  Returns are plain dicts because Ghost owns their shape.
    """

    def match(self, subtask: Subtask, skills: list[dict[str, Any]]) -> dict[str, Any]:
        """Stage 4: ``{"decision": "reuse" | "explore", "reason": str, "skill": dict | None}``.

        A partial match falls back to ``explore``; the moderator is not consulted.
        """

    def validate(
        self,
        subtask: Subtask,
        records: list[Any],
        evidence: list[str],
        *,
        report_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stage 9: ``{"status": "passed" | "failed" | "inconclusive", "checks": ...}``.

        Checks are defined independently of the procedure that produced the
        records, and the moderator cannot override a failure.

        For ``kind == "open"`` subtasks only, the controller passes
        ``report_context`` as a keyword: ``{"run_id", "subtask_id",
        "evidence", "empty_state"}`` where ``evidence`` is the accepted
        report's evidence dict with any session handle removed and
        ``empty_state`` is a bool derived from the report (a succeeded report
        whose findings are an explicit empty list, or a findings mapping that
        says so).  Generic checks read it: every record must cite an
        observation of this report, results must be present unless
        ``empty_state`` is true, the query or filter must be evidenced, and no
        record may leave the target domain.  Registry calls keep the original
        three-argument form and never pass the keyword.  This optional keyword
        is a local ARGUS boundary addition awaiting live Ghost receiver
        verification.
        """

    def compile(
        self, report: WorkerReport, subtask: Subtask
    ) -> dict[str, Any] | None:
        """Stage 11: a candidate skill from a successful run, or ``None``.

        Candidate compilation is optional; its failure never erases a valid task
        result, and session handles are never stored in a skill.
        """


@runtime_checkable
class Store(Protocol):
    """Durable JSON storage for runs, events, reports, evidence and skills.

    Implemented by ``argus.store.JsonStore``.  The controller writes through
    this protocol only; session handles must never reach disk.
    """

    def create_run(self, run_id: str, request_id: str, snapshot: dict[str, Any]) -> None: ...

    def save_snapshot(self, run_id: str, snapshot: dict[str, Any]) -> None: ...

    def append_event(self, run_id: str, event: Event) -> None: ...

    def save_report(self, run_id: str, report: WorkerReport) -> None: ...

    def save_evidence_file(self, run_id: str, observation_id: str, source_path: str) -> str: ...

    def save_skill(self, skill: dict[str, Any]) -> None: ...

    def skills(self) -> list[dict[str, Any]]: ...

    def run(self, run_id: str) -> dict[str, Any]: ...

    def events(self, run_id: str) -> list[dict[str, Any]]: ...

    def run_id_for_request(self, request_id: str) -> str | None: ...
