"""Versioned worker contract; independent of the old synthetic ARGUS contract."""

import re
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uid() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class FailureCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    DOMAIN_NOT_ALLOWED = "DOMAIN_NOT_ALLOWED"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_AMBIGUOUS = "TARGET_AMBIGUOUS"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    ACTION_REJECTED = "ACTION_REJECTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"
    VALIDATION_FAILED = "VALIDATION_FAILED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    NO_PROGRESS = "NO_PROGRESS"
    CANCELLED = "CANCELLED"
    MODEL_ERROR = "MODEL_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class Failure(Model):
    code: FailureCode
    message: str
    retryable: bool = False
    evidence_refs: list[str] = Field(default_factory=list)


class WorkerError(Exception):
    def __init__(self, code: FailureCode, message: str, retryable: bool = False):
        self.failure = Failure(code=code, message=message, retryable=retryable)
        super().__init__(message)


class Budgets(Model):
    max_actions: Annotated[int, Field(ge=1, le=100)] = 20
    max_model_calls: Annotated[int, Field(ge=1, le=150)] = 30
    max_retries: Annotated[int, Field(ge=1, le=10)] = 3
    max_runtime_seconds: Annotated[int, Field(ge=1, le=600)] = 180
    no_progress_limit: Annotated[int, Field(ge=2, le=10)] = 4


ActionType = Literal[
    "navigate",
    "fill",
    "select",
    "click",
    "wait_for",
    "inspect_element",
    "extract_records",
    "report_success",
    "report_failure",
    "request_visual_fallback",
]
ACTIONS = list(ActionType.__args__)


class SessionSpec(Model):
    ownership: Literal["worker", "argus"]
    session_ref: str | None = None
    close_on_finish: bool = False

    @model_validator(mode="after")
    def check_owner(self):
        if self.ownership == "argus" and not self.session_ref:
            raise ValueError("ARGUS-owned sessions require session_ref")
        if self.ownership == "worker" and self.session_ref:
            raise ValueError("Worker-owned sessions are created by the worker")
        if self.session_ref and not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", self.session_ref):
            raise ValueError("session_ref must be an opaque ID, not a connection URL")
        return self


class Dependency(Model):
    subtask_id: str
    status: Literal["succeeded", "failed", "pending"]
    result: dict[str, Any] = Field(default_factory=dict)


class SuccessCondition(Model):
    """Additional deterministic checks, ANDed with the site's mandatory checks."""

    kind: Literal["min_records", "max_records", "field_equals", "field_lte", "field_contains"]
    field: str | None = None
    parameter: str | None = None
    value: str | int | float | None = None


class SubtaskRequest(Model):
    schema_version: Literal["0.2"]
    request_id: Annotated[str, Field(min_length=1, max_length=128)]
    run_id: Annotated[str, Field(min_length=1, max_length=128)]
    subtask_id: Annotated[str, Field(min_length=1, max_length=128)]
    objective: Annotated[str, Field(min_length=1, max_length=4000)]
    site_id: str
    operation: Literal["search_extract", "open_search"]
    start_url: str | None = None
    session: SessionSpec
    parameters: dict[str, str | int | float | bool | None]
    output_schema_id: str
    success_conditions: list[SuccessCondition]
    allowed_actions: Annotated[list[ActionType], Field(min_length=1)]
    allowed_domains: Annotated[list[str], Field(min_length=1)]
    allowed_url_patterns: list[str] = Field(default_factory=list)
    budgets: Budgets = Field(default_factory=Budgets)
    required_evidence: list[Literal["observations", "action_trace", "field_sources"]]
    prerequisites: list[Dependency] = Field(default_factory=list)
    visual_fallback_available: bool = True
    expected_record_shape: list[str] = Field(default_factory=list, max_length=30)


class ValueOrigin(Model):
    kind: Literal["parameter", "literal"]
    key: str


class FieldSource(Model):
    field: str
    element_ref: str | None
    attribute: Literal["text", "value", "href"]


class RecordMapping(Model):
    container_ref: str
    fields: list[FieldSource]


class Action(Model):
    """All fields required for strict OpenAI function schemas; unused fields are null/empty."""

    observation_id: str
    target_ref: str | None
    semantic_target: str
    value: str | None
    value_origin: ValueOrigin | None
    url: str | None
    expected_change: Literal["changed", "unchanged", "visible", "value_equals", "results_ready"]
    reason: Annotated[str, Field(max_length=400)]
    records: list[RecordMapping]
    failure_code: str | None
    visual_question: str | None


class Decision(Model):
    action_type: ActionType
    arguments: Action


class Element(Model):
    ref: str
    tag: str
    role: str
    name: str
    text: str
    value: str | None = None
    href: str | None = None
    input_type: str | None = None
    disabled: bool = False
    parent_ref: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    options: list[dict[str, str]] = Field(default_factory=list)
    permitted_actions: list[str] = Field(default_factory=list)
    text_truncated: bool = False


class Observation(Model):
    observation_id: str = Field(default_factory=uid)
    execution_id: str
    run_id: str
    timestamp: datetime = Field(default_factory=utcnow)
    url: str
    title: str
    content_hash: str
    visible_text: str
    elements: list[Element]
    signals: dict[str, Any] = Field(default_factory=dict)
    truncated: bool = False
    limitations: list[str] = Field(default_factory=list)


class ExtractedRecord(Model):
    data: dict[str, Any]
    source_observation_id: str
    container_ref: str
    field_sources: list[FieldSource]
    retrieved_at: datetime


class ActionRecord(Model):
    step_id: str = Field(default_factory=uid)
    action_type: str
    arguments: Action
    started_at: datetime = Field(default_factory=utcnow)
    completed_at: datetime | None = None
    before_observation_id: str
    after_observation_id: str | None = None
    outcome: Literal["succeeded", "failed", "rejected", "proposed"] = "proposed"
    expected_change_observed: bool | None = None
    failure: Failure | None = None
    execution_result: dict[str, Any] = Field(default_factory=dict)


class CheckResult(Model):
    check_id: str
    passed: bool
    detail: str
    evidence_refs: list[str] = Field(default_factory=list)


class Metrics(Model):
    actions: int = 0
    model_calls: int = 0
    retries: int = 0
    elapsed_ms: int = 0
    input_tokens: int | None = None
    output_tokens: int | None = None


class VisualHandoff(Model):
    run_id: str
    subtask_id: str
    url: str
    observation_id: str
    intended_operation: str
    unresolved_target: str
    candidates: list[str]
    attempted_step_ids: list[str]
    reason_code: str
    remaining_budget: dict[str, int | float]
    question: str
    available: bool


class SubtaskReport(Model):
    schema_version: Literal["0.2"] = "0.2"
    request_id: str
    run_id: str
    subtask_id: str
    execution_id: str = Field(default_factory=uid)
    browser_backend: str = "unknown"
    reasoning_backend: str = "unknown"
    outcome: Literal["succeeded", "failed", "inconclusive", "needs_visual", "cancelled"] = "failed"
    summary: str = "No verified result."
    records: list[ExtractedRecord] = Field(default_factory=list)
    validation: list[CheckResult] = Field(default_factory=list)
    observations: list[Observation] = Field(default_factory=list)
    action_trace: list[ActionRecord] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    parameter_origins: dict[str, ValueOrigin] = Field(default_factory=dict)
    metrics: Metrics = Field(default_factory=Metrics)
    failures: list[Failure] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    visual_handoff: VisualHandoff | None = None
    ghost: dict[str, Any] = Field(default_factory=dict)
    visual_report: dict[str, Any] | None = None
    final_url: str | None = None
    session_ref: str | None = None
    session_disposition: Literal["not_started", "released", "retained", "cleanup_failed"] = (
        "not_started"
    )
