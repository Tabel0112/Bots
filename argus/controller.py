"""The ARGUS controller: one run from request text to one terminal result.

The controller is the single owner of run state, budgets, browser sessions,
the event stream and the terminal result (docs/hackathon/ARGUS.md).  It drives
the stages in order and calls out through the protocols in
:mod:`argus.interfaces`; the moderator and Ghost only return decisions, the
controller executes them and keeps every cap.

Stage state machine (``TRANSITIONS``)::

    created -> interpreting -> gating -> planning -> matching -> dispatching
      -> [reconciling] -> validating -> synthesizing -> publishing -> completed

``dispatching`` covers stages 5 to 7 (dispatch, monitor, report intake), which
run per subtask inside a thread pool.  The terminal states ``completed``,
``failed``, ``cancelled`` and ``needs_input`` are reached only through
:meth:`_RunState.finish`, which writes the result and the terminal event
together, exactly once.

Concurrency: subtasks whose ``depends_on`` are all accepted start together,
bounded by ``max_concurrency`` and by one running subtask per
``concurrency_group``.  Sessions are opened by the controller before a subtask
runs and closed in a ``finally`` block at run end, never by subagents.

Data flow between subtasks: a subtask's ``inputs_from`` bindings are resolved
from the named dependencies' accepted reports just before it is dispatched,
before a session is opened for it.  Field ``findings`` passes the whole
findings object; any other field is collected from every record of a findings
list in order, or read from a findings mapping.  A missing field, or a
dependency without an accepted report, fails the subtask with
``PRECONDITION_FAILED`` and its dependents are cancelled like any other failed
dependency.  Resolved values are placed in ``subtask.parameters`` unchanged.

Nothing that a provider or a worker said verbatim reaches events or results:
unexpected exceptions become ``EXTRACTION_FAILED`` carrying only the exception
class name, and session handles are stripped from every report that is
persisted.

Identity and provenance rules the controller enforces around the moderator and
the workers (they are controller-side checks, not moderator obligations):

* One request identity.  ``run(request, request_id)`` refuses an interpreted
  request whose ``request_id`` differs from the argument (``INVALID_INPUT``),
  the plan must carry the same ID, every ``SubtaskInput`` carries it, and a
  report is taken through intake only if its ``request_id`` and ``subtask_id``
  are the ones dispatched and its ``session_handle`` is ``None`` or the lent
  handle.  A report naming another session is a schema failure; the foreign
  handle is never adopted and never closed.
* Reconciliation cannot add or alter.  Each reconciled record must equal, as
  JSON-normalised data, one of the accepted reports' records; the moderator may
  reorder, drop and supersede, and any other record fails the run with
  ``EXTRACTION_FAILED`` naming it.
* Synthesis is selection, never prose.  The moderator may select validated
  records, their order, fields to state and typed notes.  The controller checks
  every record index, field and note subject against run-owned closed sets,
  derives evidence references, discards any prose-shaped fields, and renders
  every user-facing line itself.  A run never succeeds with a fabricated claim,
  record or model-written assertion.

Wall-clock cutoff: ``max_seconds`` bounds the run, not only the calls between
stages.  When the deadline passes while workers are still inside
``run_subtask``, the controller does not wait for them: each running subtask is
failed with ``BUDGET_EXCEEDED``, its lent session is closed through the toolbox
(the transport-level cutoff for a hung browser call), the pool is shut down
without waiting, and the terminal result is written.  A report that arrives
after that is ignored and logged (``late_report_ignored``), never stored as the
run's report.  The worker *thread* itself cannot be killed: a hung worker may
outlive the run and, since pool threads are joined at interpreter exit, delay
process shutdown.  Toolbox adapters must honour ``Budget.max_seconds``
themselves for a clean stop.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib
import json
import logging
import threading
import time
import uuid
from collections import Counter
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from typing import Any

from argus import interfaces, planner
from argus.contracts import (
    MAX_CONCURRENT_WORKERS,
    SYNTHESIS_FALLBACK_SUBJECTS,
    AnswerSelection,
    Budget,
    Claim,
    ContractError,
    Event,
    FinalAnswer,
    GateDecision,
    InterpretedRequest,
    ModeratorDecision,
    Note,
    Plan,
    RunResult,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)

__all__ = [
    "GATE_REJECT_CODES",
    "MAX_SUBAGENTS",
    "STAGES",
    "TERMINAL",
    "TERMINAL_STATE_FOR_STATUS",
    "TRANSITIONS",
    "Controller",
]

#: Late reports and session closes after the terminal event go here, since the
#: run's event stream is closed once the terminal event is written.
log = logging.getLogger(__name__)
log.addHandler(logging.NullHandler())

#: Per-run worker ceiling: each worker may need one of four local VLM slots.
#: The shared live toolbox must also arbitrate VLM capacity across runs.
MAX_SUBAGENTS = MAX_CONCURRENT_WORKERS

#: Non-terminal stages, in the order a successful run passes through them.
STAGES = (
    "created",
    "interpreting",
    "gating",
    "planning",
    "matching",
    "dispatching",
    "reconciling",
    "validating",
    "synthesizing",
    "publishing",
)

#: Terminal states.  Reached only through ``_RunState.finish``.
TERMINAL = frozenset({"completed", "failed", "cancelled", "needs_input"})

#: Legal forward transitions between non-terminal stages.
TRANSITIONS: dict[str, frozenset[str]] = {
    "created": frozenset({"interpreting"}),
    "interpreting": frozenset({"gating"}),
    "gating": frozenset({"planning"}),
    "planning": frozenset({"matching"}),
    "matching": frozenset({"dispatching"}),
    "dispatching": frozenset({"reconciling", "validating"}),
    "reconciling": frozenset({"validating"}),
    "validating": frozenset({"synthesizing"}),
    "synthesizing": frozenset({"publishing"}),
    "publishing": frozenset(),
}

#: Gate rule -> the typed code a rejection carries.  Most rejections are simply
#: an unusable request (``INVALID_INPUT``), but the two open-world rules reject
#: for a reason the contracts name in their own right, and flattening those into
#: ``INVALID_INPUT`` would hide from the caller *why* the run never started.
GATE_REJECT_CODES = {
    "S2": "DOMAIN_NOT_ALLOWED",
    "S5": "ACTION_CLASS_NOT_ALLOWED",
}

#: RunResult.status -> terminal stage.
TERMINAL_STATE_FOR_STATUS = {
    "succeeded": "completed",
    "failed": "failed",
    "cancelled": "cancelled",
    "needs_input": "needs_input",
}

#: Subtask statuses inside ``dispatching``.
_PENDING, _RUNNING, _ACCEPTED, _FAILED, _CANCELLED = (
    "pending",
    "running",
    "accepted",
    "failed",
    "cancelled",
)

_OTHER_TOOL = {"dom": "vision", "vision": "dom"}

# Fields used only to bind a record to run evidence are never selectable for a
# user-facing claim.  Evidence references are derived separately by the
# controller.
_INTERNAL_RECORD_FIELDS = frozenset({"source_observation_id"})

# These are the only moderator-authored note subjects that are not derived from
# the current request.  They describe a fixed fallback path, never provider
# prose, and are rendered as one generic controller-owned sentence.
_SYNTHESIS_FALLBACK_SUBJECTS = frozenset(SYNTHESIS_FALLBACK_SUBJECTS)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _unexpected(
    exc: BaseException, where: str, step_id: str | None = None
) -> TypedError:
    """Map an unexpected exception to a typed failure that names only its class."""
    return TypedError(
        code="EXTRACTION_FAILED",
        message=f"{where}: unexpected {type(exc).__name__}",
        retryable=False,
        step_id=step_id,
    )


def _strip_handle(report: WorkerReport) -> WorkerReport:
    """A copy of the report without its session handle, for anything persisted."""
    if report.session_handle is None:
        return report
    return dataclasses.replace(report, session_handle=None)


def _records_of(report: WorkerReport) -> list[Any]:
    """Records a report carries.  ``findings`` is worker-owned: a list is taken
    as records, an object with a ``records`` list likewise, anything else is no
    records."""
    findings = report.findings
    if isinstance(findings, list):
        return list(findings)
    if isinstance(findings, dict) and isinstance(findings.get("records"), list):
        return list(findings["records"])
    return []


def _evidence_of(report: WorkerReport) -> list[str]:
    """Observation IDs a report cites: its screenshots plus any verification."""
    refs: list[str] = []
    evidence = report.evidence if isinstance(report.evidence, dict) else {}
    for shot in evidence.get("screenshots") or []:
        if isinstance(shot, str) and shot not in refs:
            refs.append(shot)
    for verification in evidence.get("verifications") or []:
        obs = (
            verification.get("observation_id")
            if isinstance(verification, dict)
            else None
        )
        if isinstance(obs, str) and obs not in refs:
            refs.append(obs)
    return refs


def _canonical(value: Any) -> str:
    """One JSON-normalised text per record: key order and container type do
    not matter, any changed field does.  Non-JSON data falls back to ``repr``."""
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        return repr(value)


def _label(record: Any) -> str:
    """How a record is named in a failure message: its title and url when it
    has them, else the start of its canonical form."""
    if isinstance(record, dict):
        parts = [
            f"{key}={record[key][:80]!r}"
            for key in ("title", "url")
            if isinstance(record.get(key), str) and record[key].strip()
        ]
        if parts:
            return ", ".join(parts)
    return _canonical(record)[:80]


def _first_unsupplied(
    candidates: list[Any], supplied: list[Any]
) -> tuple[int, Any] | None:
    """Index and value of the first candidate that is not one of ``supplied``.

    Compared as JSON-normalised data, as a multiset: a candidate may repeat only
    as often as it was supplied, so reordering, dropping and truncating pass
    while adding, altering or duplicating do not.
    """
    remaining = Counter(_canonical(record) for record in supplied)
    for index, record in enumerate(candidates):
        key = _canonical(record)
        if remaining[key] <= 0:
            return index, record
        remaining[key] -= 1
    return None


def _bound_value(findings: Any, field: str, dependency: str) -> Any:
    """The value one ``inputs_from`` binding reads from a dependency's findings.

    ``findings`` hands over the whole object.  From a list, ``field`` is
    collected from every record in order (an empty list stays empty); from a
    mapping it is that entry.  Anything missing raises ``PRECONDITION_FAILED``
    rather than being skipped, and nothing is transformed on the way.
    """
    if field == "findings":
        return copy.deepcopy(findings)
    if isinstance(findings, list):
        values = []
        for index, record in enumerate(findings):
            if not isinstance(record, dict) or field not in record:
                raise ContractError(
                    f"record {index} of {dependency}'s findings has no field {field!r}",
                    code="PRECONDITION_FAILED",
                )
            values.append(copy.deepcopy(record[field]))
        return values
    if isinstance(findings, dict):
        if field not in findings:
            raise ContractError(
                f"{dependency}'s findings have no field {field!r}",
                code="PRECONDITION_FAILED",
            )
        return copy.deepcopy(findings[field])
    raise ContractError(
        f"{dependency}'s findings are not a list or mapping, so field {field!r} cannot be read",
        code="PRECONDITION_FAILED",
    )


def _empty_state_of(report: WorkerReport) -> bool:
    """Whether a succeeded report explicitly says there was nothing to find:
    an empty findings list, or a findings mapping flagged ``empty_state`` or
    holding an empty ``records`` list.  No findings at all is not an empty
    state, it is missing evidence."""
    if report.outcome != "succeeded":
        return False
    findings = report.findings
    if isinstance(findings, list):
        return not findings
    if isinstance(findings, dict):
        if findings.get("empty_state") is True:
            return True
        records = findings.get("records")
        return isinstance(records, list) and not records
    return False


def _criterion_subjects(interpreted: InterpretedRequest) -> dict[str, Any]:
    """Closed note-subject set for criteria in this interpreted request."""
    return {
        f"intent-{intent_index}:criterion-{criterion_index}": criterion
        for intent_index, intent in enumerate(interpreted.intents)
        for criterion_index, criterion in enumerate(intent.criteria)
    }


def _render_value(value: Any) -> str:
    """Deterministic, JSON-shaped display of a validated record field."""
    try:
        rendered = json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
        return rendered.replace("\\n", " ").replace("\\r", " ")
    except (TypeError, ValueError):
        return '"[unsupported value]"'


def _render_note(note: Note, criteria: dict[str, Any]) -> str:
    """Render a validated typed note without echoing its subject verbatim."""
    if note.kind == "criterion_not_applied":
        criterion = criteria[note.subject]
        return f"Criterion {criterion.text!r} could not be applied."
    if note.kind == "synthesis_fallback":
        return "The deterministic synthesis fallback was used."
    if note.kind == "reconciliation_unresolved":
        return "Reconciliation left an unresolved gap or conflict."
    if note.kind == "validation_not_passed":
        return f"Validation status is {note.subject}."
    if note.kind == "clarification_required":
        return "More information is required before execution."
    raise AssertionError(f"unhandled note kind {note.kind!r}")


def _discard_moderator_prose(value: Any, path: str = "") -> tuple[Any, list[str]]:
    """Remove prose/controller-owned fields before strict selection decoding."""
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        discarded: list[str] = []
        for key, item in value.items():
            field_path = f"{path}.{key}" if path else key
            controller_owned = not path and key in {
                "text",
                "lines",
                "records",
                "failures",
                "unverified",
            }
            if key == "text" or controller_owned:
                discarded.append(field_path)
                continue
            nested, nested_discarded = _discard_moderator_prose(item, field_path)
            cleaned[key] = nested
            discarded.extend(nested_discarded)
        return cleaned, discarded
    if isinstance(value, list):
        cleaned_items: list[Any] = []
        discarded = []
        for index, item in enumerate(value):
            cleaned, nested_discarded = _discard_moderator_prose(
                item, f"{path}[{index}]"
            )
            cleaned_items.append(cleaned)
            discarded.extend(nested_discarded)
        return cleaned_items, discarded
    return copy.deepcopy(value), []


def _render_answer_lines(
    records: list[Any],
    claims: list[Claim],
    validation_status: str,
    rendered_notes: list[str],
    failures: list[TypedError],
) -> list[str]:
    """Render every user-facing line from controller-validated structures."""
    noun = "record" if len(records) == 1 else "records"
    lines = [f"{len(records)} selected {noun}; validation {validation_status}."]
    for claim in claims:
        record = records[claim.record_index]
        assert isinstance(record, dict)
        fields = "; ".join(
            f"{name}: {_render_value(record[name])}" for name in claim.fields
        )
        lines.append(f"Record {claim.record_index + 1} — {fields}.")
    lines.extend(rendered_notes)
    lines.extend(f"Failure: {failure.code}." for failure in failures)
    return lines


class _RunState:
    """Run state, event sequence and the single terminal result for one run.

    Ported from ``backend/argus/state.py``: transitions are checked against
    ``TRANSITIONS``, terminal states are only entered by :meth:`finish`, which
    stores the result and appends the terminal event under one lock, and no
    event may be emitted after that.
    """

    def __init__(self, run_id: str, request_id: str, store: interfaces.Store) -> None:
        self.run_id = run_id
        self.request_id = request_id
        self.store = store
        self.state = "created"
        self.sequence = 0
        self.result: RunResult | None = None
        self.started = time.monotonic()
        self.lock = threading.RLock()
        self.cancel_requested = threading.Event()
        self.events: list[Event] = []
        self.late_events: list[
            Event
        ] = []  # after the terminal event; logged, not stored
        self.sessions: dict[str, str] = {}  # handle -> subtask_id, still open
        self.sessions_opened = 0
        self.closing = (
            False  # set once run-end cleanup starts; no session may register after
        )
        self.interpreted: InterpretedRequest | None = None
        self.gate: GateDecision | None = None
        self.plan: Plan | None = None
        self.validation: dict[str, Any] | None = None
        self.answer: FinalAnswer | None = None
        self.moderator_calls = 0
        self.subtasks: dict[str, _SubtaskState] = {}

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    @property
    def finished(self) -> bool:
        return self.result is not None

    def note(
        self, type: str, message: str, data: dict[str, Any] | None = None
    ) -> Event:
        """Emit while the stream is open; after the terminal event keep the
        event in memory and log it.  Nothing may follow the terminal event on
        disk, but what a worker thread does after the run ended must still be
        visible somewhere."""
        with self.lock:
            if self.result is None:
                return self.emit(type, message, data)
            self.sequence += 1
            event = Event(
                run_id=self.run_id,
                sequence=self.sequence,
                timestamp=_utc_now(),
                type=type,
                stage=self.state,
                message=message,
                data=dict(data or {}),
            )
            self.late_events.append(event)
        log.warning(
            "run %s: %s after the terminal event: %s %s",
            self.run_id,
            type,
            message,
            event.data,
        )
        return event

    def emit(
        self, type: str, message: str, data: dict[str, Any] | None = None
    ) -> Event:
        with self.lock:
            if self.result is not None:
                raise RuntimeError(
                    f"run {self.run_id}: cannot emit after the terminal event"
                )
            self.sequence += 1
            event = Event(
                run_id=self.run_id,
                sequence=self.sequence,
                timestamp=_utc_now(),
                type=type,
                stage=self.state,
                message=message,
                data=dict(data or {}),
            )
            self.events.append(event)
            self.store.append_event(self.run_id, event)
            return event

    def transition(self, state: str) -> None:
        with self.lock:
            if self.result is not None or state not in TRANSITIONS.get(self.state, ()):
                raise RuntimeError(
                    f"run {self.run_id}: illegal transition {self.state} -> {state}"
                )
            self.state = state
            self.emit("stage_changed", state)
            self.store.save_snapshot(self.run_id, self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        """In-progress view of the run.  Not a RunResult: those carry only
        terminal statuses, so the snapshot uses ``"running"`` and the stage."""
        with self.lock:
            return {
                "run_id": self.run_id,
                "request_id": self.request_id,
                "status": "running",
                "stage": self.state,
                "sequence": self.sequence,
                "elapsed_seconds": round(self.elapsed(), 3),
                "subtasks": {sid: st.status for sid, st in self.subtasks.items()},
            }

    def finish(self, result: RunResult) -> RunResult:
        """Write the terminal result and its event together, exactly once."""
        with self.lock:
            if self.result is not None:
                raise RuntimeError(f"run {self.run_id}: already finished")
            if result.run_id != self.run_id:
                raise RuntimeError("result belongs to another run")
            terminal = TERMINAL_STATE_FOR_STATUS[result.status]
            if terminal == "completed" and self.state != "publishing":
                raise RuntimeError(
                    f"run {self.run_id}: success is only legal from publishing"
                )
            self.state = terminal
            summary = {
                "status": result.status,
                "error": result.error.to_dict() if result.error else None,
                "claims": len(result.answer.claims) if result.answer else 0,
            }
            self.emit(f"run_{terminal}", f"run {result.status}", summary)
            self.result = result
            self.store.save_snapshot(self.run_id, result.to_dict())
            return result


@dataclasses.dataclass
class _SubtaskState:
    subtask: Subtask
    mode: str = "explore"
    bound_procedure: dict[str, Any] | None = None
    status: str = _PENDING
    handle: str | None = None
    report: WorkerReport | None = None
    failure: TypedError | None = None
    verify_used: bool = False
    retry_used: bool = False
    force_verify: bool = False
    stop_requested: str | None = None
    actions_used: int = 0
    started: float | None = None
    abandoned: bool = False  # the run stopped waiting for it after a budget breach

    @property
    def subtask_id(self) -> str:
        return self.subtask.subtask_id


class Controller:
    """Drive one request through every stage and publish one result.

    All collaborators are injected through the protocols in
    :mod:`argus.interfaces`.  ``interpret``, ``gate`` and ``plan`` may be
    injected too; when they are not, ``argus.interpreter.interpret``,
    ``argus.gate.gate`` and :func:`argus.planner.plan` are imported lazily at
    the stage that needs them.
    """

    def __init__(
        self,
        toolbox: interfaces.Toolbox,
        moderator: interfaces.Moderator,
        ghost: interfaces.Ghost,
        store: interfaces.Store,
        max_actions: int = 30,
        max_seconds: float = 120,
        max_concurrency: int = 2,
        *,
        interpret: Callable[[str, str], InterpretedRequest] | None = None,
        gate: Callable[[InterpretedRequest], GateDecision] | None = None,
        plan: Callable[[InterpretedRequest, str], Plan] | None = None,
    ) -> None:
        if type(max_concurrency) is not int:
            raise ValueError("max_concurrency must be an integer from 1 to 4")
        if max_actions < 1 or max_seconds <= 0 or max_concurrency < 1:
            raise ValueError(
                "max_actions, max_seconds and max_concurrency must be positive"
            )
        if max_concurrency > MAX_SUBAGENTS:
            raise ValueError(f"max_concurrency must be at most {MAX_SUBAGENTS}")
        self.toolbox = toolbox
        self.moderator = moderator
        self.ghost = ghost
        self.store = store
        self.max_actions = int(max_actions)
        self.max_seconds = float(max_seconds)
        self.max_concurrency = int(max_concurrency)
        self._interpret = interpret
        self._gate = gate
        self._plan = plan
        self._observer = (
            moderator if isinstance(moderator, interfaces.ProgressObserver) else None
        )
        self._runs: dict[str, _RunState] = {}

    # ------------------------------------------------------------------ public

    def run(
        self,
        text_or_interpreted: str | InterpretedRequest,
        request_id: str,
        run_id: str | None = None,
    ) -> RunResult:
        """Run one request to its single terminal result.

        Every session the run opened is closed before the terminal event, even
        when a stage raises.  The returned result is also the last snapshot in
        the store.
        """
        given = None
        if isinstance(text_or_interpreted, InterpretedRequest):
            given = text_or_interpreted.request_id
        elif isinstance(text_or_interpreted, dict):
            given = text_or_interpreted.get("request_id")
        if given is not None and given != request_id:
            raise ContractError(
                f"interpreted request_id {given!r} differs from the run's request_id "
                f"{request_id!r}; a run has exactly one request identity",
                code="INVALID_INPUT",
            )
        run_id = run_id or f"run-{uuid.uuid4().hex[:12]}"
        state = _RunState(run_id, request_id, self.store)
        self._runs[run_id] = state
        self.store.create_run(run_id, request_id, state.snapshot())
        state.emit("run_created", "run created", {"request_id": request_id})
        result: RunResult | None = None
        try:
            result = self._drive(state, text_or_interpreted, request_id)
        except _Finished as done:
            result = done.result
        except ContractError as exc:
            result = self._failed(state, exc.typed_error)
        except Exception as exc:  # noqa: BLE001 - every failure must become typed
            result = self._failed(state, _unexpected(exc, f"stage {state.state}"))
        finally:
            self._close_sessions(state)
        return state.finish(result)

    def cancel(self, run_id: str) -> None:
        """Ask a running run to stop; it ends ``cancelled`` at the next check."""
        state = self._runs.get(run_id)
        if state is not None:
            state.cancel_requested.set()

    # ------------------------------------------------------------------ stages

    def _drive(
        self,
        state: _RunState,
        text_or_interpreted: str | InterpretedRequest,
        request_id: str,
    ) -> RunResult:
        # 1. interpret
        state.transition("interpreting")
        state.interpreted = self._stage_interpret(
            state, text_or_interpreted, request_id
        )
        state.emit(
            "interpreted",
            f"{len(state.interpreted.intents)} intent(s)",
            {
                "intents": len(state.interpreted.intents),
                "missing_required": len(state.interpreted.missing_required),
                "ambiguities": len(state.interpreted.ambiguities),
            },
        )
        self._check_run_limits(state)

        # 2. gate
        state.transition("gating")
        gate = self._gate or self._import("argus.gate", "gate")
        state.gate = gate(state.interpreted)
        if not isinstance(state.gate, GateDecision):
            state.gate = GateDecision.from_dict(state.gate)
        state.emit(
            "gate_decided",
            state.gate.decision,
            {
                "rule_id": state.gate.rule_id,
                "reason": state.gate.reason,
                "questions": list(state.gate.questions),
            },
        )
        if state.gate.decision == "clarify":
            return self._result(
                state,
                "needs_input",
                error=TypedError(
                    code="NEEDS_INPUT",
                    message=state.gate.reason,
                    retryable=True,
                ),
                answer=FinalAnswer(
                    lines=list(state.gate.questions) or [state.gate.reason],
                    notes=[Note("clarification_required", "gate")],
                ),
            )
        if state.gate.decision == "reject":
            return self._result(
                state,
                "failed",
                error=TypedError(
                    code=GATE_REJECT_CODES.get(state.gate.rule_id, "INVALID_INPUT"),
                    message=f"{state.gate.rule_id}: {state.gate.reason}",
                    retryable=False,
                ),
            )
        self._check_run_limits(state)

        # 3. plan
        state.transition("planning")
        plan_fn = self._plan or planner.plan
        state.plan = plan_fn(state.interpreted, f"plan-{state.run_id}")
        if not isinstance(state.plan, Plan):
            state.plan = Plan.from_dict(state.plan)
        planner.validate_plan(state.plan)
        if state.plan.request_id != state.request_id:
            raise ContractError(
                f"plan request_id {state.plan.request_id!r} is not the run's "
                f"{state.request_id!r}; a run has exactly one request identity",
                code="INVALID_INPUT",
            )
        state.subtasks = {
            s.subtask_id: _SubtaskState(subtask=s) for s in state.plan.subtasks
        }
        state.emit(
            "plan_created",
            f"{len(state.plan.subtasks)} subtask(s)",
            {
                "plan_id": state.plan.plan_id,
                "subtasks": [
                    {
                        "subtask_id": s.subtask_id,
                        "operation": s.operation,
                        "depends_on": list(s.depends_on),
                        "concurrency_group": s.concurrency_group,
                    }
                    for s in state.plan.subtasks
                ],
            },
        )
        self._check_run_limits(state)

        # 4. match
        state.transition("matching")
        self._stage_match(state)
        self._check_run_limits(state)

        # 5-7. dispatch, monitor, intake
        state.transition("dispatching")
        self._stage_dispatch(state)
        self._check_run_limits(state)

        accepted = [st for st in state.subtasks.values() if st.status == _ACCEPTED]
        failures = [
            st.failure for st in state.subtasks.values() if st.failure is not None
        ]
        records: list[Any] = []
        evidence: list[str] = []
        controller_notes: list[Note] = []
        for st in accepted:
            assert st.report is not None
            records.extend(_records_of(st.report))
            for ref in _evidence_of(st.report):
                if ref not in evidence:
                    evidence.append(ref)

        # 8. reconcile, multi-subtask runs only
        if len(state.plan.subtasks) > 1 and accepted:
            state.transition("reconciling")
            records, controller_notes = self._stage_reconcile(state, accepted, records)
            self._check_run_limits(state)

        # 9. validate
        state.transition("validating")
        state.validation = self._stage_validate(state, accepted, evidence)
        self._check_run_limits(state)

        # 10. synthesize
        state.transition("synthesizing")
        state.answer = self._stage_synthesize(
            state, accepted, records, evidence, failures, controller_notes
        )
        self._check_run_limits(state)

        # 11. publish
        state.transition("publishing")
        if not accepted:
            # nothing came back: the first subtask failure is the run's failure
            return self._result(
                state,
                "failed",
                error=failures[0]
                if failures
                else TypedError(
                    code="EXTRACTION_FAILED",
                    message="no subtask produced a report",
                    retryable=False,
                ),
            )
        if state.validation["status"] != "passed":
            return self._result(
                state,
                "failed",
                error=TypedError(
                    code="VALIDATION_FAILED",
                    message=f"validation {state.validation['status']}",
                    retryable=state.validation["status"] == "inconclusive",
                    evidence_refs=list(evidence),
                ),
            )
        if failures:
            return self._result(state, "failed", error=failures[0])
        self._stage_compile(state, accepted)
        return self._result(state, "succeeded")

    def _stage_interpret(
        self,
        state: _RunState,
        text_or_interpreted: str | InterpretedRequest,
        request_id: str,
    ) -> InterpretedRequest:
        if isinstance(text_or_interpreted, InterpretedRequest):
            return text_or_interpreted
        if isinstance(text_or_interpreted, dict):
            return InterpretedRequest.from_dict(text_or_interpreted)
        if not isinstance(text_or_interpreted, str) or not text_or_interpreted.strip():
            raise ContractError("request text must be a non-empty string")
        interpret = self._interpret or self._import("argus.interpreter", "interpret")
        interpreted = interpret(text_or_interpreted, request_id)
        if not isinstance(interpreted, InterpretedRequest):
            interpreted = InterpretedRequest.from_dict(interpreted)
        if interpreted.request_id != request_id:
            raise ContractError(
                f"interpreter returned request_id {interpreted.request_id!r} for "
                f"request {request_id!r}; a run has exactly one request identity",
                code="INVALID_INPUT",
            )
        return interpreted

    def _stage_match(self, state: _RunState) -> None:
        try:
            skills = list(self.store.skills())
        except Exception as exc:  # noqa: BLE001
            state.emit(
                "skills_unavailable", f"store.skills raised {type(exc).__name__}"
            )
            skills = []
        for st in state.subtasks.values():
            decision, reason, skill = "explore", "", None
            try:
                match = self.ghost.match(st.subtask, skills) or {}
                decision = match.get("decision", "explore")
                reason = str(match.get("reason", ""))
                skill = match.get("skill")
            except Exception as exc:  # noqa: BLE001
                reason = f"ghost.match raised {type(exc).__name__}"
            if decision == "reuse" and isinstance(skill, dict):
                st.mode, st.bound_procedure = "reuse", dict(skill)
            else:
                st.mode, st.bound_procedure = "explore", None
                if decision == "reuse":
                    reason = reason or "reuse without a skill falls back to explore"
            state.emit(
                "match_decided",
                f"{st.subtask_id}: {st.mode}",
                {
                    "subtask_id": st.subtask_id,
                    "mode": st.mode,
                    "reason": reason,
                    "skill_id": (st.bound_procedure or {}).get("skill_id"),
                },
            )

    # ---------------------------------------------------------------- dispatch

    def _stage_dispatch(self, state: _RunState) -> None:
        """Stages 5 to 7 for every subtask, bounded by the concurrency limits."""
        pending = [st for st in state.subtasks.values()]
        running: dict[Future, _SubtaskState] = {}
        budget_breached = False
        pool = ThreadPoolExecutor(max_workers=self.max_concurrency)
        try:
            while pending or running:
                if state.cancel_requested.is_set() or budget_breached:
                    reason = (
                        "run budget exceeded" if budget_breached else "run cancelled"
                    )
                    for st in pending:
                        self._cancel_subtask(state, st, reason)
                    pending = []
                    if not running:
                        break

                # cancel dependents of failed or cancelled subtasks
                for st in list(pending):
                    blocked = [
                        dep
                        for dep in st.subtask.depends_on
                        if state.subtasks[dep].status in (_FAILED, _CANCELLED)
                    ]
                    if blocked:
                        pending.remove(st)
                        self._cancel_subtask(
                            state, st, f"dependency {blocked[0]} did not succeed"
                        )

                # start everything that is ready, within the caps
                busy_groups = {st.subtask.concurrency_group for st in running.values()}
                for st in list(pending):
                    if len(running) >= self.max_concurrency:
                        break
                    deps_ok = all(
                        state.subtasks[d].status == _ACCEPTED
                        for d in st.subtask.depends_on
                    )
                    if not deps_ok or st.subtask.concurrency_group in busy_groups:
                        continue
                    pending.remove(st)
                    st.status = _RUNNING
                    st.started = time.monotonic()
                    busy_groups.add(st.subtask.concurrency_group)
                    running[pool.submit(self._execute_subtask, state, st)] = st

                if not running:
                    if pending:  # nothing ready and nothing running: unschedulable
                        for st in pending:
                            self._cancel_subtask(
                                state, st, "no schedulable path to this subtask"
                            )
                        pending = []
                    break

                remaining = self.max_seconds - state.elapsed()
                done, _ = wait(
                    running, timeout=max(remaining, 0.0), return_when=FIRST_COMPLETED
                )
                if not done:
                    budget_breached = True
                    state.emit(
                        "budget_exceeded",
                        "run exceeded max_seconds",
                        {
                            "max_seconds": self.max_seconds,
                            "elapsed_seconds": round(state.elapsed(), 3),
                        },
                    )
                    # The run does not wait for a worker that is still inside
                    # run_subtask: each one is failed now, its session is
                    # closed, and whatever it returns later is ignored.
                    for st in running.values():
                        self._abandon_after_breach(state, st)
                    running = {}
                    continue
                for future in done:
                    st = running.pop(future)
                    exc = future.exception()
                    if exc is not None:
                        self._fail_subtask(
                            state, st, _unexpected(exc, st.subtask_id, st.subtask_id)
                        )
                    elif st.status == _RUNNING:
                        self._fail_subtask(
                            state,
                            st,
                            TypedError(
                                code="EXTRACTION_FAILED",
                                message="subtask ended without a decision",
                                retryable=False,
                                step_id=st.subtask_id,
                            ),
                        )
        finally:
            # After a breach the pool is not waited for: waiting on a hung
            # worker would hold the run open past max_seconds.
            pool.shutdown(wait=not budget_breached, cancel_futures=True)
        if budget_breached:
            raise _Finished(
                self._result(
                    state,
                    "failed",
                    error=TypedError(
                        code="BUDGET_EXCEEDED",
                        message=f"run exceeded max_seconds={self.max_seconds:g}",
                        retryable=False,
                    ),
                )
            )
        if state.cancel_requested.is_set():
            raise _Finished(
                self._result(
                    state,
                    "cancelled",
                    error=TypedError(
                        code="CANCELLED",
                        message="run cancelled",
                        retryable=False,
                    ),
                )
            )

    def _execute_subtask(self, state: _RunState, st: _SubtaskState) -> None:
        """Open a session, run the subtask, take the report through intake.

        Runs on a pool thread.  Never raises: every outcome is written to ``st``.
        """
        sid = st.subtask_id
        try:
            if self._abandoned(state, st):
                return
            self._observe(
                state,
                st,
                state.emit(
                    "subtask_started",
                    sid,
                    {
                        "subtask_id": sid,
                        "mode": st.mode,
                        "preferred_tool": st.subtask.preferred_tool,
                    },
                ),
            )
            if st.stop_requested is not None:
                self._fail_subtask(
                    state,
                    st,
                    TypedError(
                        code="CANCELLED",
                        message=f"stopped by moderator: {st.stop_requested}",
                        retryable=False,
                        step_id=sid,
                    ),
                )
                return
            if not self._resolve_inputs(state, st):
                return
            handle = self.toolbox.open_session(st.subtask.site_id)
            with state.lock:
                too_late = state.closing or st.abandoned
                if not too_late:
                    st.handle = handle
                    state.sessions[handle] = sid
                    state.sessions_opened += 1
            if too_late:
                # The run stopped waiting for this subtask while the session
                # was being opened; nothing may run on it.
                self._close_session(state, handle, sid)
                return
            state.emit("session_opened", sid, {"subtask_id": sid})

            report = self._run_worker(state, st, st.subtask)
            if report is None:
                return
            self._intake(state, st, report)
        except Exception as exc:  # noqa: BLE001 - the pool must not see it
            self._fail_subtask(state, st, _unexpected(exc, sid, sid))

    def _resolve_inputs(self, state: _RunState, st: _SubtaskState) -> bool:
        """Fill the subtask's ``inputs_from`` bindings from accepted reports.

        Replaces ``st.subtask`` with a copy whose ``parameters`` carry the bound
        values, so the worker, a retry, validation and compilation all see the
        same resolved subtask; the plan itself is untouched.  Returns ``False``
        after failing the subtask when a binding cannot be resolved.
        """
        subtask = st.subtask
        if not subtask.inputs_from:
            return True
        sid = st.subtask_id
        resolved: dict[str, Any] = {}
        try:
            for parameter, source in subtask.inputs_from.items():
                dependency = state.subtasks.get(source["subtask_id"])
                with state.lock:
                    report = (
                        dependency.report
                        if dependency is not None and dependency.status == _ACCEPTED
                        else None
                    )
                if report is None:
                    raise ContractError(
                        f"dependency {source['subtask_id']!r} has no accepted report "
                        f"to read {parameter!r} from",
                        code="PRECONDITION_FAILED",
                    )
                resolved[parameter] = _bound_value(
                    report.findings, source["field"], source["subtask_id"]
                )
        except ContractError as exc:
            self._fail_subtask(
                state,
                st,
                TypedError(
                    code="PRECONDITION_FAILED",
                    message=f"{sid}: {exc}",
                    retryable=False,
                    step_id=sid,
                ),
            )
            return False
        parameters = (
            dict(subtask.parameters) if isinstance(subtask.parameters, dict) else {}
        )
        parameters.update(resolved)
        st.subtask = dataclasses.replace(subtask, parameters=parameters)
        state.emit(
            "subtask_inputs_resolved",
            sid,
            {
                "subtask_id": sid,
                "bindings": {
                    parameter: dict(source)
                    for parameter, source in subtask.inputs_from.items()
                },
            },
        )
        return True

    def _run_worker(
        self, state: _RunState, st: _SubtaskState, subtask: Subtask
    ) -> WorkerReport | None:
        """Run the worker once on the lent session; ``None`` when the subtask
        failed (already recorded)."""
        sid = st.subtask_id
        remaining_actions = self.max_actions - st.actions_used
        remaining_seconds = min(
            self.max_seconds - (time.monotonic() - (st.started or time.monotonic())),
            self.max_seconds - state.elapsed(),
        )
        if remaining_actions <= 0 or remaining_seconds <= 0:
            self._fail_subtask(
                state, st, self._budget_error(st, "budget exhausted before start")
            )
            return None
        subtask_input = SubtaskInput(
            run_id=state.run_id,
            subtask=subtask,
            session_handle=st.handle,
            budget=Budget(max_actions=remaining_actions, max_seconds=remaining_seconds),
            mode=st.mode,
            bound_procedure=st.bound_procedure,
            request_id=state.request_id,
        )
        try:
            raw = self.toolbox.run_subtask(subtask_input)
        except Exception as exc:  # noqa: BLE001
            self._fail_subtask(state, st, _unexpected(exc, f"{sid} run_subtask", sid))
            return None
        if self._abandoned(state, st):
            # The run stopped waiting for this subtask (budget breach) or has
            # already published: the report is not this run's report.
            outcome = getattr(raw, "outcome", None)
            if isinstance(raw, dict):
                outcome = raw.get("outcome")
            state.note(
                "late_report_ignored",
                sid,
                {
                    "subtask_id": sid,
                    "outcome": outcome if isinstance(outcome, str) else None,
                    "reason": "report arrived after the run stopped waiting for this subtask",
                },
            )
            return None

        # schema check, and the binding to this request and the lent session
        try:
            report = (
                raw if isinstance(raw, WorkerReport) else WorkerReport.from_dict(raw)
            )
            if report.subtask_id != sid:
                raise ContractError(
                    f"report subtask_id {report.subtask_id!r} is not {sid!r}"
                )
            if report.request_id != state.request_id:
                raise ContractError(
                    f"report request_id {report.request_id!r} is not this run's "
                    f"{state.request_id!r}"
                )
            if report.session_handle is not None and report.session_handle != st.handle:
                # The foreign handle is named nowhere: not adopted, not closed,
                # not written to an event.
                raise ContractError(
                    "report returned a session handle that is not the lent one"
                )
        except ContractError as exc:
            self._fail_subtask(
                state,
                st,
                TypedError(
                    code="EXTRACTION_FAILED",
                    message=f"report failed schema check: {exc}",
                    retryable=False,
                    step_id=sid,
                ),
            )
            return None
        st.report = report
        self.store.save_report(state.run_id, _strip_handle(report))

        # budgets (stage 6): actions from the report, wall clock from the controller
        count = (
            report.metrics.get("browser_action_count")
            if isinstance(report.metrics, dict)
            else None
        )
        if isinstance(count, int) and not isinstance(count, bool):
            st.actions_used += max(count, 0)
        elapsed = time.monotonic() - (st.started or time.monotonic())
        self._observe(
            state,
            st,
            state.emit(
                "subtask_report_received",
                sid,
                {
                    "subtask_id": sid,
                    "outcome": report.outcome,
                    "browser_action_count": count,
                    "elapsed_seconds": round(elapsed, 3),
                    "preferred_tool": subtask.preferred_tool,
                },
            ),
        )
        for index, action in enumerate(report.actions if isinstance(report.actions, list) else []):
            if not isinstance(action, dict):
                continue
            operation = action.get("action")
            name = operation.get("name") if isinstance(operation, dict) else None
            state.emit(
                "worker_action_recorded",
                f"{sid}: {name or 'browser action'}",
                {
                    "subtask_id": sid,
                    "step_id": action.get("step_id") or f"action-{index + 1}",
                    "action": name,
                    "semantic_target": action.get("semantic_target"),
                    "outcome": action.get("outcome"),
                    "observation_before": action.get("observation_before"),
                    "observation_after": action.get("observation_after"),
                    "url": action.get("url"),
                },
            )
        if st.actions_used > self.max_actions:
            self._fail_subtask(
                state,
                st,
                self._budget_error(
                    st,
                    f"{st.actions_used} browser actions exceed max_actions={self.max_actions}",
                ),
            )
            return None
        if elapsed > self.max_seconds:
            self._fail_subtask(
                state,
                st,
                self._budget_error(
                    st, f"{elapsed:.1f}s exceed max_seconds={self.max_seconds:g}"
                ),
            )
            return None
        if st.stop_requested is not None:
            self._fail_subtask(
                state,
                st,
                TypedError(
                    code="CANCELLED",
                    message=f"stopped by moderator: {st.stop_requested}",
                    retryable=False,
                    step_id=sid,
                ),
            )
            return None
        return report

    def _budget_error(self, st: _SubtaskState, detail: str) -> TypedError:
        return TypedError(
            code="BUDGET_EXCEEDED",
            message=f"{st.subtask_id}: {detail}",
            retryable=False,
            step_id=st.subtask_id,
        )

    # ------------------------------------------------------------------ intake

    def _intake(
        self, state: _RunState, st: _SubtaskState, report: WorkerReport
    ) -> None:
        """Stage 7: assess, then execute the decision under the controller's caps.

        Bounded: at most one verification and one retry per subtask, so the
        loop runs at most four assessments.
        """
        sid = st.subtask_id
        current = st.subtask
        for _ in range(4):
            if self._abandoned(state, st):
                state.note(
                    "late_report_ignored",
                    sid,
                    {
                        "subtask_id": sid,
                        "reason": "intake stopped: the run no longer waits for this subtask",
                    },
                )
                return
            if st.force_verify and not st.verify_used:
                self._verify(state, st, report, None)
            decision = self._assess(state, st, report)
            if decision is None:
                return
            if decision.decision == "accept":
                with state.lock:
                    accepted = st.status == _RUNNING and not st.abandoned
                    if accepted:
                        st.status = _ACCEPTED
                if not accepted:
                    state.note(
                        "late_report_ignored",
                        sid,
                        {
                            "subtask_id": sid,
                            "reason": "accepted after the run stopped waiting for it",
                        },
                    )
                    return
                state.emit(
                    "subtask_accepted",
                    sid,
                    {
                        "subtask_id": sid,
                        "reason": decision.reason,
                        "evidence_refs": list(decision.evidence_refs),
                    },
                )
                return
            if decision.decision == "verify":
                if st.verify_used:
                    self._fail_subtask(
                        state,
                        st,
                        TypedError(
                            code="EXTRACTION_FAILED",
                            message=f"{sid}: verification cap reached; still unconfirmed: {decision.reason}",
                            retryable=False,
                            step_id=sid,
                            evidence_refs=list(decision.evidence_refs),
                        ),
                    )
                    return
                self._verify(state, st, report, decision)
                continue
            if decision.decision == "retry_other_path":
                if st.retry_used:
                    failure = self._carried_failure(
                        report, sid, decision.reason, "retry cap reached"
                    )
                    self._fail_subtask(state, st, failure)
                    return
                st.retry_used = True
                current = dataclasses.replace(
                    current, preferred_tool=_OTHER_TOOL[current.preferred_tool]
                )
                state.emit(
                    "subtask_retried",
                    sid,
                    {
                        "subtask_id": sid,
                        "preferred_tool": current.preferred_tool,
                        "reason": decision.reason,
                    },
                )
                retried = self._run_worker(state, st, current)
                if retried is None:
                    return
                report = retried
                continue
            # fail
            self._fail_subtask(
                state,
                st,
                self._carried_failure(
                    report, sid, decision.reason, "moderator failed the report"
                ),
            )
            return
        self._fail_subtask(
            state,
            st,
            TypedError(
                code="EXTRACTION_FAILED",
                message=f"{sid}: intake did not converge",
                retryable=False,
                step_id=sid,
            ),
        )

    def _assess(
        self, state: _RunState, st: _SubtaskState, report: WorkerReport
    ) -> ModeratorDecision | None:
        sid = st.subtask_id
        try:
            with state.lock:
                state.moderator_calls += 1
            # A copy: the moderator judges the report, it does not edit it.
            decision = self.moderator.assess_report(
                st.subtask, copy.deepcopy(report), list(st.subtask.success_conditions)
            )
            if not isinstance(decision, ModeratorDecision):
                decision = ModeratorDecision.from_dict(decision)
            if decision.stage != "assess":
                raise ContractError(f"assess_report returned stage {decision.stage!r}")
        except ContractError as exc:
            self._fail_subtask(
                state,
                st,
                TypedError(
                    code="EXTRACTION_FAILED",
                    message=f"{sid}: moderator decision invalid: {exc}",
                    retryable=False,
                    step_id=sid,
                ),
            )
            return None
        except Exception as exc:  # noqa: BLE001
            self._fail_subtask(state, st, _unexpected(exc, f"{sid} assess_report", sid))
            return None
        state.emit(
            "report_assessed",
            f"{sid}: {decision.decision}",
            {
                "subtask_id": sid,
                "decision": decision.decision,
                "reason": decision.reason,
            },
        )
        return decision

    def _verify(
        self,
        state: _RunState,
        st: _SubtaskState,
        report: WorkerReport,
        decision: ModeratorDecision | None,
    ) -> None:
        """One read-only verification on the lent session; its result is added
        to the report's evidence so the next assessment can see it."""
        sid = st.subtask_id
        if self._abandoned(state, st):
            return
        st.verify_used = True
        st.force_verify = False
        question = None
        if decision is not None and isinstance(decision.next_action, dict):
            question = decision.next_action.get("question")
        if not isinstance(question, str) or not question.strip():
            question = (
                "Confirm each of the following on the current page: "
                + "; ".join(st.subtask.success_conditions)
            )
        tool = (
            st.subtask.preferred_tool
            if not st.retry_used
            else _OTHER_TOOL[st.subtask.preferred_tool]
        )
        observation = self.toolbox.observe(st.handle)
        interpret = (
            self.toolbox.vision_interpret
            if tool == "vision"
            else self.toolbox.dom_interpret
        )
        answer = interpret(st.handle, question)
        if not isinstance(report.evidence, dict):
            report.evidence = {}
        report.evidence.setdefault("verifications", []).append(
            {
                "observation_id": observation,
                "tool": tool,
                "question": question,
                "answer": answer,
                "success_conditions": list(st.subtask.success_conditions),
            }
        )
        self.store.save_report(state.run_id, _strip_handle(report))
        state.emit(
            "subtask_verified",
            sid,
            {
                "subtask_id": sid,
                "observation_id": observation,
                "tool": tool,
            },
        )

    @staticmethod
    def _carried_failure(
        report: WorkerReport, sid: str, reason: str, fallback: str
    ) -> TypedError:
        """The report's own typed failure, unchanged; else one built from the reason."""
        if report.typed_failures:
            return report.typed_failures[0]
        return TypedError(
            code="EXTRACTION_FAILED",
            message=f"{sid}: {fallback}: {reason}",
            retryable=False,
            step_id=sid,
        )

    def _fail_subtask(
        self, state: _RunState, st: _SubtaskState, failure: TypedError
    ) -> None:
        with state.lock:
            if st.status in (_FAILED, _CANCELLED, _ACCEPTED):
                return
            st.status = _FAILED
            st.failure = failure
        state.note(
            "subtask_failed",
            f"{st.subtask_id}: {failure.code}",
            {
                "subtask_id": st.subtask_id,
                "error": failure.to_dict(),
            },
        )

    def _cancel_subtask(self, state: _RunState, st: _SubtaskState, reason: str) -> None:
        with state.lock:
            if st.status != _PENDING:
                return
            st.status = _CANCELLED
            st.failure = TypedError(
                code="CANCELLED",
                message=f"{st.subtask_id}: {reason}",
                retryable=False,
                step_id=st.subtask_id,
            )
        state.emit(
            "subtask_cancelled",
            st.subtask_id,
            {
                "subtask_id": st.subtask_id,
                "reason": reason,
            },
        )

    @staticmethod
    def _abandoned(state: _RunState, st: _SubtaskState) -> bool:
        """Whether the run has stopped waiting for this subtask: its report, if
        one still arrives, is ignored and nothing is stored for it."""
        with state.lock:
            return st.abandoned or state.result is not None

    def _abandon_after_breach(self, state: _RunState, st: _SubtaskState) -> None:
        """Stop waiting for a subtask the run has no time left for.

        Its thread may still be inside ``run_subtask``.  The subtask is failed
        with ``BUDGET_EXCEEDED`` (unless it was accepted just before the
        deadline), its lent session is closed now through the toolbox, which is
        the transport-level cutoff for a hung browser call, and its report, if
        it ever arrives, is ignored.
        """
        with state.lock:
            st.abandoned = True
            handle = st.handle
            if handle is not None:
                state.sessions.pop(handle, None)
        self._fail_subtask(
            state, st, self._budget_error(st, "run exceeded max_seconds")
        )
        if handle is not None:
            self._close_session(state, handle, st.subtask_id)

    def _observe(self, state: _RunState, st: _SubtaskState, event: Event) -> None:
        """Optional live monitoring on selected events only."""
        if self._observer is None:
            return
        try:
            decision = self._observer.observe_progress(state.snapshot(), event)
            if not isinstance(decision, ModeratorDecision):
                decision = ModeratorDecision.from_dict(decision)
            if decision.stage != "observe":
                return
        except Exception as exc:  # noqa: BLE001 - monitoring never breaks a run
            state.emit(
                "observer_failed", f"observe_progress raised {type(exc).__name__}"
            )
            return
        if decision.decision == "flag":
            st.force_verify = True
        elif decision.decision == "stop_subtask":
            st.stop_requested = decision.reason or "stop_subtask"
        if decision.decision != "continue":
            state.emit(
                "observer_decided",
                f"{st.subtask_id}: {decision.decision}",
                {
                    "subtask_id": st.subtask_id,
                    "decision": decision.decision,
                    "reason": decision.reason,
                },
            )

    # ------------------------------------------------------ reconcile onwards

    def _stage_reconcile(
        self, state: _RunState, accepted: list[_SubtaskState], records: list[Any]
    ) -> tuple[list[Any], list[Note]]:
        assert state.plan is not None
        reports = [st.report for st in accepted if st.report is not None]
        with state.lock:
            state.moderator_calls += 1
        # Copies: the accepted reports are the provenance the reconciled
        # records are checked against below, so the moderator cannot hold them.
        decision = self.moderator.reconcile(
            state.plan, [copy.deepcopy(r) for r in reports]
        )
        if not isinstance(decision, ModeratorDecision):
            decision = ModeratorDecision.from_dict(decision)
        if decision.stage != "reconcile":
            raise ContractError(f"reconcile returned stage {decision.stage!r}")
        state.emit(
            "reconciled",
            decision.decision,
            {
                "decision": decision.decision,
                "reason": decision.reason,
            },
        )
        if decision.decision == "fail":
            raise _Finished(
                self._result(
                    state,
                    "failed",
                    error=TypedError(
                        code="EXTRACTION_FAILED",
                        message=f"reconcile failed: {decision.reason}",
                        retryable=False,
                        evidence_refs=list(decision.evidence_refs),
                    ),
                )
            )
        notes: list[Note] = []
        if decision.decision == "verify":
            # Post-intake verification is not executed in this phase.  Its
            # model-written reason remains internal; publication gets one typed
            # controller-rendered note instead.
            notes.append(Note("reconciliation_unresolved", "plan"))
        action = decision.next_action if isinstance(decision.next_action, dict) else {}
        merged = action.get("findings")
        if isinstance(merged, list):
            # rule check: reconciliation may reorder, drop and supersede, never
            # add or alter.  Every reconciled record must be one of the accepted
            # reports' records, compared as JSON-normalised data.
            unsupplied = _first_unsupplied(merged, records)
            if unsupplied is not None:
                index, record = unsupplied
                raise ContractError(
                    f"reconciled record {index} is not one of the accepted reports' "
                    f"records: {_label(record)}",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(decision.evidence_refs),
                )
            records = list(merged)
        unresolved = False
        for key in ("gaps", "conflicts"):
            for entry in action.get(key) or []:
                if isinstance(entry, dict):
                    tag = (
                        entry.get("resolution")
                        or entry.get("action")
                        or "report_as_gap"
                    )
                    if tag == "resolve_from_evidence":
                        continue
                unresolved = True
        if unresolved and not notes:
            notes.append(Note("reconciliation_unresolved", "plan"))
        return records, notes

    def _stage_validate(
        self, state: _RunState, accepted: list[_SubtaskState], evidence: list[str]
    ) -> dict[str, Any]:
        if not accepted:
            validation = {
                "status": "inconclusive",
                "checks": [],
                "reason": "no accepted reports",
                "subtasks": {},
            }
            state.emit("validated", "inconclusive", {"status": "inconclusive"})
            return validation
        per_subtask: dict[str, Any] = {}
        statuses: list[str] = []
        for st in accepted:
            assert st.report is not None
            records, refs = _records_of(st.report), _evidence_of(st.report)
            if st.subtask.kind == "open":
                result = self.ghost.validate(
                    st.subtask,
                    records,
                    refs,
                    report_context=self._report_context(state, st),
                )
            else:
                result = self.ghost.validate(st.subtask, records, refs)
            if not isinstance(result, dict) or result.get("status") not in (
                "passed",
                "failed",
                "inconclusive",
            ):
                raise ContractError(
                    f"ghost.validate returned no status for {st.subtask_id}"
                )
            per_subtask[st.subtask_id] = result
            statuses.append(result["status"])
        if all(s == "passed" for s in statuses):
            overall = "passed"
        elif any(s == "failed" for s in statuses):
            overall = "failed"
        else:
            overall = "inconclusive"
        # Ghost owns the shape of "checks": a list of check objects, or a
        # ``name -> passed`` mapping.  Iterating a mapping would keep only the
        # names and silently drop whether each check passed, so a mapping is
        # expanded into one entry per check instead.
        checks: list[Any] = []
        for subtask_id, result in per_subtask.items():
            raw = result.get("checks")
            if isinstance(raw, list):
                checks.extend(raw)
            elif isinstance(raw, dict):
                checks.extend(
                    {"subtask_id": subtask_id, "check": name, "passed": passed}
                    for name, passed in raw.items()
                )
        validation = {
            "status": overall,
            "checks": checks,
            "subtasks": per_subtask,
            "evidence": list(evidence),
        }
        state.emit(
            "validated",
            overall,
            {
                "status": overall,
                "subtasks": {k: v["status"] for k, v in per_subtask.items()},
            },
        )
        return validation

    @staticmethod
    def _report_context(state: _RunState, st: _SubtaskState) -> dict[str, Any]:
        """What open-world validation gets to know about the report, handle-free.

        The evidence dict is copied with a ``session_handle`` entry and any
        value equal to the lent handle removed, so Ghost never sees a session.
        """
        assert st.report is not None
        report = st.report
        evidence = (
            copy.deepcopy(report.evidence) if isinstance(report.evidence, dict) else {}
        )
        evidence.pop("session_handle", None)
        handles = {handle for handle in (st.handle, report.session_handle) if handle}
        evidence = {
            key: value
            for key, value in evidence.items()
            if not (isinstance(value, str) and value in handles)
        }
        return {
            "run_id": state.run_id,
            "subtask_id": st.subtask_id,
            "evidence": evidence,
            "empty_state": _empty_state_of(report),
        }

    def _stage_synthesize(
        self,
        state: _RunState,
        accepted: list[_SubtaskState],
        records: list[Any],
        evidence: list[str],
        failures: list[TypedError],
        controller_notes: list[Note],
    ) -> FinalAnswer:
        assert state.interpreted is not None and state.validation is not None
        with state.lock:
            state.moderator_calls += 1
        # Copies: the validated records are the provenance the selection's
        # records are checked against below, so the moderator cannot alter them
        # in place.  The moderator returns selection only; controller-owned
        # output fields from an older or adversarial implementation are dropped
        # without ever being echoed into an event or result.
        raw = self.moderator.synthesize(
            state.interpreted,
            copy.deepcopy(records),
            copy.deepcopy(state.validation),
            list(evidence),
            list(failures),
        )
        discarded: list[str] = []
        if isinstance(raw, AnswerSelection):
            selection = copy.deepcopy(raw)
        elif isinstance(raw, dict):
            payload, discarded = _discard_moderator_prose(raw)
            try:
                selection = AnswerSelection.from_dict(payload)
            except ContractError as exc:
                raise ContractError(
                    f"moderator selection is invalid: {exc}",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                ) from exc
        else:
            discarded = []
            raise ContractError(
                "moderator synthesize returned no structured selection",
                code="EXTRACTION_FAILED",
                evidence_refs=list(evidence),
            )
        try:
            # Re-decode even a direct dataclass result: callers can mutate a
            # dataclass after ``__post_init__``, and the stage boundary must
            # validate what it actually received.
            selection = AnswerSelection.from_dict(selection.to_dict())
        except ContractError as exc:
            raise ContractError(
                f"moderator selection is invalid: {exc}",
                code="EXTRACTION_FAILED",
                evidence_refs=list(evidence),
            ) from exc
        if discarded:
            state.emit(
                "moderator_output_discarded",
                "controller-owned moderator output fields were discarded",
                {"fields": sorted(discarded)},
            )

        selected_records: list[Any] = []
        for index in selection.record_indices:
            if index >= len(records):
                raise ContractError(
                    "moderator selected a record outside the validated record set",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                )
            selected_records.append(copy.deepcopy(records[index]))

        # Evidence belongs to the controller.  It is derived from the selected
        # record after the moderator's index/field choices have been validated.
        allowed = set(evidence)
        for st in accepted:
            if st.report is not None:
                allowed.update(_evidence_of(st.report))
        claims: list[Claim] = []
        for index, claim in enumerate(selection.claims):
            if claim.record_index >= len(selected_records):
                raise ContractError(
                    f"claim {index} names a record outside the selected records",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                )
            record = selected_records[claim.record_index]
            if not isinstance(record, dict):
                raise ContractError(
                    f"claim {index} names a record that has no selectable fields",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                )
            for name in claim.fields:
                if name in _INTERNAL_RECORD_FIELDS or name not in record:
                    raise ContractError(
                        f"claim {index} names a field outside its validated record",
                        code="EXTRACTION_FAILED",
                        evidence_refs=list(evidence),
                    )
            observation = record.get("source_observation_id")
            if not isinstance(observation, str) or observation not in allowed:
                raise ContractError(
                    f"claim {index} has no run evidence on its validated record",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                )
            claims.append(Claim(claim.record_index, list(claim.fields)))
        if [claim.record_index for claim in claims] != list(
            range(len(selected_records))
        ):
            raise ContractError(
                "claims must name every selected record exactly once and in selected order",
                code="EXTRACTION_FAILED",
                evidence_refs=list(evidence),
            )

        criteria = _criterion_subjects(state.interpreted)
        notes: list[Note] = []
        for index, note in enumerate(selection.notes):
            if note.kind == "criterion_not_applied":
                valid = note.subject in criteria
            elif note.kind == "synthesis_fallback":
                valid = note.subject in _SYNTHESIS_FALLBACK_SUBJECTS
            else:
                valid = False
            if not valid:
                raise ContractError(
                    f"note {index} has a subject outside the controller's closed set",
                    code="EXTRACTION_FAILED",
                    evidence_refs=list(evidence),
                )
            notes.append(note)
        notes.extend(copy.deepcopy(controller_notes))
        status = state.validation["status"]
        if status != "passed":
            notes.append(Note("validation_not_passed", status))
            claims = []
            selected_records = []
        notes = list({(note.kind, note.subject): note for note in notes}.values())
        rendered_notes = [_render_note(note, criteria) for note in notes]
        lines = _render_answer_lines(
            selected_records, claims, status, rendered_notes, failures
        )
        answer = FinalAnswer(
            lines=lines,
            claims=claims,
            records=selected_records,
            failures=copy.deepcopy(failures),
            notes=notes,
        )
        state.emit(
            "synthesized",
            f"{len(answer.claims)} claim(s)",
            {
                "claims": len(answer.claims),
                "records": len(answer.records),
                "failures": len(answer.failures),
                "notes": len(answer.notes),
            },
        )
        return answer

    def _stage_compile(self, state: _RunState, accepted: list[_SubtaskState]) -> None:
        """Candidate compilation; its failure never changes the task result."""
        for st in accepted:
            assert st.report is not None
            try:
                skill = self.ghost.compile(_strip_handle(st.report), st.subtask)
                if skill is None:
                    continue
                skill = dict(skill)
                skill.pop("session_handle", None)
                self.store.save_skill(skill)
                state.emit(
                    "skill_candidate_created",
                    st.subtask_id,
                    {
                        "subtask_id": st.subtask_id,
                        "skill_id": skill.get("skill_id"),
                        "status": skill.get("status"),
                    },
                )
            except Exception as exc:  # noqa: BLE001
                state.emit(
                    "compile_failed",
                    f"{st.subtask_id}: {type(exc).__name__}",
                    {
                        "subtask_id": st.subtask_id,
                        "exception": type(exc).__name__,
                    },
                )

    # ----------------------------------------------------------------- helpers

    def _check_run_limits(self, state: _RunState) -> None:
        if state.cancel_requested.is_set():
            raise _Finished(
                self._result(
                    state,
                    "cancelled",
                    error=TypedError(
                        code="CANCELLED",
                        message="run cancelled",
                        retryable=False,
                    ),
                )
            )
        if state.elapsed() > self.max_seconds:
            state.emit(
                "budget_exceeded",
                "run exceeded max_seconds",
                {
                    "max_seconds": self.max_seconds,
                    "elapsed_seconds": round(state.elapsed(), 3),
                },
            )
            raise _Finished(
                self._result(
                    state,
                    "failed",
                    error=TypedError(
                        code="BUDGET_EXCEEDED",
                        message=f"run exceeded max_seconds={self.max_seconds:g}",
                        retryable=False,
                    ),
                )
            )

    def _result(
        self,
        state: _RunState,
        status: str,
        *,
        error: TypedError | None = None,
        answer: FinalAnswer | None = None,
    ) -> RunResult:
        reports = [
            _strip_handle(st.report)
            for st in state.subtasks.values()
            if st.report is not None
        ]
        metrics = {
            "elapsed_seconds": round(state.elapsed(), 3),
            "browser_action_count": sum(
                st.actions_used for st in state.subtasks.values()
            ),
            "moderator_calls": state.moderator_calls,
            "sessions_opened": state.sessions_opened,
            "subtasks": {sid: st.status for sid, st in state.subtasks.items()},
            "max_actions": self.max_actions,
            "max_seconds": self.max_seconds,
        }
        return RunResult(
            run_id=state.run_id,
            status=status,
            interpreted=state.interpreted,
            gate=state.gate,
            plan=state.plan,
            reports=reports,
            validation=state.validation,
            answer=answer if answer is not None else state.answer,
            metrics=metrics,
            error=error,
        )

    def _failed(self, state: _RunState, error: TypedError) -> RunResult:
        state.emit(
            "stage_failed",
            f"{state.state}: {error.code}",
            {
                "stage": state.state,
                "error": error.to_dict(),
            },
        )
        return self._result(state, "failed", error=error)

    def _close_sessions(self, state: _RunState) -> None:
        """Close every session this run still holds.  Runs in ``finally``; never raises."""
        with state.lock:
            state.closing = True
            handles = list(state.sessions.items())
            state.sessions.clear()
        for handle, sid in handles:
            self._close_session(state, handle, sid)

    def _close_session(self, state: _RunState, handle: str, sid: str) -> None:
        """Close one session through the toolbox.  Never raises; the handle
        itself is named in no event."""
        try:
            self.toolbox.close_session(handle)
            state.note("session_closed", sid, {"subtask_id": sid})
        except Exception as exc:  # noqa: BLE001
            try:
                state.note(
                    "session_close_failed",
                    f"{sid}: {type(exc).__name__}",
                    {
                        "subtask_id": sid,
                        "exception": type(exc).__name__,
                    },
                )
            except Exception as note_exc:  # noqa: BLE001 - the store is already failing
                log.warning(
                    "run %s: could not record stage failure: %s",
                    state.run_id,
                    type(note_exc).__name__,
                )

    @staticmethod
    def _import(module: str, name: str) -> Callable[..., Any]:
        try:
            return getattr(importlib.import_module(module), name)
        except (ImportError, AttributeError) as exc:
            raise ContractError(
                f"{module}.{name} is not available ({type(exc).__name__})",
                code="EXTRACTION_FAILED",
            ) from None


class _Finished(Exception):
    """Carries a terminal result out of a stage that ends the run early."""

    def __init__(self, result: RunResult) -> None:
        super().__init__(result.status)
        self.result = result
