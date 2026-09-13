"""Continue a DOM handoff using the existing UI-TARS screenshot/action loop."""

import asyncio
import base64
import hashlib
import json
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from Agents.browser_worker.schemas import Failure
from Agents.browser_worker.schemas import FailureCode as C

from .uitars_agent import UITarsSubagent
from .worker_report import build_worker_report


def normalize_visual_trace(visual, log_dir):
    """Keep all browser-changing actions, or reject the entire candidate."""
    trace, artifacts = [], {}
    total = 0
    root = Path(log_dir).resolve()
    for step in visual["actions"]:
        raw_name = step["action"].get("name")
        canonical = step.get("ghost_action")
        if raw_name == "finished":
            continue
        if step["outcome"] != "succeeded" or canonical is None:
            raise ValueError("Visual trace contains an unsupported or failed action.")
        if canonical.get("inspection"):
            continue
        target = canonical.get("target")
        if canonical["action"] in {"fill", "select", "click"} and (
            not target or not target.get("role") or not target.get("label")
        ):
            raise ValueError("Visual target has no stable semantic identity.")
        refs = []
        for name in ("observation_before", "observation_after"):
            if not step.get(name):
                raise ValueError("Visual action is missing screenshot evidence.")
            path = Path(step[name]).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Screenshot is outside the run artifact directory.")
            data = path.read_bytes()
            total += len(data)
            if total > 12 * 1024 * 1024:
                raise ValueError(
                    "Visual evidence exceeds the candidate artifact limit."
                )
            ref = "screenshot:" + hashlib.sha256(data + str(path).encode()).hexdigest()
            artifacts[ref] = {
                "media_type": "image/png",
                "sha256": hashlib.sha256(data).hexdigest(),
                "base64": base64.b64encode(data).decode(),
            }
            refs.append(ref)
        trace.append(
            {
                **canonical,
                "step_id": "visual-" + step["step_id"],
                "observation_before": refs[0],
                "observation_after": refs[1],
                "outcome": "succeeded",
                "timestamp": datetime.fromtimestamp(
                    step["timestamp"], timezone.utc
                ).isoformat(),
            }
        )
    return trace, artifacts


async def validate_visual_result(task, site, report):
    """Independently extract configured DOM records after the visual model has finished."""
    from Agents.browser_worker.browser.adapter import BrowserAdapter
    from Agents.browser_worker.config import Settings
    from Agents.browser_worker.ghost_adapter import ReplayReasoner, compatibility_key
    from Agents.browser_worker.schemas import ActionRecord, utcnow
    from Agents.browser_worker.verifier import extract, verify

    if not site.extraction_fields:
        return report
    browser = BrowserAdapter(task, site, Settings.from_env().model_copy(update={"browser": "steel"}))
    try:
        await browser.start()
        observation = await browser.observe(report.execution_id)
        workflow = {
            "compatibility_key": compatibility_key(task, site),
            "output_schema_id": task.output_schema_id,
            "preconditions": ["start_url=" + observation.url],
            "bound_steps": [
                {
                    "action": "extract",
                    "expected_state": {"fields": site.extraction_fields},
                }
            ],
        }
        recipe = ReplayReasoner(workflow, task, site)
        decision = await recipe.decide(
            {"observation": observation.model_dump(mode="json")}
        )
        report.records = extract(decision.arguments, observation, task, site)
        report.observations.append(observation)
        report.evidence_refs.append(observation.observation_id)
        final = await browser.observe(report.execution_id)
        report.observations.append(final)
        report.evidence_refs.append(final.observation_id)
        report.action_trace.append(
            ActionRecord(
                action_type="extract_records",
                arguments=decision.arguments,
                before_observation_id=observation.observation_id,
                after_observation_id=observation.observation_id,
                completed_at=utcnow(),
                outcome="succeeded",
            )
        )
        report.metrics.actions += 1
        report.validation = verify(task, site, report, final, observation.content_hash)
        if all(check.passed for check in report.validation):
            report.outcome = "succeeded"
            report.summary = f"Visual worker completed the task; independently verified {len(report.records)} records."
        report.final_url = final.url
    finally:
        report.session_disposition = await browser.close()
    return report


async def run_visual_handoff(
    task, site, dom_report, cancel, *, agent_factory=UITarsSubagent
):
    if not task.session.session_ref or dom_report.session_disposition != "retained":
        raise ValueError("Visual continuation requires a retained Steel session.")
    stopped = threading.Event()
    started = time.monotonic()
    log_dir = tempfile.mkdtemp(prefix="ghost-visual-")

    def execute():
        agent = agent_factory(
            max_steps=max(
                1, min(task.budgets.max_actions - 1, task.budgets.max_model_calls)
            ),
            log_dir=log_dir,
        )
        # A single model call cannot outlive the remaining run budget by 300 seconds.
        agent._http.close()
        agent._http = httpx.Client(timeout=min(30, task.budgets.max_runtime_seconds))
        agent.reader_url = None  # One model-call budget; no uncounted secondary reader calls.
        try:
            result = agent.run(
                task.objective
                + "\nStructured task inputs: "
                + json.dumps(task.parameters),
                session_ref=task.session.session_ref,
                task=task,
                site=site,
                cancel_event=stopped,
                max_runtime_seconds=task.budgets.max_runtime_seconds,
            )
            return result, build_worker_report(
                result, log_dir, task.objective, task.request_id, task.subtask_id
            )
        finally:
            agent._http.close()

    work = asyncio.create_task(asyncio.to_thread(execute))
    cancelling = asyncio.create_task(cancel.wait())
    try:
        done, _ = await asyncio.wait(
            {work, cancelling},
            return_when=asyncio.FIRST_COMPLETED,
            timeout=task.budgets.max_runtime_seconds,
        )
        if work not in done:
            stopped.set()
        # Join the bounded worker before allowing another controller or releasing Steel.
        result, visual = await asyncio.shield(work)
    except asyncio.CancelledError:
        stopped.set()
        try:
            await asyncio.shield(work)
        finally:
            raise
    finally:
        cancelling.cancel()
        await asyncio.gather(cancelling, return_exceptions=True)
    report = dom_report.model_copy(deep=True)
    report.visual_report = visual
    report.reasoning_backend += "+uitars"
    report.summary = result.get("summary") or "Visual worker returned no answer."
    report.session_disposition = result.get("session_disposition", "cleanup_failed")
    report.metrics.actions += (result.get("metrics") or {}).get(
        "browser_action_count", 0
    )
    report.metrics.model_calls += (result.get("metrics") or {}).get(
        "model_call_count", 0
    )
    report.metrics.input_tokens = report.metrics.output_tokens = None
    report.records, report.validation = [], []
    report.outcome = (
        "cancelled"
        if cancel.is_set()
        else "inconclusive"
        if result.get("success")
        else "failed"
    )
    if report.outcome == "inconclusive":
        try:
            # The ordinary success claim is insufficient; read fresh sources independently.
            left = task.budgets.max_runtime_seconds - (time.monotonic() - started)
            if left <= 0 or cancel.is_set():
                raise TimeoutError("No budget remains for visual validation.")
            report = await asyncio.wait_for(
                validate_visual_result(task, site, report), timeout=min(20, left)
            )
            if report.outcome == "succeeded":
                trace, artifacts = normalize_visual_trace(visual, log_dir)
                report.visual_report.update(
                    ghost_trace=trace, ghost_artifacts=artifacts
                )
        except Exception:
            report.limitations.append(
                "Visual validation or semantic trace compilation did not complete."
            )
        if report.outcome != "succeeded":
            report.limitations.append(
                "Visual answer is backed by screenshots but lacks independent structured-result validation; no Ghost candidate is saved."
            )
    elif report.outcome == "cancelled":
        report.failures.append(
            Failure(code=C.CANCELLED, message="Visual execution cancelled.")
        )
    else:
        report.failures.append(
            Failure(
                code=C.PRECONDITION_FAILED,
                message="Visual worker could not finish the task.",
            )
        )
    return report
