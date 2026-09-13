"""DOM-first workflow memory with a single optional visual handoff."""

import asyncio
import math
import os
import threading
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from ghostapi.client import GhostError

from .ghost_adapter import (
    NotCompilable,
    ReplayReasoner,
    candidate_request,
    lookup_request,
    run_report,
)
from .policy import validate_request
from .schemas import Failure, SessionSpec, SubtaskReport, WorkerError
from .schemas import FailureCode as C

_sessions, _session_lock = set(), threading.Lock()


@asynccontextmanager
async def session_lease(session_ref):
    if session_ref:
        with _session_lock:
            if session_ref in _sessions:
                raise WorkerError(
                    C.SESSION_UNAVAILABLE, "Another worker is controlling this session."
                )
            _sessions.add(session_ref)
    try:
        yield
    finally:
        if session_ref:
            with _session_lock:
                _sessions.discard(session_ref)


class GhostWorkflow:
    def __init__(
        self, worker, client, *, visual_runner=None, session_factory=None, agent_id="browser-worker"
    ):
        self.worker, self.client = worker, client
        self.visual_runner, self.session_factory = visual_runner, session_factory
        self.agent_id = agent_id

    async def replay(self, task, site, workflow, cancel=None, on_status=None):
        from .runner import Worker

        policy = ReplayReasoner(workflow, task, site)
        worker = Worker(
            sites=self.worker.sites,
            settings=self.worker.settings,
            browser_factory=self.worker.browser_factory,
            reasoner_factory=lambda settings: policy,
        )
        return await worker._run(task, cancel, on_status)

    @asynccontextmanager
    async def shared_session(self, task, cleanup=None):
        cleanup = cleanup if cleanup is not None else {}
        if (
            task.session.ownership == "argus"
            or not task.visual_fallback_available
            or self.worker.settings.browser != "steel"
        ):
            # Delay explicitly transferred cleanup until visual work has also finished.
            if task.session.close_on_finish and task.visual_fallback_available:
                from steel import AsyncSteel

                steel = AsyncSteel(
                    steel_api_key=os.environ.get("STEEL_API_KEY"), max_retries=0, timeout=15
                )
                try:
                    session = task.session.model_copy(update={"close_on_finish": False})
                    yield task.model_copy(update={"session": session}), False
                finally:
                    try:
                        await steel.sessions.release(task.session.session_ref)
                        cleanup.update(released=True, session_ref=task.session.session_ref)
                    finally:
                        await steel.close()
            else:
                yield task, task.session.ownership == "worker"
            return
        from steel import AsyncSteel

        if not os.environ.get("STEEL_API_KEY") and self.session_factory is None:
            raise WorkerError(C.SESSION_UNAVAILABLE, "STEEL_API_KEY is not configured.")
        steel = (
            self.session_factory()
            if self.session_factory
            else AsyncSteel(max_retries=0, timeout=15)
        )
        session_id = str(uuid4())
        try:
            await steel.sessions.create(
                session_id=session_id, api_timeout=(task.budgets.max_runtime_seconds + 30) * 1000
            )
            session = SessionSpec(ownership="argus", session_ref=session_id)
            # The outer workflow owns cleanup. Both execution paths only disconnect.
            yield task.model_copy(update={"session": session}), True
        finally:
            try:
                await steel.sessions.release(session_id)
                cleanup.update(released=True, session_ref=session_id)
            finally:
                await steel.close()

    @staticmethod
    def needs_visual(report):
        if report.outcome == "needs_visual":
            return True
        codes = {f.code for f in report.failures}
        return (
            report.outcome in {"failed", "inconclusive"}
            and any(o.signals.get("canvas") for o in report.observations)
            and codes
            <= {C.VALIDATION_FAILED, C.TARGET_NOT_FOUND, C.TARGET_AMBIGUOUS, C.PRECONDITION_FAILED}
        )

    async def run(self, raw, cancel=None, on_status=None):
        task, site = validate_request(raw, self.worker.sites)
        cancel = cancel or asyncio.Event()
        started = time.monotonic()
        info = {"mode": "exploration", "candidate": None, "errors": [], "visual_used": False}
        workflow, report, replay_report = None, None, None
        cleanup = {}

        def remaining_task(current, report=None):
            seconds = task.budgets.max_runtime_seconds - (time.monotonic() - started)
            actions = task.budgets.max_actions - (report.metrics.actions if report else 0)
            calls = task.budgets.max_model_calls - (report.metrics.model_calls if report else 0)
            if seconds <= 0 or actions <= 0 or calls <= 0 or cancel.is_set():
                raise WorkerError(
                    C.CANCELLED if cancel.is_set() else C.BUDGET_EXHAUSTED,
                    "No remaining budget for execution.",
                )
            return current.model_copy(
                update={
                    "budgets": current.budgets.model_copy(
                        update={
                            "max_runtime_seconds": max(1, math.floor(seconds)),
                            "max_actions": actions,
                            "max_model_calls": calls,
                        }
                    )
                }
            )

        try:
            if cancel.is_set():
                raise WorkerError(C.CANCELLED, "Cancellation requested before lookup.")
            if on_status:
                on_status("ghost_lookup")
            try:
                from .runner import interruptible

                decision = await interruptible(
                    self.client.lookup(lookup_request(task, site, self.agent_id)),
                    cancel,
                    min(5, task.budgets.max_runtime_seconds),
                )
                workflow = decision.get("workflow") if decision["decision"] == "reuse" else None
            except GhostError as exc:
                info["errors"].append(exc.code)
            except TimeoutError:
                info["errors"].append("GHOST_UNAVAILABLE")
            async with (
                session_lease(task.session.session_ref),
                self.shared_session(remaining_task(task), cleanup) as (active, owned),
            ):
                async with session_lease(
                    active.session.session_ref
                    if active.session.session_ref != task.session.session_ref
                    else None
                ):
                    active = remaining_task(active)
                    if workflow:
                        info.update(
                            mode="reuse", workflow={k: workflow[k] for k in ("skill_id", "version")}
                        )
                        report = await self.replay(active, site, workflow, cancel, on_status)
                    else:
                        report = await self.worker._run(active, cancel, on_status)
                    if (
                        self.needs_visual(report)
                        and task.visual_fallback_available
                        and report.session_disposition == "retained"
                    ):
                        if workflow:
                            replay_report = report.model_copy(deep=True)
                            info["mode"] = "fallback_visual"
                        visual_task = remaining_task(active, report).model_copy(
                            update={"start_url": None}
                        )
                        info["visual_used"] = True
                        if on_status:
                            on_status("visual_handoff")
                        runner = self.visual_runner
                        if runner is None:
                            from Agents.visual.browser_subagent.ghost_adapter import (
                                run_visual_handoff,
                            )

                            runner = run_visual_handoff
                        report = await runner(visual_task, site, report, cancel)
            if (
                (owned or task.session.close_on_finish)
                and report.session_ref
                and report.session_disposition != "cleanup_failed"
            ):
                report.session_disposition = "released"
        except WorkerError as exc:
            report = report or SubtaskReport(
                request_id=task.request_id, run_id=task.run_id, subtask_id=task.subtask_id
            )
            report.outcome = "cancelled" if exc.failure.code == C.CANCELLED else "failed"
            report.summary = exc.failure.message
            report.failures.append(exc.failure)
        except asyncio.CancelledError:
            raise
        except Exception:
            report = report or SubtaskReport(
                request_id=task.request_id, run_id=task.run_id, subtask_id=task.subtask_id
            )
            report.outcome = "failed"
            report.summary = "Worker connection or session cleanup failed."
            report.session_disposition = "cleanup_failed"
            report.failures.append(Failure(code=C.SESSION_UNAVAILABLE, message=report.summary))
        report.metrics.elapsed_ms = int((time.monotonic() - started) * 1000)
        if cleanup.get("released"):
            report.session_ref = cleanup["session_ref"]
            report.session_disposition = "released"
            if replay_report:
                replay_report.session_disposition = "released"
        if workflow:
            try:
                recorded = await self.client.report_run(
                    workflow["skill_id"],
                    workflow["version"],
                    run_report(task, replay_report or report, self.agent_id),
                )
                info["run_id"] = recorded["run_id"]
            except GhostError as exc:
                info["errors"].append(exc.code)
        elif report.outcome == "succeeded":
            try:
                body = candidate_request(
                    task, site, report, self.agent_id, "visual" if info["visual_used"] else "dom"
                )
                info["candidate"] = await self.client.create_candidate(body)
            except NotCompilable as exc:
                info["candidate_skipped"] = str(exc)
            except GhostError as exc:
                info["errors"].append(exc.code)
        report.ghost = info
        return report

    async def qualify(self, raw, skill_id, version, input_sets):
        """Explicit qualification; never run paid replays as a side effect of exploration."""
        from ghostapi.api.service import bind

        task, site = validate_request(raw, self.worker.sites)
        if len(input_sets) != 3 or len({str(sorted(p.items())) for p in input_sets}) != 3:
            raise ValueError(
                "Supply three distinct changed input sets, including an empty-result case."
            )
        saved = await self.client.get_workflow(skill_id, version)
        definition = saved["definition"]
        reports, executions = [], []
        for parameters in input_sets:
            if parameters == definition.get("parameters"):
                raise ValueError("Qualification inputs must differ from exploration inputs.")
            replay_task = task.model_copy(
                update={
                    "parameters": parameters,
                    "run_id": str(uuid4()),
                    "session": SessionSpec(ownership="worker"),
                    "visual_fallback_available": False,
                }
            )
            replay_task, _ = validate_request(
                replay_task.model_dump(mode="json"), self.worker.sites
            )
            workflow = {
                k: definition[k] for k in ("compatibility_key", "output_schema_id", "preconditions")
            }
            workflow["bound_steps"] = bind(definition, parameters)
            execution = await self.replay(replay_task, site, workflow)
            stored = await self.client.report_run(
                skill_id,
                version,
                run_report(replay_task, execution, self.agent_id, kind="qualification"),
            )
            reports.append(
                {
                    "parameters": parameters,
                    "validation_status": "passed" if execution.outcome == "succeeded" else "failed",
                    "empty_result": execution.outcome == "succeeded" and not execution.records,
                    "evidence_refs": execution.evidence_refs or ["missing-evidence"],
                    "run_id": stored["run_id"],
                }
            )
            executions.append(execution)
        outcome = await self.client.submit_qualification(
            skill_id,
            version,
            {"schema_version": "0.2", "agent_id": self.agent_id, "reports": reports},
        )
        return {"qualification": outcome, "runs": [r.model_dump(mode="json") for r in executions]}
