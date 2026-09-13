from __future__ import annotations

import math
from typing import Any

from .models import CandidateRequest, LookupRequest
from .storage import WorkflowStore


def value_matches(value: Any, spec: dict[str, Any]) -> bool:
    expected = spec["type"]
    checks = {
        "string": lambda item: isinstance(item, str),
        "number": lambda item: type(item) in (int, float),
        "integer": lambda item: type(item) is int,
        "boolean": lambda item: type(item) is bool,
        "array": lambda item: isinstance(item, list),
        "object": lambda item: isinstance(item, dict),
    }
    if not checks[expected](value):
        return False
    if expected in ("number", "integer"):
        if not math.isfinite(value):
            return False
        if spec.get("minimum") is not None and value < spec["minimum"]:
            return False
        if spec.get("maximum") is not None and value > spec["maximum"]:
            return False
    return True


def compatible(request: LookupRequest, definition: dict[str, Any]) -> bool:
    if request.schema_version != definition.get("schema_version", "0.1"):
        return False
    if request.compatibility_key != definition.get("compatibility_key"):
        return False
    schema = definition["input_schema"]
    if set(request.parameters) - set(schema):
        return False
    if any(spec.get("required", True) and name not in request.parameters for name, spec in schema.items()):
        return False
    if any(not value_matches(value, schema[name]) for name, value in request.parameters.items()):
        return False
    if not set(request.required_outputs).issubset(definition["required_outputs"]):
        return False
    if not set(step["action"] for step in definition["steps"]).issubset(request.allowed_actions):
        return False
    return True


def bind_value(value: Any, parameters: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        if set(value) == {"parameter"}:
            return parameters[value["parameter"]]
        return {key: bind_value(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [bind_value(item, parameters) for item in value]
    return value


def bind(definition: dict[str, Any], parameters: dict[str, Any]) -> list[dict[str, Any]]:
    bound = []
    for step in definition["steps"]:
        step = dict(step)
        step["value"] = bind_value(step.get("value"), parameters)
        step["expected_state"] = bind_value(step.get("expected_state", {}), parameters)
        bound.append(step)
    return bound


def lookup(store: WorkflowStore, request: LookupRequest) -> dict[str, Any]:
    for candidate in store.qualified_candidates(request.site_id, request.operation):
        definition = candidate["definition"]
        if compatible(request, definition):
            return {
                "schema_version": request.schema_version,
                "decision": "reuse",
                "workflow": {
                    "skill_id": candidate["skill_id"],
                    "version": candidate["version"],
                    "status": candidate["status"],
                    "bound_steps": bind(definition, request.parameters),
                    "output_schema_id": definition["output_schema_id"],
                    "validator_id": definition["validator_id"],
                    "preconditions": definition["preconditions"],
                    "compatibility_key": definition.get("compatibility_key"),
                },
            }
    return {
        "schema_version": request.schema_version,
        "decision": "explore",
        "reason": {
            "code": "NO_MATCH",
            "message": "No qualified workflow is compatible with this task.",
            "retryable": False,
        },
    }


def compile_candidate(request: CandidateRequest) -> dict[str, Any]:
    steps = []
    for action in request.trace:
        step = action.model_dump(exclude={"outcome", "timestamp", "observation_before", "observation_after"})
        if action.input_parameter:
            step["value"] = {"parameter": action.input_parameter}
            if request.schema_version == "0.1":
                step["expected_state"] = parameterize_observed_value(
                    step["expected_state"], action.value, action.input_parameter
                )
        step.pop("input_parameter", None)
        steps.append(step)
    return {
        "schema_version": request.schema_version,
        "site_id": request.site_id,
        "operation": request.operation,
        "description": request.description,
        "input_schema": {name: spec.model_dump() for name, spec in request.input_schema.items()},
        "required_outputs": request.required_outputs,
        "allowed_actions": request.allowed_actions,
        "preconditions": request.preconditions,
        "compatibility_key": request.compatibility_key,
        "parameters": request.parameters,
        "steps": steps,
        "output_schema_id": request.output_schema_id,
        "validator_id": request.validator_id,
        "source": {
            "task_id": request.task_id,
            "agent_id": request.agent_id,
            "evidence_refs": request.evidence_refs,
            "context": request.context.model_dump(mode="json") if request.context else None,
            "validation": [c.model_dump() for c in request.validation],
            "artifacts": request.artifacts,
        },
    }


def parameterize_observed_value(value: Any, observed: Any, parameter: str) -> Any:
    if type(value) is type(observed) and value == observed:
        return {"parameter": parameter}
    if isinstance(value, dict):
        return {key: parameterize_observed_value(item, observed, parameter) for key, item in value.items()}
    if isinstance(value, list):
        return [parameterize_observed_value(item, observed, parameter) for item in value]
    return value
