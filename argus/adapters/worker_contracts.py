"""Translation between ARGUS subtask messages and the DOM worker's contract (P3-CONTRACTS).

The controller speaks :class:`argus.contracts.SubtaskInput` and
:class:`argus.contracts.WorkerReport`; Tianqi's DOM worker speaks
``Agents.browser_worker.schemas.SubtaskRequest`` 0.2 and ``SubtaskReport``.
This module is the only place the two vocabularies meet:

* :func:`to_subtask_request` builds a worker request.  Only registry operations
  listed in :data:`OPERATION_MAP` are accepted; anything else raises
  ``ContractError(PRECONDITION_FAILED)`` before any browser work.  Sessions are
  worker-owned tonight (ARGUS-3 decision 4) and visual fallback is offered only
  when ``UITARS_BASE_URL`` is configured.
* :func:`to_worker_report` converts the worker's report into the ARGUS report
  plus a separate *context* dict.  The worker's own validation, Ghost block and
  session disposition never enter the ARGUS report; the Ghost bridge reads them
  from the context instead.
* :func:`failed_report` is the exception-to-report path lifted from
  ``argus.live_runtime``: it names only the exception class, never provider text.

Every typed failure code produced here is a member of ``contracts.ERROR_CODES``.
"""

from __future__ import annotations

import math
import os
from typing import Any

from Agents.browser_worker.config import SiteConfig
from Agents.browser_worker.schemas import (
    ACTIONS,
    ActionRecord,
    Budgets,
    ExtractedRecord,
    Failure,
    SessionSpec,
    SubtaskReport,
    SubtaskRequest,
    SuccessCondition,
)
from argus.contracts import (
    ERROR_CODES,
    SCHEMA_VERSION,
    ContractError,
    SubtaskInput,
    TypedError,
    WorkerReport,
)

__all__ = [
    "FAILURE_CODE_MAP",
    "OPERATION_MAP",
    "OUTCOME_MAP",
    "REQUIRED_EVIDENCE",
    "WORKER_NAME",
    "failed_report",
    "prepare_request",
    "to_subtask_request",
    "to_worker_report",
    "translate_success_conditions",
]

#: Registry operation -> worker operation.  Decision 8: only the catalog search tonight.
OPERATION_MAP: dict[str, str] = {"search_products": "search_extract"}

#: The ``worker`` field of every ARGUS report produced from the DOM worker.
WORKER_NAME = "dom"

#: Everything the worker can produce; ARGUS asks for all of it.
REQUIRED_EVIDENCE = ["observations", "action_trace", "field_sources"]

#: Worker ``FailureCode`` -> ARGUS ``ERROR_CODES``.  Codes missing here map to
#: ``EXTRACTION_FAILED``.
FAILURE_CODE_MAP: dict[str, str] = {
    "BUDGET_EXHAUSTED": "BUDGET_EXCEEDED",
    "ACTION_REJECTED": "ACTION_CLASS_NOT_ALLOWED",
    "MODEL_TIMEOUT": "EXTRACTION_FAILED",
    "MODEL_ERROR": "EXTRACTION_FAILED",
    "NO_PROGRESS": "EXTRACTION_FAILED",
    "STALE_OBSERVATION": "EXTRACTION_FAILED",
    "AUTH_REQUIRED": "AUTH_REQUIRED",
    "CANCELLED": "CANCELLED",
}

#: Worker outcome -> ARGUS outcome.  Every outcome except ``succeeded`` is a
#: failure for the controller; the typed failure says which kind.
OUTCOME_MAP: dict[str, str] = {
    "succeeded": "succeeded",
    "failed": "failed",
    "needs_visual": "failed",
    "inconclusive": "failed",
    "cancelled": "failed",
}

_DEFAULT_FAILURE_CODE = "EXTRACTION_FAILED"
_SCALAR_TYPES = (str, int, float, bool, type(None))
_OBJECTIVE_MAX = 4000

#: The one registry success-condition string (``argus.planner.SUCCESS_CONDITIONS``)
#: with a typed worker equivalent: ``field_lte`` on ``price`` bound to ``max_price``.
_PRICE_CONDITION = "price within max_price when given"


def _field_bounds(bounds: type[Budgets], name: str) -> tuple[int, int]:
    """The ``ge``/``le`` limits pydantic declared for one ``Budgets`` field."""
    low, high = 1, 10**9
    for meta in bounds.model_fields[name].metadata:
        low = getattr(meta, "ge", low) if getattr(meta, "ge", None) is not None else low
        high = (
            getattr(meta, "le", high) if getattr(meta, "le", None) is not None else high
        )
    return low, high


def _clamp_ceil(value: Any, bounds: tuple[int, int]) -> int:
    low, high = bounds
    try:
        number = math.ceil(float(value))
    except (TypeError, ValueError, OverflowError):
        return low
    return max(low, min(high, number))


def _precondition(message: str, subtask_id: str | None) -> ContractError:
    return ContractError(message, code="PRECONDITION_FAILED", step_id=subtask_id)


def _site_key(site_id: Any) -> str:
    return str(site_id or "").removeprefix("open:")


def translate_success_conditions(
    conditions: list[str], parameters: dict[str, Any], site: SiteConfig
) -> tuple[list[SuccessCondition], list[str]]:
    """Map registry condition strings and result-shaping parameters to worker checks.

    Returns ``(typed conditions, limitations)``.  A registry string without a
    typed equivalent, or a parameter the worker cannot check, becomes a
    limitation string so nothing is dropped silently.  Only fields the site's
    record schema declares are referenced, since the worker rejects others.
    """
    fields = set(site.record_schema.get("properties", {}))
    typed: list[SuccessCondition] = []
    limitations: list[str] = []

    for text in conditions:
        if text == _PRICE_CONDITION:
            if "max_price" not in parameters:
                limitations.append(f"{text!r}: max_price not given; condition vacuous")
            elif "price" not in fields:
                limitations.append(f"{text!r}: site records carry no price field")
            else:
                typed.append(
                    SuccessCondition(
                        kind="field_lte", field="price", parameter="max_price"
                    )
                )
        else:
            limitations.append(
                f"{text!r}: no typed worker check; judged by the moderator"
            )

    max_results = parameters.get("max_results")
    if max_results is not None:
        # Deliberately NOT a worker success condition: the worker's success
        # conditions are part of Ghost's compatibility key, so a per-request
        # result count would make every differently-sized request a different
        # workflow and defeat reuse (observed on INT-2, 2026-09-13). The
        # controller's limit criterion caps the published records instead.
        limitations.append(
            f"max_results={max_results!r} is applied by the controller's limit "
            "criterion, not as a worker success condition"
        )
    currency = parameters.get("currency")
    if currency is not None:
        if "currency" in fields and isinstance(currency, str) and currency:
            typed.append(
                SuccessCondition(kind="field_equals", field="currency", value=currency)
            )
        else:
            limitations.append(
                f"currency={currency!r}: site records carry no currency field"
            )
    return typed, limitations


def _split_parameters(
    parameters: dict[str, Any], site: SiteConfig, subtask_id: str
) -> tuple[dict[str, Any], list[str]]:
    """Keep the parameters the site's schema declares; name the rest as limitations.

    ``max_results`` and ``currency`` are registry parameters the worker checks
    through success conditions (see :func:`translate_success_conditions`), so
    they are never forwarded as request parameters.
    """
    declared = site.parameters_schema.get("properties")
    passthrough: dict[str, Any] = {}
    limitations: list[str] = []
    for key, value in parameters.items():
        if not isinstance(value, _SCALAR_TYPES):
            raise _precondition(
                f"parameter {key!r} must be a scalar for the DOM worker; got {type(value).__name__}",
                subtask_id,
            )
        if key in ("max_results", "currency"):
            continue
        if isinstance(declared, dict) and key not in declared:
            limitations.append(
                f"parameter {key!r} is not accepted by site {site.site_id!r}"
            )
            continue
        passthrough[key] = value
    return passthrough, limitations


def prepare_request(
    subtask_input: SubtaskInput,
    sites: dict[str, SiteConfig],
    *,
    settings: Any = None,
) -> tuple[SubtaskRequest, list[str]]:
    """Build the worker's ``SubtaskRequest`` 0.2 and the limitations it carries.

    The limitations name registry conditions and parameters the worker cannot
    check itself; the toolbox keeps them in the subtask context.  Raises
    ``ContractError(PRECONDITION_FAILED)`` for an operation outside
    :data:`OPERATION_MAP`, a site without a ``SiteConfig`` or a non-scalar
    parameter.  ``settings`` is accepted for a future toolbox-level override and
    is not read tonight: visual fallback follows ``UITARS_BASE_URL`` only.
    """
    subtask = subtask_input.subtask
    subtask_id = subtask.subtask_id
    worker_operation = OPERATION_MAP.get(subtask.operation)
    if worker_operation is None:
        raise _precondition(
            f"operation {subtask.operation!r} has no DOM worker mapping; "
            f"supported: {', '.join(sorted(OPERATION_MAP))}",
            subtask_id,
        )
    site_key = _site_key(subtask.site_id)
    site = sites.get(site_key)
    if site is None:
        raise _precondition(
            f"site {subtask.site_id!r} has no worker site configuration", subtask_id
        )

    parameters = (
        dict(subtask.parameters) if isinstance(subtask.parameters, dict) else {}
    )
    passthrough, limitations = _split_parameters(parameters, site, subtask_id)
    conditions, untranslated = translate_success_conditions(
        list(subtask.success_conditions), parameters, site
    )
    limitations.extend(untranslated)

    goal = (
        subtask.goal if isinstance(subtask.goal, str) and subtask.goal.strip() else None
    )
    if goal is None:
        rendered = ", ".join(f"{key}={value!r}" for key, value in parameters.items())
        goal = f"{subtask.operation} with {rendered}" if rendered else subtask.operation
    objective = goal[:_OBJECTIVE_MAX]

    budget = subtask_input.budget
    budgets = Budgets(
        max_actions=_clamp_ceil(
            budget.max_actions, _field_bounds(Budgets, "max_actions")
        ),
        max_runtime_seconds=_clamp_ceil(
            budget.max_seconds, _field_bounds(Budgets, "max_runtime_seconds")
        ),
    )

    request = SubtaskRequest(
        schema_version="0.2",
        request_id=subtask_input.request_id or subtask_input.run_id,
        run_id=subtask_input.run_id,
        subtask_id=subtask_id,
        objective=objective,
        site_id=site.site_id,
        operation=worker_operation,
        start_url=site.start_url,
        session=SessionSpec(ownership="worker"),
        parameters=passthrough,
        output_schema_id=site.output_schema_id,
        success_conditions=conditions,
        allowed_actions=list(ACTIONS),
        allowed_domains=list(site.allowed_domains),
        allowed_url_patterns=list(site.allowed_url_patterns),
        budgets=budgets,
        required_evidence=list(REQUIRED_EVIDENCE),
        prerequisites=[],
        visual_fallback_available=bool(os.environ.get("UITARS_BASE_URL")),
    )
    return request, limitations


def to_subtask_request(
    subtask_input: SubtaskInput,
    sites: dict[str, SiteConfig],
    *,
    settings: Any = None,
) -> SubtaskRequest:
    """Build the worker's ``SubtaskRequest`` 0.2 for one ARGUS subtask.

    Same checks as :func:`prepare_request`; use that when the limitations are
    needed as well.
    """
    return prepare_request(subtask_input, sites, settings=settings)[0]


def _typed_code(worker_code: str) -> str:
    code = FAILURE_CODE_MAP.get(worker_code, _DEFAULT_FAILURE_CODE)
    if code not in ERROR_CODES:  # pragma: no cover - guards a bad table edit
        code = _DEFAULT_FAILURE_CODE
    return code


def _typed_failure(failure: Failure, subtask_id: str) -> TypedError:
    return TypedError(
        code=_typed_code(str(failure.code)),
        message=failure.message,
        retryable=bool(failure.retryable),
        step_id=subtask_id,
        evidence_refs=list(failure.evidence_refs),
    )


def _finding(record: ExtractedRecord) -> dict[str, Any]:
    dumped = record.model_dump(mode="json")
    finding = dict(dumped["data"])
    finding["source_observation_id"] = dumped["source_observation_id"]
    finding["retrieved_at"] = dumped["retrieved_at"]
    return finding


def _action(record: ActionRecord) -> dict[str, Any]:
    dumped = record.model_dump(mode="json")
    arguments = dumped["arguments"]
    return {
        "step_id": dumped["step_id"],
        "action": {
            "name": dumped["action_type"],
            "input": {
                "target_ref": arguments["target_ref"],
                "value": arguments["value"],
                "value_origin": arguments["value_origin"],
                "url": arguments["url"],
                "expected_change": arguments["expected_change"],
                "records": len(arguments["records"]),
                "failure_code": arguments["failure_code"],
                "visual_question": arguments["visual_question"],
            },
        },
        "semantic_target": arguments["semantic_target"],
        "observation_before": dumped["before_observation_id"],
        "observation_after": dumped["after_observation_id"],
        "url": arguments["url"],
        "outcome": dumped["outcome"],
        "timestamp": dumped["started_at"],
        "completed_at": dumped["completed_at"],
        "expected_change_observed": dumped["expected_change_observed"],
        "worker_reasoning": arguments["reason"],
        "failure": dumped["failure"],
    }


def _outcome_failure(report: SubtaskReport, subtask_id: str) -> TypedError | None:
    if report.outcome == "needs_visual":
        handoff = report.visual_handoff
        question = handoff.question if handoff is not None else "visual interpretation"
        target = handoff.unresolved_target if handoff is not None else "the target"
        refs = [handoff.observation_id] if handoff is not None else []
        return TypedError(
            code="TARGET_NOT_FOUND",
            message=f"DOM worker could not resolve {target!r}; visual question: {question}",
            retryable=True,
            step_id=subtask_id,
            evidence_refs=refs,
        )
    if report.outcome == "inconclusive":
        failed = [check.check_id for check in report.validation if not check.passed]
        detail = f"; failed checks: {', '.join(failed)}" if failed else ""
        return TypedError(
            code="VALIDATION_FAILED",
            message=f"DOM worker result is inconclusive{detail}",
            retryable=True,
            step_id=subtask_id,
        )
    if report.outcome == "cancelled":
        return TypedError(
            code="CANCELLED",
            message="DOM worker subtask was cancelled",
            retryable=False,
            step_id=subtask_id,
        )
    return None


def ghost_summary(ghost: Any) -> dict[str, Any]:
    """Persisted, credential-free summary of the worker's Ghost decision.

    Says whether memory was reused or explored, whether a candidate was saved or
    why it was skipped, and which Ghost error codes occurred.  Only these fixed
    keys are copied.
    """
    if not isinstance(ghost, dict):
        return {}
    candidate = ghost.get("candidate")
    summary: dict[str, Any] = {"mode": ghost.get("mode")}
    if isinstance(candidate, dict):
        summary["candidate"] = {
            key: candidate.get(key) for key in ("skill_id", "version", "status")
        }
    for key in ("candidate_skipped", "workflow", "run_id", "visual_used"):
        if ghost.get(key) is not None:
            summary[key] = ghost.get(key)
    errors = ghost.get("errors")
    if errors:
        summary["errors"] = [str(code) for code in errors]
    return summary


def to_worker_report(
    report: SubtaskReport, subtask_input: SubtaskInput
) -> tuple[WorkerReport, dict[str, Any]]:
    """Convert the worker's ``SubtaskReport`` into an ARGUS report and its context.

    The ARGUS report echoes the lent ``request_id``, ``subtask_id`` and
    ``session_handle`` from ``subtask_input`` so the controller's intake accepts
    it.  The context carries the worker's validation checks, Ghost block,
    limitations, session disposition and final URL for the Ghost bridge.
    """
    task = subtask_input.subtask
    subtask_id = task.subtask_id
    outcome = OUTCOME_MAP.get(report.outcome, "failed")

    typed_failures = [
        _typed_failure(failure, subtask_id) for failure in report.failures
    ]
    extra = _outcome_failure(report, subtask_id)
    if extra is not None and all(
        existing.code != extra.code for existing in typed_failures
    ):
        typed_failures.append(extra)
    if outcome == "failed" and not typed_failures:
        typed_failures.append(
            TypedError(
                code=_DEFAULT_FAILURE_CODE,
                message="DOM worker reported failure without a failure code",
                retryable=True,
                step_id=subtask_id,
            )
        )

    metrics = report.metrics
    worker_report = WorkerReport(
        SCHEMA_VERSION,
        WORKER_NAME,
        report.reasoning_backend,
        subtask_input.request_id or subtask_input.run_id,
        subtask_id,
        task.goal or task.operation,
        outcome,
        report.summary,
        [_finding(record) for record in report.records],
        [_action(record) for record in report.action_trace],
        {
            "screenshots": list(report.evidence_refs),
            "observations": [
                observation.observation_id for observation in report.observations
            ],
            "final_url": report.final_url,
            "ghost": ghost_summary(report.ghost),
        },
        {
            "browser_action_count": int(metrics.actions),
            "model_call_count": int(metrics.model_calls),
            "elapsed_ms": int(metrics.elapsed_ms),
        },
        [failure.model_dump(mode="json") for failure in report.failures],
        subtask_input.session_handle,
        typed_failures,
    )
    context = {
        "validation": [check.model_dump(mode="json") for check in report.validation],
        "ghost": dict(report.ghost),
        "limitations": list(report.limitations),
        "session_disposition": report.session_disposition,
        "final_url": report.final_url,
    }
    return worker_report, context


def failed_report(subtask_input: SubtaskInput, error_type: str) -> WorkerReport:
    """A failed ARGUS report for an exception the worker did not turn into a report.

    ``error_type`` is the exception class name only; the message never carries
    provider or exception text.
    """
    task = subtask_input.subtask
    failure = TypedError(
        _DEFAULT_FAILURE_CODE,
        f"DOM worker subtask failed ({error_type}).",
        True,
        task.subtask_id,
        [],
    )
    return WorkerReport(
        SCHEMA_VERSION,
        WORKER_NAME,
        "unknown",
        subtask_input.request_id or subtask_input.run_id,
        task.subtask_id,
        task.goal or task.operation,
        "failed",
        "The DOM worker subtask did not complete.",
        [],
        [],
        {"screenshots": [], "observations": [], "final_url": None},
        {"browser_action_count": 0, "elapsed_ms": 0, "model_call_count": 0},
        [],
        subtask_input.session_handle,
        [failure],
    )
