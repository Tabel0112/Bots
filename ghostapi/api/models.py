from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# JSON-like values remain intentionally open at the transport boundary. Operation-
# specific validators apply the narrower result and evidence schemas.
JsonValue = Any


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ValidationCheck(StrictModel):
    check_id: str
    passed: bool
    detail: str
    evidence_refs: list[str] = Field(min_length=1)


class ExecutionContext(StrictModel):
    request_id: str
    run_id: str
    subtask_id: str
    execution_id: str
    worker_kind: Literal["dom", "visual"]
    browser_backend: str
    reasoning_backend: str
    session_ref: str | None = None
    session_disposition: str


class InputSpec(StrictModel):
    type: Literal["string", "number", "integer", "boolean", "array", "object"]
    required: bool = True
    minimum: float | None = None
    maximum: float | None = None


class Target(StrictModel):
    strategy: Literal["semantic", "visual", "path"] = "semantic"
    role: str | None = None
    label: str | None = None
    description: str | None = None
    path: str | None = None


class ActionRecord(StrictModel):
    step_id: str = Field(min_length=1)
    action: Literal["navigate", "fill", "select", "click", "wait", "extract"]
    target: Target | None = None
    value: JsonValue = None
    input_parameter: str | None = None
    expected_state: dict[str, JsonValue] = Field(default_factory=dict)
    observation_before: str | None = None
    observation_after: str | None = None
    outcome: Literal["succeeded", "failed"]
    timestamp: str


class LookupRequest(StrictModel):
    schema_version: Literal["0.1", "0.2"] = "0.1"
    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    site_id: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    operation: str = Field(min_length=1, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    parameters: dict[str, JsonValue]
    required_outputs: list[str] = Field(default_factory=list)
    allowed_actions: list[str] = Field(default_factory=list)
    compatibility_key: str | None = None


class CandidateRequest(LookupRequest):
    description: str = Field(min_length=1)
    input_schema: dict[str, InputSpec]
    output_schema_id: str = Field(min_length=1)
    validator_id: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    trace: list[ActionRecord] = Field(min_length=1)
    result: JsonValue
    evidence_refs: list[str] = Field(min_length=1)
    context: ExecutionContext | None = None
    validation: list[ValidationCheck] = Field(default_factory=list)
    artifacts: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: str | None = None

    @model_validator(mode="after")
    def successful_trace_and_parameters(self) -> "CandidateRequest":
        if self.schema_version == "0.2":
            if (
                not self.context
                or not self.compatibility_key
                or not self.idempotency_key
            ):
                raise ValueError(
                    "integrated candidates need context, compatibility and idempotency keys"
                )
            if not self.validation or not all(c.passed for c in self.validation):
                raise ValueError(
                    "candidate requires independent passed validation checks"
                )
            if not any(s.action == "extract" for s in self.trace):
                raise ValueError("candidate requires a replayable extraction step")
            used = {s.input_parameter for s in self.trace if s.input_parameter}
            if used != set(self.parameters):
                raise ValueError(
                    "every parameter needs an explicit binding in the trace"
                )
            refs = set(self.evidence_refs)
            if not refs.issubset(self.artifacts):
                raise ValueError(
                    "candidate evidence must resolve to attached artifacts"
                )
            if any(not set(c.evidence_refs).issubset(refs) for c in self.validation):
                raise ValueError("validation references are not in candidate evidence")
            for step in self.trace:
                if step.action not in self.allowed_actions:
                    raise ValueError("trace exceeds allowed actions")
                if step.action in {"fill", "select", "click"} and (
                    not step.target
                    or step.target.strategy != "semantic"
                    or not step.target.role
                    or not step.target.label
                ):
                    raise ValueError("integrated replay requires semantic targets")
                if not step.observation_before or not step.observation_after:
                    raise ValueError("integrated actions require before/after evidence")
                if not {step.observation_before, step.observation_after}.issubset(refs):
                    raise ValueError("action evidence is missing")
        if any(step.outcome != "succeeded" for step in self.trace):
            raise ValueError("candidate traces must contain only successful actions")
        missing = set(self.parameters) - set(self.input_schema)
        if missing:
            raise ValueError(
                f"input_schema is missing parameters: {', '.join(sorted(missing))}"
            )
        for step in self.trace:
            if step.input_parameter and step.input_parameter not in self.parameters:
                raise ValueError(f"unknown input_parameter: {step.input_parameter}")
            if (
                step.input_parameter
                and step.value != self.parameters[step.input_parameter]
            ):
                raise ValueError(
                    f"step {step.step_id} value does not match its input_parameter"
                )
        return self


class RunReport(StrictModel):
    schema_version: Literal["0.1", "0.2"] = "0.1"
    task_id: str = Field(min_length=1)
    agent_id: str = Field(min_length=1)
    status: Literal["succeeded", "failed", "cancelled"]
    trace: list[ActionRecord] = Field(default_factory=list)
    result: JsonValue = None
    evidence_refs: list[str] = Field(default_factory=list)
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    error: dict[str, JsonValue] | None = None
    context: ExecutionContext | None = None
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    validation: list[ValidationCheck] = Field(default_factory=list)
    artifacts: dict[str, JsonValue] = Field(default_factory=dict)
    idempotency_key: str | None = None
    kind: Literal["reuse", "qualification"] = "reuse"

    @model_validator(mode="after")
    def integrated_evidence(self):
        if self.schema_version == "0.2":
            if not self.context or not self.idempotency_key:
                raise ValueError("integrated runs require context and an idempotency key")
            refs = set(self.evidence_refs)
            if not refs.issubset(self.artifacts):
                raise ValueError("run evidence must resolve to attached artifacts")
            if self.status == "succeeded" and (
                not self.validation or not all(c.passed for c in self.validation)
                or any(not set(c.evidence_refs).issubset(refs) for c in self.validation)
            ):
                raise ValueError("successful integrated runs require evidence-linked validation")
        return self


class QualificationReport(StrictModel):
    parameters: dict[str, JsonValue]
    validation_status: Literal["passed", "failed", "inconclusive"]
    empty_result: bool = False
    evidence_refs: list[str] = Field(min_length=1)
    run_id: str | None = None


class QualificationRequest(StrictModel):
    schema_version: Literal["0.1", "0.2"] = "0.1"
    agent_id: str = Field(min_length=1)
    reports: list[QualificationReport] = Field(min_length=3)


class ErrorBody(StrictModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(StrictModel):
    error: ErrorBody


class WorkflowSummary(StrictModel):
    skill_id: str
    version: int
    status: str
    site_id: str
    operation: str
    description: str


class LookupResponse(StrictModel):
    schema_version: Literal["0.1"] = "0.1"
    decision: Literal["reuse", "explore"]
    reason: ErrorBody | None = None
    workflow: dict[str, Any] | None = None
