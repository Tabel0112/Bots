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
:class:`AnswerSelection`     stage 10, moderator selection
:class:`FinalAnswer`         stage 10, controller rendering
:class:`Event`               every stage, appended to the run event stream
:class:`RunResult`           stage 11, publish (the single terminal result)
===========================  ==========================================

Standard library only; no third-party dependency and no I/O.
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, field, fields
from typing import Any, ClassVar

__all__ = [
    "CRITERION_KINDS",
    "ERROR_CODES",
    "GATE_DECISIONS",
    "INTENT_KINDS",
    "MAX_CONCURRENT_WORKERS",
    "MAX_OPEN_DEPTH",
    "MAX_OPEN_SUBTASKS",
    "MODERATOR_DECISIONS",
    "MODERATOR_STAGES",
    "NOTE_KINDS",
    "PARAMETER_SOURCES",
    "PLANNED_BY_VALUES",
    "PREFERRED_TOOLS",
    "RUN_STATUSES",
    "SCHEMA_VERSION",
    "SUBTASK_MODES",
    "SYNTHESIS_FALLBACK_SUBJECTS",
    "AnswerSelection",
    "Budget",
    "Claim",
    "ContractError",
    "Criterion",
    "Event",
    "FinalAnswer",
    "GateDecision",
    "Intent",
    "InterpretedRequest",
    "Message",
    "MissingParameter",
    "ModeratorDecision",
    "Note",
    "ParameterOrigin",
    "Plan",
    "RunResult",
    "Subtask",
    "SubtaskInput",
    "TypedError",
    "WorkerReport",
]

#: Version of the message shapes in this module.  Bump it when a field changes.
SCHEMA_VERSION = "0.5-argus-draft"

#: Local VLM capacity and the separate MVP open-plan size budget.
MAX_CONCURRENT_WORKERS = 4
MAX_OPEN_SUBTASKS = 4
MAX_OPEN_DEPTH = 3

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
    "DOMAIN_NOT_ALLOWED",
    "ACTION_CLASS_NOT_ALLOWED",
    "PLAN_TOO_LARGE",
)

#: Where a parameter value came from.
PARAMETER_SOURCES = ("text_span", "default", "structured", "suggested")

#: Whether an intent or subtask uses a qualified registry operation or an
#: open-world navigation path.
INTENT_KINDS = ("registry", "open")

#: Ways a user can constrain or order open-world results.
CRITERION_KINDS = ("rank", "filter", "limit")

#: Which planning path produced a plan.
PLANNED_BY_VALUES = ("deterministic", "model")

#: Outcomes of the acceptance gate (stage 2).
GATE_DECISIONS = ("accept", "clarify", "reject")

#: Stages at which the moderator returns a ModeratorDecision.  Stage 10
#: (synthesize) returns an AnswerSelection instead, so it is not listed here.
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

#: Structured caveats the moderator or controller may attach to an answer.
#: Their subjects are checked against run-owned closed sets by the controller;
#: neither field is rendered verbatim.
NOTE_KINDS = (
    "criterion_not_applied",
    "synthesis_fallback",
    "reconciliation_unresolved",
    "validation_not_passed",
    "clarification_required",
)

#: Closed reasons a model-backed moderator may use when its deterministic
#: synthesis fallback produced the selection.
SYNTHESIS_FALLBACK_SUBJECTS = (
    "model_refused",
    "model_truncated",
    "model_invalid",
    "selection_invalid",
)


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
    def typed_error(self) -> TypedError:
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


def _check_open_context(
    owner: str, target_domain: Any, goal: Any, criteria: Any, expected_record_shape: Any
) -> None:
    """Check optional context without deciding whether an intent can execute."""
    for name, value in (("target_domain", target_domain), ("goal", goal)):
        if value is not None and not isinstance(value, str):
            raise ContractError(f"{owner}.{name} must be a string or null")
    if not isinstance(criteria, list) or any(
        not isinstance(c, Criterion) for c in criteria
    ):
        raise ContractError(f"{owner}.criteria must be a list of Criterion messages")
    if not isinstance(expected_record_shape, list) or any(
        not isinstance(name, str) or not name.strip() for name in expected_record_shape
    ):
        raise ContractError(
            f"{owner}.expected_record_shape must be a list of nonempty field names"
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
            raise ContractError(
                f"{cls.__name__}: expected an object, got {type(data).__name__}"
            )
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise ContractError(
                f"{cls.__name__}: unknown field(s) {', '.join(unknown)}"
            )
        missing = [name for name in cls.required_fields() if name not in data]
        if missing:
            raise ContractError(
                f"{cls.__name__}: missing required field(s) {', '.join(missing)}"
            )
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
class Criterion(Message):
    """One explicit ranking, filtering or limiting request from the user.

    Produced by stage 1 for open-world intents.  ``span`` is the half-open
    character range in ``InterpretedRequest.raw_text`` when the criterion came
    from the request.  ``parameter`` carries its resolved value when there is
    one, such as ``10`` for a result limit or ``"salary"`` for a ranking.
    """

    text: str
    kind: str
    parameter: Any | None
    span: tuple[int, int] | None
    confidence: float

    def __post_init__(self) -> None:
        _check_choice("Criterion", "kind", self.kind, CRITERION_KINDS)
        if not isinstance(self.text, str) or not self.text.strip():
            raise ContractError("Criterion.text must be a nonempty string")
        if (
            type(self.confidence) not in (int, float)
            or not 0 <= self.confidence <= 1
            or not math.isfinite(self.confidence)
        ):
            raise ContractError(
                "Criterion.confidence must be a finite number from 0 to 1"
            )
        if self.span is not None:
            if not isinstance(self.span, (list, tuple)):
                raise ContractError("Criterion.span must be two integers or null")
            span = tuple(self.span)
            if len(span) != 2 or not all(type(part) is int for part in span):
                raise ContractError("Criterion.span must be two integers or null")
            if span[0] < 0 or span[1] < span[0]:
                raise ContractError(f"Criterion.span {span} is not a valid range")
            self.span = span


@dataclass
class Intent(Message):
    """One registry operation or open-world navigation goal from the user.

    Produced by stage 1.  A compound request yields several intents; the planner
    turns each one into one or more subtasks.  Registry intents retain the phase
    1 fields; open intents additionally carry a target domain, plain-language
    goal, explicit criteria and the expected record field names.
    """

    site_id: str
    operation: str
    parameters: dict[str, ParameterOrigin]
    confidence: float
    kind: str = "registry"
    target_domain: str | None = None
    goal: str | None = None
    criteria: list[Criterion] = field(default_factory=list)
    expected_record_shape: list[str] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "parameters": ("map", ParameterOrigin),
        "criteria": ("list", Criterion),
    }

    def __post_init__(self) -> None:
        _check_choice("Intent", "kind", self.kind, INTENT_KINDS)
        _check_open_context(
            "Intent",
            self.target_domain,
            self.goal,
            self.criteria,
            self.expected_record_shape,
        )


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
    ``questions``; ``reject`` ends it with ``INVALID_INPUT``, or ``DOMAIN_NOT_ALLOWED`` (S2) and ``ACTION_CLASS_NOT_ALLOWED`` (S5) for open intents.  ``rule_id`` names
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
    reorders them later.  The planner copies approved open-intent context into
    target_domain, goal, criteria and expected_record_shape for the worker.
    Each inputs_from field names either all "findings" or one top-level field:
    from record lists, that field is collected in record order; from mappings,
    it is the corresponding value.  Missing fields fail rather than being skipped.
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
    inputs_from: dict[str, dict[str, str]] = field(default_factory=dict)
    kind: str = "registry"
    target_domain: str | None = None
    goal: str | None = None
    criteria: list[Criterion] = field(default_factory=list)
    expected_record_shape: list[str] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "criteria": ("list", Criterion)
    }

    def __post_init__(self) -> None:
        _check_choice("Subtask", "preferred_tool", self.preferred_tool, PREFERRED_TOOLS)
        _check_choice("Subtask", "kind", self.kind, INTENT_KINDS)
        _check_open_context(
            "Subtask",
            self.target_domain,
            self.goal,
            self.criteria,
            self.expected_record_shape,
        )
        if not isinstance(self.inputs_from, dict):
            raise ContractError("Subtask.inputs_from must be an object")
        for parameter, source in self.inputs_from.items():
            if not isinstance(parameter, str) or not parameter.strip():
                raise ContractError(
                    "Subtask.inputs_from keys must be nonempty parameter names"
                )
            if (
                not isinstance(source, dict)
                or set(source) != {"subtask_id", "field"}
                or any(not isinstance(v, str) or not v.strip() for v in source.values())
            ):
                raise ContractError(
                    "Subtask.inputs_from entries require only nonempty subtask_id and field"
                )
            if source["subtask_id"] not in self.depends_on:
                raise ContractError(
                    "Subtask.inputs_from must name a declared dependency"
                )


@dataclass
class Plan(Message):
    """Stage 3 output: the subtasks for one accepted request."""

    plan_id: str
    request_id: str
    subtasks: list[Subtask]
    created_at: str
    planned_by: str = "deterministic"
    caps: dict[str, int] = field(
        default_factory=lambda: {
            "max_subtasks": MAX_OPEN_SUBTASKS,
            "max_depth": MAX_OPEN_DEPTH,
        }
    )

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {"subtasks": ("list", Subtask)}

    def __post_init__(self) -> None:
        _check_choice("Plan", "planned_by", self.planned_by, PLANNED_BY_VALUES)
        self.validate_limits()

    def validate_limits(self) -> None:
        """Validate controller-owned budgets, including after a plan is mutated.

        A mixed plan counts all its subtasks toward the open-plan budget.
        Registry-only plans retain their existing sequential-work behavior.
        Graph/depth validation remains the planner's responsibility.
        """
        if not isinstance(self.caps, dict) or set(self.caps) != {
            "max_subtasks",
            "max_depth",
        }:
            raise ContractError("Plan.caps requires only max_subtasks and max_depth")
        for name, ceiling in (
            ("max_subtasks", MAX_OPEN_SUBTASKS),
            ("max_depth", MAX_OPEN_DEPTH),
        ):
            value = self.caps[name]
            if type(value) is not int or value < 1:
                raise ContractError(f"Plan.caps.{name} must be a positive integer")
            if value > ceiling:
                raise ContractError(
                    f"Plan.caps.{name} must be at most {ceiling}", code="PLAN_TOO_LARGE"
                )
        if (
            self.planned_by == "model" or any(s.kind == "open" for s in self.subtasks)
        ) and len(self.subtasks) > self.caps["max_subtasks"]:
            raise ContractError("Open plan exceeds max_subtasks", code="PLAN_TOO_LARGE")


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

    ``request_id`` is the run's single request identity.  The controller always
    sets it, and the worker must echo it in ``WorkerReport.request_id``: a report
    carrying another request's ID fails intake.  It defaults to ``None`` only so
    that payloads written before the field existed still load.
    """

    run_id: str
    subtask: Subtask
    session_handle: str | None
    budget: Budget
    mode: str
    bound_procedure: dict[str, Any] | None = None
    request_id: str | None = None

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
    verbatim (``Agents/visual/examples/hn-top-story/report.json``), including
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
            f"ModeratorDecision[{self.stage}]",
            "decision",
            self.decision,
            MODERATOR_DECISIONS[self.stage],
        )


@dataclass
class Claim(Message):
    """A structured reference to fields of one selected, validated record.

    ``record_index`` indexes the selected-record order.  The moderator chooses
    the index and fields only; the controller validates both against the
    selected validated record.  No model-written prose or evidence reference is
    part of a claim.
    """

    record_index: int
    fields: list[str]

    def __post_init__(self) -> None:
        if type(self.record_index) is not int or self.record_index < 0:
            raise ContractError("Claim.record_index must be a non-negative integer")
        if (
            not isinstance(self.fields, list)
            or not self.fields
            or any(
                not isinstance(name, str) or not name.strip() for name in self.fields
            )
        ):
            raise ContractError("Claim.fields must be a nonempty list of field names")
        if len(set(self.fields)) != len(self.fields):
            raise ContractError("Claim.fields must not contain duplicates")


@dataclass
class Note(Message):
    """A typed caveat whose subject is validated and rendered by the controller."""

    kind: str
    subject: str

    def __post_init__(self) -> None:
        _check_choice("Note", "kind", self.kind, NOTE_KINDS)
        if not isinstance(self.subject, str) or not self.subject:
            raise ContractError("Note.subject must be a nonempty string")


@dataclass
class AnswerSelection(Message):
    """Stage 10 moderator output: selection only, never user-facing prose."""

    record_indices: list[int] = field(default_factory=list)
    claims: list[Claim] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "claims": ("list", Claim),
        "notes": ("list", Note),
    }

    def __post_init__(self) -> None:
        if not isinstance(self.record_indices, list) or any(
            type(index) is not int or index < 0 for index in self.record_indices
        ):
            raise ContractError(
                "AnswerSelection.record_indices must be non-negative integers"
            )
        if len(set(self.record_indices)) != len(self.record_indices):
            raise ContractError(
                "AnswerSelection.record_indices must not contain duplicates"
            )


@dataclass
class FinalAnswer(Message):
    """Controller-rendered stage 10 output.

    ``lines`` are derived only from validated records, typed notes, validation
    state and controller-owned failures.  The moderator never supplies them.
    """

    lines: list[str]
    claims: list[Claim] = field(default_factory=list)
    records: list[Any] = field(default_factory=list)
    failures: list[TypedError] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    _NESTED: ClassVar[Mapping[str, tuple[str, type]]] = {
        "claims": ("list", Claim),
        "failures": ("list", TypedError),
        "notes": ("list", Note),
    }

    def __post_init__(self) -> None:
        if not isinstance(self.lines, list) or any(
            not isinstance(line, str) or not line for line in self.lines
        ):
            raise ContractError("FinalAnswer.lines must be a list of nonempty strings")


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
