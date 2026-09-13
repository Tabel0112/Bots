"""Translate validated DOM evidence into parameterized Ghost procedures."""

import hashlib
import json

from ghostapi.api.models import CandidateRequest

from .policy import resolve_element
from .schemas import (
    Action,
    Decision,
    FieldSource,
    RecordMapping,
    ValueOrigin,
    WorkerError,
)
from .schemas import FailureCode as C
from .verifier import within

ACTIONS = {
    "navigate": "navigate",
    "fill": "fill",
    "select": "select",
    "click": "click",
    "wait_for": "wait",
    "extract_records": "extract",
}
REVERSE = {value: key for key, value in ACTIONS.items()}


class NotCompilable(ValueError):
    pass


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def compatibility_key(task, site):
    # Trusted site controls/validators and task acceptance conditions are part of scope.
    return digest(
        {
            "adapter": "semantic-replay.1",
            "site": site.model_dump(mode="json"),
            "conditions": [c.model_dump() for c in task.success_conditions],
            "start_url": task.start_url or site.start_url,
            "domains": sorted(task.allowed_domains),
            "paths": sorted(task.allowed_url_patterns),
        }
    )


def ghost_site_id(site_id: str) -> str:
    """Registry-safe site id: Ghost ids allow [A-Za-z0-9._-], so ``open:host`` becomes ``open-host``."""
    return site_id.replace(":", "-")


def lookup_request(task, site, agent_id):
    return {
        "schema_version": "0.2",
        "task_id": f"{task.run_id}/{task.subtask_id}",
        "agent_id": agent_id,
        "site_id": ghost_site_id(task.site_id),
        "operation": task.operation,
        "parameters": task.parameters,
        "required_outputs": sorted(site.record_schema["properties"]),
        "allowed_actions": sorted({ACTIONS[a] for a in task.allowed_actions if a in ACTIONS}),
        "compatibility_key": compatibility_key(task, site),
    }


def context(report, worker_kind):
    return {
        name: getattr(report, name)
        for name in (
            "request_id",
            "run_id",
            "subtask_id",
            "execution_id",
            "browser_backend",
            "reasoning_backend",
            "session_ref",
            "session_disposition",
        )
    } | {"worker_kind": worker_kind}


def field_descriptor(element):
    return {
        "tag": element.tag,
        "role": element.role,
        "attributes": {k: v for k, v in element.attributes.items() if k in {"name", "data-testid"}},
    }


def matches_field(element, descriptor):
    return (
        element.tag == descriptor.get("tag")
        and element.role == descriptor.get("role")
        and all(element.attributes.get(k) == v for k, v in descriptor.get("attributes", {}).items())
    )


def extraction_recipe(action, obs):
    if not action.records:
        raise NotCompilable(
            "An empty exploration cannot teach field extraction; learn from a nonempty result first."
        )
    recipes = []
    for mapping in action.records:
        fields = []
        for field in mapping.fields:
            element = resolve_element(obs, field.element_ref)
            descriptor = field_descriptor(element)
            found = [
                e
                for e in obs.elements
                if within(e.ref, mapping.container_ref, obs) and matches_field(e, descriptor)
            ]
            if len(found) != 1:
                raise NotCompilable(
                    "A field needs a stable unique identity inside every result row."
                )
            fields.append(
                {"field": field.field, "attribute": field.attribute, "target": descriptor}
            )
        recipes.append(sorted(fields, key=lambda f: f["field"]))
    if any(recipe != recipes[0] for recipe in recipes):
        raise NotCompilable("Result rows require different extraction recipes.")
    return recipes[0]


def candidate_request(task, site, report, agent_id, worker_kind="dom"):
    if (
        report.outcome != "succeeded"
        or not report.validation
        or not all(c.passed for c in report.validation)
    ):
        raise NotCompilable("Only independently validated success can become a candidate.")
    if report.failures or report.session_disposition == "cleanup_failed":
        raise NotCompilable(
            "The first compiler requires an uninterrupted successful trace and cleanup."
        )
    if not report.observations:
        raise NotCompilable("The run has no observations.")
    if report.observations[0].url != (task.start_url or site.start_url):
        raise NotCompilable("The trace starts in an unqualified intermediate page state.")
    observations = {o.observation_id: o for o in report.observations}
    trace = []
    for step in report.action_trace:
        if step.action_type == "request_visual_fallback" and report.visual_report:
            visual_trace = report.visual_report.get("ghost_trace")
            if visual_trace is None:
                raise NotCompilable("Visual actions do not have a complete semantic replay trace.")
            trace.extend(visual_trace)
            continue
        if step.action_type in {"report_success", "inspect_element"}:
            continue
        if step.action_type not in ACTIONS or step.outcome != "succeeded":
            raise NotCompilable("Trace contains an unsupported, failed, or rejected operation.")
        a = step.arguments
        obs = observations[step.before_observation_id]
        target, value, parameter = None, None, None
        expected = {"change": a.expected_change}
        # Extraction replays through the field recipe over the page's record refs and
        # never resolves a semantic target, so the container the model happened to
        # point at must not become one: it is often a label-less wrapper such as
        # <section id="results"> (which would fail the name check below and block the
        # candidate), or a result row whose accessible name is data-dependent (which
        # would make every changed-input replay fail with TARGET_NOT_FOUND).
        if a.target_ref and step.action_type != "extract_records":
            element = resolve_element(obs, a.target_ref)
            if not element.name:
                raise NotCompilable("Coordinate-only or unnamed targets cannot be reused.")
            target = {"strategy": "semantic", "role": element.role, "label": element.name}
        if step.action_type in {"fill", "select"}:
            origin = a.value_origin
            if origin is None:
                raise NotCompilable("Input origin is missing.")
            if origin.kind == "parameter":
                parameter = origin.key
                value = task.parameters[parameter]
                if a.value != str(value):
                    raise NotCompilable("Observed input disagrees with its parameter.")
            else:
                value = a.value
                expected["literal_key"] = origin.key
        elif step.action_type == "navigate":
            value = a.url
        elif step.action_type == "extract_records":
            expected["fields"] = extraction_recipe(a, obs)
        trace.append(
            {
                "step_id": step.step_id,
                "action": ACTIONS[step.action_type],
                "target": target,
                "value": value,
                "input_parameter": parameter,
                "expected_state": expected,
                "observation_before": step.before_observation_id,
                "observation_after": step.after_observation_id,
                "outcome": "succeeded",
                "timestamp": step.started_at.isoformat(),
            }
        )
    properties = site.parameters_schema["properties"]
    schema = {
        key: {
            "type": spec["type"],
            "required": key in site.parameters_schema.get("required", []),
            **{k: spec[k] for k in ("minimum", "maximum") if k in spec},
        }
        for key, spec in properties.items()
    }
    body = lookup_request(task, site, agent_id) | {
        "description": f"Verified {task.operation} on {task.site_id}",
        "input_schema": schema,
        "output_schema_id": task.output_schema_id,
        "validator_id": "dom-verifier.1:" + compatibility_key(task, site),
        "preconditions": ["start_url=" + report.observations[0].url],
        "trace": trace,
        "result": [r.data for r in report.records],
        "evidence_refs": report.evidence_refs,
        "context": context(report, worker_kind),
        "validation": [c.model_dump() for c in report.validation],
        "artifacts": {o.observation_id: o.model_dump(mode="json") for o in report.observations},
        "idempotency_key": "candidate:" + report.execution_id,
    }
    if report.visual_report:
        body["artifacts"].update(report.visual_report.get("ghost_artifacts", {}))
        body["evidence_refs"] = sorted(
            set(body["evidence_refs"]) | set(report.visual_report.get("ghost_artifacts", {}))
        )
    try:
        return CandidateRequest.model_validate(body).model_dump(mode="json")
    except ValueError as exc:
        raise NotCompilable("Trace lacks complete parameter bindings or replay evidence.") from exc


def run_report(task, report, agent_id, worker_kind="dom", kind="reuse"):
    # Keep the native trace as evidence: failed/rejected actions must never disappear.
    artifacts = {o.observation_id: o.model_dump(mode="json") for o in report.observations}
    artifacts["native_trace"] = [a.model_dump(mode="json") for a in report.action_trace]
    if report.visual_report:
        artifacts["visual_report"] = report.visual_report
    return {
        "schema_version": "0.2",
        "task_id": f"{task.run_id}/{task.subtask_id}",
        "agent_id": agent_id,
        "status": report.outcome if report.outcome in {"succeeded", "cancelled"} else "failed",
        "trace": [],
        "result": [r.data for r in report.records],
        "parameters": task.parameters,
        "evidence_refs": report.evidence_refs,
        "artifacts": artifacts,
        "validation": [c.model_dump() for c in report.validation],
        "context": context(report, worker_kind),
        "metrics": report.metrics.model_dump(),
        "error": report.failures[-1].model_dump(mode="json") if report.failures else None,
        "kind": kind,
        "idempotency_key": kind + ":" + report.execution_id,
    }


class ReplayReasoner:
    """Fresh target resolution; Worker still enforces every policy, budget, and validator."""

    model_id = "ghost-semantic-replay"
    is_replay = True

    def __init__(self, workflow, task, site):
        if workflow.get("compatibility_key") != compatibility_key(task, site):
            raise WorkerError(C.PRECONDITION_FAILED, "Workflow scope or validator changed.")
        if workflow.get("output_schema_id") != task.output_schema_id:
            raise WorkerError(C.PRECONDITION_FAILED, "Workflow output schema is incompatible.")
        self.steps, self.index = workflow["bound_steps"], 0
        self.preconditions = workflow.get("preconditions", [])

    async def decide(self, ctx):
        from .schemas import Observation

        obs = Observation.model_validate_json(json.dumps(ctx["observation"]))
        if self.index == 0 and self.preconditions != ["start_url=" + obs.url]:
            raise WorkerError(
                C.PRECONDITION_FAILED, "Replay start page differs from the learned page."
            )
        args = dict(
            observation_id=obs.observation_id,
            target_ref=None,
            semantic_target="",
            value=None,
            value_origin=None,
            url=None,
            expected_change="unchanged",
            reason="Execute the saved procedure against fresh evidence.",
            records=[],
            failure_code=None,
            visual_question=None,
        )
        if self.index == len(self.steps):
            self.index += 1
            return Decision(action_type="report_success", arguments=Action(**args))
        if self.index > len(self.steps):
            raise WorkerError(C.PRECONDITION_FAILED, "Replay already finished.")
        step = self.steps[self.index]
        self.index += 1
        kind = REVERSE.get(step.get("action"))
        if not kind:
            raise WorkerError(C.UNSUPPORTED_OPERATION, "Saved action has no replay implementation.")
        expected = step.get("expected_state", {})
        args["expected_change"] = expected.get("change", "unchanged")
        target = step.get("target")
        if target:
            matches = [
                e
                for e in obs.elements
                if e.role == target.get("role") and e.name == target.get("label")
            ]
            if len(matches) != 1:
                raise WorkerError(
                    C.TARGET_AMBIGUOUS if matches else C.TARGET_NOT_FOUND,
                    "Saved semantic target is not uniquely available.",
                )
            args.update(target_ref=matches[0].ref, semantic_target=matches[0].name)
        if kind in {"fill", "select"}:
            # Target's trusted parameter binding is checked again by validate_action.
            element = resolve_element(obs, args["target_ref"])
            parameter = element.attributes.get("bound_parameter")
            if "literal_key" in expected:
                args["value_origin"] = ValueOrigin(kind="literal", key=expected["literal_key"])
            elif parameter:
                args["value_origin"] = ValueOrigin(kind="parameter", key=parameter)
            else:
                raise WorkerError(
                    C.ACTION_REJECTED, "Replay input has no trusted parameter binding."
                )
            args["value"] = str(step.get("value"))
        elif kind == "navigate":
            args["url"] = step.get("value")
        elif kind == "extract_records":
            if not expected.get("fields"):
                raise WorkerError(C.EXTRACTION_FAILED, "Workflow has no extraction recipe.")
            mappings = []
            for ref in obs.signals.get("record_refs", []):
                fields = []
                for recipe in expected["fields"]:
                    found = [
                        e
                        for e in obs.elements
                        if within(e.ref, ref, obs) and matches_field(e, recipe["target"])
                    ]
                    if len(found) != 1:
                        raise WorkerError(
                            C.EXTRACTION_FAILED, "Result field is missing or ambiguous."
                        )
                    fields.append(
                        FieldSource(
                            field=recipe["field"],
                            element_ref=found[0].ref,
                            attribute=recipe["attribute"],
                        )
                    )
                mappings.append(RecordMapping(container_ref=ref, fields=fields))
            args["records"] = mappings
        return Decision(action_type=kind, arguments=Action(**args))
