"""Messages exchanged between the ARGUS stages.

Every message in this module is a plain dataclass with ``to_dict()`` and
``from_dict()``.  ``from_dict()`` rejects unknown fields and missing required
fields with :class:`ContractError`, so a malformed payload fails at the stage
boundary that produced it instead of deeper in the run.

Stage map (see docs/hackathon/ARGUS.md):

===========================  ==========================================
Message                      Produced by
===========================  ==========================================
:class:`InterpretedRequest`  stage 1, interpret
:class:`GateDecision`        stage 2, gate
:class:`Plan`                stage 3, plan
:class:`SubtaskInput`        stage 5, dispatch
:class:`WorkerReport`        a subagent, read at stage 7, report intake
:class:`ModeratorDecision`   stages 7, 8 and 10, the moderator
:class:`FinalAnswer`         stage 10, synthesize
:class:`Event`               every stage, appended to the run event stream
:class:`RunResult`           stage 11, publish (the single terminal result)
===========================  ==========================================

Standard library only; no third-party dependency and no I/O.
"""

from __future__ import annotations

import copy
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, ClassVar, Mapping

__all__ = [
    "SCHEMA_VERSION",
    "ERROR_CODES",
    "PARAMETER_SOURCES",
    "GATE_DECISIONS",
    "MODERATOR_STAGES",
    "PREFERRED_TOOLS",
    "SUBTASK_MODES",
    "RUN_STATUSES",
    "ContractError",
    "Message",
    "TypedError",
    "ParameterOrigin",
    "Intent",
    "MissingParameter",
    "InterpretedRequest",
    "GateDecision",
    "Subtask",
    "Plan",
    "Budget",
    "SubtaskInput",
    "WorkerReport",
    "ModeratorDecision",
    "MODERATOR_DECISIONS",
    "Claim",
    "FinalAnswer",
    "Event",
    "RunResult",
]

#: Version of the message shapes in this module.  Bump it when a field changes.
SCHEMA_VERSION = "0.2-argus-draft"

#: Typed failure codes.  Every failure that reaches the user carries one of these.
ERROR_CODES = (
    "NO_MATCH",
    "INVALID_INPUT",
    "NEEDS_INPUT",
    "TARGET_NOT_FOUND",
    "TARGET_AMBIGUOUS",
    "PRECONDITION_FAILED",
    "NAVIGATION_TIMEOUT",
    "AUTH_REQUIRED",
    "EXTRACTION_FAILED",
    "VALIDATION_FAILED",
    "UNSUPPORTED_CHANGE",
    "BUDGET_EXCEEDED",
    "CANCELLED",
    "MODEL_REFUSED",
)

#: Where a parameter value came from.
PARAMETER_SOURCES = ("text_span", "default", "structured")

#: Outcomes of the acceptance gate (stage 2).
GATE_DECISIONS = ("accept", "clarify", "reject")

#: Stages at which the moderator returns a ModeratorDecision.  Stage 10
#: (synthesize) returns a FinalAnswer instead, so it is not listed here.
MODERATOR_STAGES = ("assess", "reconcile", "observe")

#: Allowed ``decision`` values per stage.  The controller enforces the caps
#: (one verify and one retry_other_path per subtask); the moderator only picks.
MODERATOR_DECISIONS = {
    "assess": ("accept", "verify", "retry_other_path", "fail"),
    "reconcile": ("merged", "verify", "fail"),
    "observe": ("continue", "flag", "stop_subtask"),
}

#: Interpretation tool a subtask prefers.
PREFERRED_TOOLS = ("dom", "vision")

#: How a subtask is executed: fresh exploration or replay of a bound procedure.
SUBTASK_MODES = ("explore", "reuse")

#: Statuses of a run; "running" is the in-progress snapshot, the rest are terminal.
RUN_STATUSES = ("running", "succeeded", "failed", "cancelled", "needs_input")


class ContractError(ValueError):
    """A payload does not satisfy a contract in this module.

    Raised by ``from_dict()`` for unknown fields, missing required fields and
    values outside a fixed vocabulary, and by callers that need to surface a
    typed failure without a worker report (for example a refused model call).
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "INVALID_INPUT",
        step_id: str | None = None,
        evidence_refs: list[str] | None = None,
    ) -> None:
        if code not in ERROR_CODES:
            raise ValueError(f"unknown error code {code!r}")
        super().__init__(message)
        self.code = code
        self.step_id = step_id
        self.evidence_refs = list(evidence_refs or [])

    @property
    def typed_error(self) -> "TypedError":
        """The failure as a :class:`TypedError` for the run result."""
        return TypedError(
            code=self.code,
            message=str(self),
            retryable=False,
            step_id=self.step_id,
            evidence_refs=list(self.evidence_refs),
        )


def _encode(value: Any) -> Any:
    """Convert a field value to JSON-serialisable data."""
    if isinstance(value, Message):
        return value.to_dict()
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {key: _encode(item) for key, item in value.items()}
    return value


def _check_choice(owner: str, name: str, value: Any, allowed: tuple[str, ...]) -> None:
    if value not in allowed:
        raise ContractError(
            f"{owner}.{name} must be one of {', '.join(allowed)}; got {value!r}"
        )


class Message:
    """Shared JSON behaviour for the dataclasses below.

    Subclasses declare nested message fields in ``_NESTED`` as
    ``field name -> (kind, class)`` where kind is ``"one"`` (nullable single
    message), ``"list"`` or ``"map"`` (``dict[str, Message]``).  Fields absent
    from ``_NESTED`` are carried through as plain JSON data.
    """

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {}

    def to_dict(self) -> dict[str, Any]:
        """Return the message as JSON-serialisable data, every field present."""
        return {f.name: _encode(getattr(self, f.name)) for f in fields(self)}

    @classmethod
    def required_fields(cls) -> tuple[str, ...]:
        """Names of the fields that a payload must supply."""
        return tuple(
            f.name
            for f in fields(cls)
            if f.default is MISSING and f.default_factory is MISSING
        )

    @classmethod
    def from_dict(cls, data: Any):
        """Build the message from JSON data, rejecting anything unexpected."""
        if not isinstance(data, Mapping):
            raise ContractError(f"{cls.__name__}: expected an object, got {type(data).__name__}")
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ContractError(f"{cls.__name__}: unknown field(s) {', '.join(unknown)}")
        missing = [name for name in cls.required_fields() if name not in data]
        if missing:
            raise ContractError(f"{cls.__name__}: missing required field(s) {', '.join(missing)}")
        kwargs = {
            f.name: cls._decode_field(f.name, data[f.name])
            for f in fields(cls)
            if f.name in data
        }
        return cls(**kwargs)

    @classmethod
    def _decode_field(cls, name: str, value: Any) -> Any:
        nested = cls._NESTED.get(name)
        if nested is None:
            return copy.deepcopy(value)
        kind, klass = nested
        if kind == "one":
            return None if value is None else klass.from_dict(value)
        if kind == "list":
            if not isinstance(value, list):
                raise ContractError(f"{cls.__name__}.{name} must be a list")
            return [klass.from_dict(item) for item in value]
        if not isinstance(value, Mapping):
            raise ContractError(f"{cls.__name__}.{name} must be an object")
        return {key: klass.from_dict(item) for key, item in value.items()}


@dataclass
class TypedError(Message):
    """One machine-readable failure.

    Produced wherever a stage fails: a worker report's ``typed_failures``, the
    controller's budget and cancellation checks, and ``RunResult.error``.  It is
    carried into the final answer unchanged, never rewritten as prose.
    """

    code: str
    message: str
    retryable: bool
    step_id: str | None = None
    evidence_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _check_choice("TypedError", "code", self.code, ERROR_CODES)


@dataclass
class ParameterOrigin(Message):
    """One interpreted parameter value and where it came from.

    Produced by stage 1 so a reviewer can see which characters of the request
    produced each value, and so the gate can tell a defaulted value from one the
    user actually wrote.  ``span`` is the half-open ``(start, end)`` range into
    ``InterpretedRequest.raw_text`` for ``source == "text_span"``, else ``None``.
    """

    value: Any
    source: str
    confidence: float
    span: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        _check_choice("ParameterOrigin", "source", self.source, PARAMETER_SOURCES)
        if self.span is not None:
            span = tuple(self.span)
            if len(span) != 2 or not all(isinstance(part, int) for part in span):
                raise ContractError("ParameterOrigin.span must be two integers or null")
            if span[0] < 0 or span[1] < span[0]:
                raise ContractError(f"ParameterOrigin.span {span} is not a valid range")
            self.span = span


@dataclass
class Intent(Message):
    """One supported site operation the user asked for.

    Produced by stage 1.  A compound request yields several intents; the planner
    turns each one into a subtask.
    """

    site_id: str
    operation: str
    parameters: dict[str, ParameterOrigin]
    confidence: float

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "parameters": ("map", ParameterOrigin),
    }


@dataclass
class MissingParameter(Message):
    """A required parameter the request did not supply.

    Produced by stage 1 and turned into a clarifying question by the gate.
    """

    intent_index: int
    parameter: str
    question: str


@dataclass
class InterpretedRequest(Message):
    """Stage 1 output and the handoff artifact every teammate can read.

    Same shape whether the input was free text or structured fields.  It records
    what the model understood, not what will be executed; the gate decides that.
    """

    request_id: str
    raw_text: str
    intents: list[Intent]
    model: str
    interpreted_at: str
    missing_required: list[MissingParameter] = field(default_factory=list)
    ambiguities: list[str] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "intents": ("list", Intent),
        "missing_required": ("list", MissingParameter),
    }


@dataclass
class GateDecision(Message):
    """Stage 2 output: whether the interpreted request may execute.

    Pure rules, no model call.  ``clarify`` ends the run as ``needs_input`` with
    ``questions``; ``reject`` ends it with ``INVALID_INPUT``.  ``rule_id`` names
    the rule that fired so the decision can be explained.
    """

    decision: str
    rule_id: str
    reason: str
    questions: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        _check_choice("GateDecision", "decision", self.decision, GATE_DECISIONS)


@dataclass
class Subtask(Message):
    """One unit of browser work, produced by stage 3.

    ``parameters`` holds plain resolved values, unlike the intent it came from.
    ``depends_on`` and ``concurrency_group`` are fixed at creation time: the
    dispatcher starts subtasks whose dependencies are satisfied and never
    reorders them later.
    """

    subtask_id: str
    intent_index: int
    site_id: str
    operation: str
    parameters: dict[str, Any]
    concurrency_group: str
    output_schema_id: str
    depends_on: list[str] = field(default_factory=list)
    success_conditions: list[str] = field(default_factory=list)
    preferred_tool: str = "dom"

    def __post_init__(self) -> None:
        _check_choice("Subtask", "preferred_tool", self.preferred_tool, PREFERRED_TOOLS)


@dataclass
class Plan(Message):
    """Stage 3 output: the subtasks for one accepted request."""

    plan_id: str
    request_id: str
    subtasks: list[Subtask]
    created_at: str

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {"subtasks": ("list", Subtask)}


@dataclass
class Budget(Message):
    """The per-subtask ceiling the controller enforces at stage 6."""

    max_actions: int
    max_seconds: float


@dataclass
class SubtaskInput(Message):
    """What a subagent receives at stage 5 (dispatch).

    The session is lent by the controller; the subagent never opens or closes
    one.  ``mode`` is ``reuse`` only when Ghost matched a qualified skill, in
    which case ``bound_procedure`` carries it.
    """

    run_id: str
    subtask: Subtask
    session_handle: str | None
    budget: Budget
    mode: str
    bound_procedure: dict[str, Any] | None = None

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "subtask": ("one", Subtask),
        "budget": ("one", Budget),
    }

    def __post_init__(self) -> None:
        _check_choice("SubtaskInput", "mode", self.mode, SUBTASK_MODES)


@dataclass
class WorkerReport(Message):
    """What a subagent returns for one subtask; read at stage 7.

    The first thirteen fields are the visual worker's report format adopted
    verbatim (``workers/visual/examples/hn-top-story/report.json``), including
    its own ``schema_version`` and its untyped ``failures``.  ARGUS adds the
    returned ``session_handle`` and ``typed_failures``; both are optional so a
    worker's report loads unchanged.
    """

    schema_version: str
    worker: str
    worker_model: str
    request_id: str
    subtask_id: str
    subtask: str
    outcome: str
    summary: Any
    findings: Any
    actions: list[Any]
    evidence: dict[str, Any]
    metrics: dict[str, Any]
    failures: list[Any]
    session_handle: str | None = None
    typed_failures: list[TypedError] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "typed_failures": ("list", TypedError),
    }


@dataclass
class ModeratorDecision(Message):
    """One moderator answer, at stage 7 (assess), 8 (reconcile) or the
    optional live-monitoring hook (observe).

    The moderator only returns decisions; the controller executes them and keeps
    every cap.  Free text stays in ``reason``.  ``next_action`` carries the
    stage-specific detail: for ``verify`` the question to ask the page; for
    ``merged`` the merged findings plus ``gaps`` and ``conflicts`` lists, each
    entry tagged ``resolve_from_evidence``, ``verify`` or ``report_as_gap``;
    for ``stop_subtask`` the subtask_id.
    """

    stage: str
    decision: str
    reason: str
    evidence_refs: list[str] = field(default_factory=list)
    next_action: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        _check_choice("ModeratorDecision", "stage", self.stage, MODERATOR_STAGES)
        _check_choice(
            f"ModeratorDecision[{self.stage}]", "decision", self.decision,
            MODERATOR_DECISIONS[self.stage],
        )


@dataclass
class Claim(Message):
    """One statement in the final answer with the evidence behind it.

    The controller's rule check after stage 10 fails the run if any claim has no
    evidence reference.
    """

    text: str
    evidence_refs: list[str] = field(default_factory=list)


@dataclass
class FinalAnswer(Message):
    """Stage 10 output: what the user is told.

    ``failures`` repeats the typed failures unchanged and ``unverified`` names
    what the run could not confirm, so an honest partial result is expressible.
    """

    text: str
    claims: list[Claim] = field(default_factory=list)
    records: list[Any] = field(default_factory=list)
    failures: list[TypedError] = field(default_factory=list)
    unverified: list[str] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "claims": ("list", Claim),
        "failures": ("list", TypedError),
    }


@dataclass
class Event(Message):
    """One entry in a run's ordered event stream, appended by any stage.

    ``sequence`` increases strictly within a run, so a consumer can order and
    deduplicate events.  Provider messages and stack traces never go in here.
    """

    run_id: str
    sequence: int
    timestamp: str
    type: str
    stage: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunResult(Message):
    """The single terminal result of a run, written once at stage 11.

    Also used as the in-progress snapshot, where the later fields are still
    empty.  ``validation`` is Ghost's validation report and ``metrics`` the run
    totals; both stay plain dicts because they are owned by other components.
    """

    run_id: str
    status: str
    interpreted: InterpretedRequest | None = None
    gate: GateDecision | None = None
    plan: Plan | None = None
    reports: list[WorkerReport] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    answer: FinalAnswer | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    error: TypedError | None = None

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "interpreted": ("one", InterpretedRequest),
        "gate": ("one", GateDecision),
        "plan": ("one", Plan),
        "reports": ("list", WorkerReport),
        "answer": ("one", FinalAnswer),
        "error": ("one", TypedError),
    }

    def __post_init__(self) -> None:
        _check_choice("RunResult", "status", self.status, RUN_STATUSES)
