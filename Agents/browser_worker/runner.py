"""Transport-neutral bounded observe/decide/validate/act/verify loop."""

import asyncio
import inspect
import logging
import os
import time
from collections import Counter
from dataclasses import dataclass, field

from .browser.adapter import BrowserAdapter
from .config import Settings, load_sites
from .llm import OpenAIReasoner
from .policy import validate_action, validate_request
from .schemas import (
    Action,
    ActionRecord,
    Decision,
    SubtaskReport,
    VisualHandoff,
    WorkerError,
    utcnow,
)
from .schemas import (
    FailureCode as C,
)
from .verifier import expected_change, extract, verify

logger = logging.getLogger("argus.browser_worker")


@dataclass
class WorkerState:
    report: SubtaskReport
    status: str = "received"
    started: float = field(default_factory=time.monotonic)
    observation: object = None
    extracted_hash: str | None = None
    no_progress: int = 0
    repeated: Counter = field(default_factory=Counter)


async def interruptible(awaitable, cancel: asyncio.Event, timeout: float):
    operation = asyncio.ensure_future(awaitable)
    cancelled = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {operation, cancelled}, timeout=max(0, timeout), return_when=asyncio.FIRST_COMPLETED
        )
        if cancelled in done:
            raise WorkerError(C.CANCELLED, "Cancellation requested.")
        if operation not in done:
            raise TimeoutError()
        return await operation
    finally:
        for future in (operation, cancelled):
            if not future.done():
                future.cancel()
        await asyncio.gather(operation, cancelled, return_exceptions=True)


class Worker:
    def __init__(self, sites=None, settings=None, browser_factory=None, reasoner_factory=None):
        self.sites = sites if sites is not None else load_sites()
        self.settings = settings or Settings.from_env()
        self.browser_factory = browser_factory or BrowserAdapter
        self.reasoner_factory = reasoner_factory or OpenAIReasoner

    async def run(self, raw, cancel: asyncio.Event | None = None, on_status=None) -> SubtaskReport:
        if os.environ.get("GHOST_API_URL"):
            from ghostapi.client import GhostClient

            from .ghost import GhostWorkflow

            async with GhostClient() as client:
                return await GhostWorkflow(self, client).run(raw, cancel, on_status)
        return await self._run(raw, cancel, on_status)

    async def _run(self, raw, cancel: asyncio.Event | None = None, on_status=None) -> SubtaskReport:
        # Invalid orchestration messages are rejected before constructing either adapter.
        task, site = validate_request(raw, self.sites)
        report = SubtaskReport(
            request_id=task.request_id, run_id=task.run_id, subtask_id=task.subtask_id
        )
        state = WorkerState(report)
        cancel = cancel or asyncio.Event()
        browser = self.browser_factory(task, site, self.settings)
        model = self.reasoner_factory(self.settings)
        report.browser_backend = self.settings.browser
        report.reasoning_backend = getattr(model, "model_id", type(model).__name__)
        replay = getattr(model, "is_replay", False)

        def stage(name):
            state.status = name
            if on_status:
                on_status(name)
            # Operational logs contain no page text, arguments, credentials, or exceptions.
            logger.info(
                "worker_stage",
                extra={
                    "execution_id": report.execution_id,
                    "stage": name,
                    "step": report.metrics.actions,
                },
            )

        def remaining():
            return task.budgets.max_runtime_seconds - (time.monotonic() - state.started)

        async def call(awaitable, timeout=None, timeout_code=C.BUDGET_EXHAUSTED):
            remaining_seconds = remaining()
            limited_by_runtime = timeout is None or remaining_seconds <= timeout
            try:
                return await interruptible(
                    awaitable, cancel, min(remaining_seconds, timeout or remaining_seconds)
                )
            except TimeoutError:
                code = C.BUDGET_EXHAUSTED if limited_by_runtime else timeout_code
                raise WorkerError(
                    code,
                    "Runtime budget exhausted." if limited_by_runtime else "Operation timed out.",
                    not limited_by_runtime,
                ) from None

        async def observe():
            stage("observing")
            obs = await call(
                browser.observe(report.execution_id),
                self.settings.browser_timeout_seconds,
                C.PRECONDITION_FAILED,
            )
            state.observation = obs
            report.observations.append(obs)
            report.evidence_refs.append(obs.observation_id)
            report.final_url = obs.url
            if obs.signals.get("auth_required"):
                raise WorkerError(C.AUTH_REQUIRED, "Page requires authentication.")
            return obs

        def failure(exc):
            item = exc.failure.model_copy(deep=True)
            if state.observation:
                item.evidence_refs = [state.observation.observation_id]
            report.failures.append(item)
            return item

        try:
            if cancel.is_set():
                raise WorkerError(C.CANCELLED, "Cancellation requested before initialization.")
            stage("initializing")
            await call(browser.start())
            report.session_ref = browser.session_ref
            await observe()
            initial = state.observation
            if (
                not replay
                and task.visual_fallback_available
                and initial.signals.get("canvas")
                and not initial.signals.get("record_refs")
                and not initial.signals.get("results_ready")
            ):
                report.outcome = "needs_visual"
                report.summary = "The page draws content in canvas without readable DOM results."
                report.visual_handoff = VisualHandoff(
                    run_id=task.run_id,
                    subtask_id=task.subtask_id,
                    url=initial.url,
                    observation_id=initial.observation_id,
                    intended_operation=task.operation,
                    unresolved_target="canvas content",
                    candidates=[],
                    attempted_step_ids=[],
                    reason_code="INSUFFICIENT_DOM",
                    remaining_budget={
                        "actions": task.budgets.max_actions,
                        "model_calls": task.budgets.max_model_calls,
                        "seconds": remaining(),
                    },
                    question=task.objective,
                    available=True,
                )
                report.action_trace.append(
                    ActionRecord(
                        action_type="request_visual_fallback",
                        before_observation_id=initial.observation_id,
                        after_observation_id=initial.observation_id,
                        outcome="succeeded",
                        completed_at=utcnow(),
                        arguments=Action(
                            observation_id=initial.observation_id,
                            target_ref=None,
                            semantic_target="canvas content",
                            value=None,
                            value_origin=None,
                            url=None,
                            expected_change="unchanged",
                            reason=report.summary,
                            records=[],
                            failure_code="INSUFFICIENT_DOM",
                            visual_question=task.objective,
                        ),
                    )
                )
                return report
            while True:
                if cancel.is_set():
                    raise WorkerError(C.CANCELLED, "Cancellation requested.")
                if remaining() <= 0 or (
                    not replay and report.metrics.model_calls >= task.budgets.max_model_calls
                ):
                    raise WorkerError(C.BUDGET_EXHAUSTED, "Runtime or model-call budget exhausted.")
                obs = state.observation
                trace = None
                try:
                    stage("reasoning")
                    if not replay:
                        report.metrics.model_calls += 1
                    context = {
                        "task": task.model_dump(mode="json", exclude={"session"}),
                        "output_schema": site.record_schema,
                        "mandatory_conditions": [c.model_dump() for c in site.mandatory_conditions],
                        "approved_literals": site.approved_literals,
                        "observation": obs.model_dump(mode="json"),
                        "remaining_budget": {
                            "actions": task.budgets.max_actions - report.metrics.actions,
                            "model_calls": task.budgets.max_model_calls
                            - report.metrics.model_calls,
                            "retries": task.budgets.max_retries - report.metrics.retries,
                            "seconds": round(remaining(), 2),
                        },
                        "recent_actions": [
                            a.model_dump(mode="json") for a in report.action_trace[-4:]
                        ],
                        "recent_errors": [f.model_dump(mode="json") for f in report.failures[-3:]],
                        "extracted_records": [r.model_dump(mode="json") for r in report.records],
                    }
                    proposed = await call(
                        model.decide(context), self.settings.model_timeout_seconds, C.MODEL_TIMEOUT
                    )
                    try:
                        decision = Decision.model_validate(proposed)
                    except ValueError:
                        raise WorkerError(C.MODEL_ERROR, "Malformed tool call.", True) from None
                    trace = ActionRecord(
                        action_type=decision.action_type,
                        arguments=decision.arguments,
                        before_observation_id=obs.observation_id,
                    )
                    report.action_trace.append(trace)
                    validate_action(decision, task, site, obs)
                    await call(
                        browser.assert_fresh(obs),
                        self.settings.browser_timeout_seconds,
                        C.PRECONDITION_FAILED,
                    )
                    kind, a = decision.action_type, decision.arguments
                    signature = (obs.content_hash, kind, a.semantic_target, a.value, a.url)
                    state.repeated[signature] += 1
                    if state.repeated[signature] >= task.budgets.no_progress_limit:
                        raise WorkerError(
                            C.NO_PROGRESS, "Repeated operation without new page evidence."
                        )
                    if kind == "report_success":
                        stage("verifying")
                        final = await observe()
                        report.validation = verify(task, site, report, final, state.extracted_hash)
                        report.outcome = (
                            "succeeded"
                            if all(c.passed for c in report.validation)
                            else "inconclusive"
                        )
                        report.summary = (
                            f"Verified {len(report.records)} records from the observed results."
                            if report.outcome == "succeeded"
                            else "The observed result did not pass all independent checks."
                        )
                        trace.outcome = "succeeded" if report.outcome == "succeeded" else "failed"
                        trace.after_observation_id = final.observation_id
                        if report.outcome == "inconclusive":
                            trace.failure = failure(
                                WorkerError(C.VALIDATION_FAILED, report.summary)
                            )
                        break
                    if kind == "report_failure":
                        code = (
                            C(a.failure_code)
                            if a.failure_code
                            in {
                                C.PRECONDITION_FAILED,
                                C.TARGET_NOT_FOUND,
                                C.TARGET_AMBIGUOUS,
                                C.EXTRACTION_FAILED,
                                C.AUTH_REQUIRED,
                                C.UNSUPPORTED_OPERATION,
                            }
                            else C.PRECONDITION_FAILED
                        )
                        raise WorkerError(code, "Model reported that the task cannot continue.")
                    if kind == "request_visual_fallback":
                        report.outcome = "needs_visual"
                        report.summary = (
                            "DOM evidence is insufficient; visual interpretation is required."
                        )
                        report.visual_handoff = VisualHandoff(
                            run_id=task.run_id,
                            subtask_id=task.subtask_id,
                            url=obs.url,
                            observation_id=obs.observation_id,
                            intended_operation=a.reason,
                            unresolved_target=a.semantic_target,
                            candidates=[e.ref for e in obs.elements if e.name == a.semantic_target][
                                :20
                            ],
                            attempted_step_ids=[t.step_id for t in report.action_trace[:-1]],
                            reason_code=a.failure_code or "INSUFFICIENT_DOM",
                            remaining_budget=context["remaining_budget"],
                            question=a.visual_question or a.reason,
                            available=task.visual_fallback_available,
                        )
                        trace.outcome = "succeeded"
                        break
                    if report.metrics.actions >= task.budgets.max_actions:
                        raise WorkerError(C.BUDGET_EXHAUSTED, "Action budget exhausted.")
                    report.metrics.actions += 1
                    trace.outcome = "failed"  # Now attempted; later errors are execution failures.
                    if kind == "extract_records":
                        stage("extracting")
                        report.records = extract(a, obs, task, site)
                        state.extracted_hash = obs.content_hash
                        trace.after_observation_id = obs.observation_id
                        trace.outcome = "succeeded"
                        trace.execution_result = {"records_extracted": len(report.records)}
                        state.no_progress = 0
                    else:
                        stage("acting")
                        trace.execution_result = await call(
                            browser.execute(decision, obs),
                            self.settings.browser_timeout_seconds,
                            C.PRECONDITION_FAILED,
                        )
                        await call(
                            browser.settle(decision, obs),
                            self.settings.browser_timeout_seconds,
                            C.PRECONDITION_FAILED,
                        )
                        after = await observe()
                        trace.after_observation_id = after.observation_id
                        trace.expected_change_observed = expected_change(decision, obs, after)
                        if replay and not trace.expected_change_observed:
                            raise WorkerError(
                                C.PRECONDITION_FAILED,
                                "Replay step did not reach its expected state.",
                            )
                        trace.outcome = "succeeded"
                        state.no_progress = (
                            state.no_progress + 1 if after.content_hash == obs.content_hash else 0
                        )
                        if a.value_origin:
                            report.parameter_origins[trace.step_id] = a.value_origin
                        if state.no_progress >= task.budgets.no_progress_limit:
                            raise WorkerError(
                                C.NO_PROGRESS, "Repeated actions made no observed progress."
                            )
                except WorkerError as exc:
                    item = failure(exc)
                    if trace:
                        trace.failure = item
                        trace.outcome = (
                            "failed"
                            if trace.outcome == "failed" or trace.after_observation_id
                            else "rejected"
                        )
                    if replay or not item.retryable:
                        raise
                    if report.metrics.retries >= task.budgets.max_retries:
                        raise WorkerError(C.BUDGET_EXHAUSTED, "Retry budget exhausted.")
                    report.metrics.retries += 1
                    recovered = await observe()
                    if trace:
                        trace.after_observation_id = recovered.observation_id
                finally:
                    if trace:
                        trace.completed_at = utcnow()
        except WorkerError as exc:
            if not report.failures or report.failures[-1].code != exc.failure.code:
                failure(exc)
            report.outcome = "cancelled" if exc.failure.code == C.CANCELLED else "failed"
            report.summary = exc.failure.message
        except asyncio.CancelledError:
            failure(WorkerError(C.CANCELLED, "Calling task was cancelled."))
            report.outcome = "cancelled"
            report.summary = "Execution cancelled."
        except Exception:
            failure(
                WorkerError(C.INTERNAL_ERROR, "Unexpected worker error; no success was accepted.")
            )
            report.outcome = "failed"
        finally:
            report.session_ref = browser.session_ref
            # Cleanup has its own small allowance even after cancellation/runtime exhaustion.
            try:
                report.session_disposition = await asyncio.wait_for(browser.close(), timeout=15)
            except Exception:
                report.session_disposition = "cleanup_failed"
            if report.session_disposition == "cleanup_failed":
                failure(
                    WorkerError(
                        C.SESSION_UNAVAILABLE, "Session cleanup failed; owner must release it."
                    )
                )
                report.limitations.append("Browser cleanup requires operator attention.")
            try:
                close = getattr(model, "close", None)
                if close:
                    value = close()
                    if inspect.isawaitable(value):
                        await asyncio.wait_for(value, timeout=5)
            except Exception:
                report.limitations.append("Model client cleanup failed.")
            report.metrics.elapsed_ms = int((time.monotonic() - state.started) * 1000)
            report.metrics.input_tokens = getattr(model, "input_tokens", None)
            report.metrics.output_tokens = getattr(model, "output_tokens", None)
            coverage = (
                "Coverage is limited to model-identified records visible in the final DOM; "
                "cross-page completeness is unverified."
                if site.open_site
                else "Coverage is limited to configured records visible in the final DOM; "
                "cross-page completeness is unverified."
            )
            report.limitations.extend(
                [
                    coverage,
                    "Visual interpretation is a handoff only; this worker does not execute "
                    "coordinates or qualify Ghost skills.",
                ]
            )
            for o in report.observations:
                for limitation in o.limitations:
                    if limitation not in report.limitations:
                        report.limitations.append(limitation)
            stage(report.outcome)
        return report
