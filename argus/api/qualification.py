"""Bounded Ghost qualification jobs for Mission Control (P3-LEARN)."""

from __future__ import annotations

import asyncio
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, model_validator

from argus.adapters.worker_contracts import to_subtask_request
from argus.contracts import Budget, ContractError, Subtask, SubtaskInput

router = APIRouter()

_jobs_initialization_lock = threading.Lock()
_MAX_PROXY_BYTES = 1_000_000


class QualificationRequest(BaseModel):
    """Three changed parameter sets and the exploration run that produced them."""

    inputs: list[dict[str, Any]] = Field(min_length=3, max_length=3)
    run_id: str = Field(min_length=1, max_length=128)

    @model_validator(mode="after")
    def distinct_inputs(self):
        fingerprints = {_canonical(item) for item in self.inputs}
        if len(fingerprints) != 3:
            raise ValueError("qualification requires exactly three distinct input sets")
        return self


class _JobStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()

    def create(self, job_id: str) -> None:
        with self.lock:
            self.items[job_id] = {
                "job_id": job_id,
                "status": "running",
                "result": None,
                "runs": [],
                "error": None,
            }

    def finish(self, job_id: str, result: Mapping[str, Any]) -> None:
        with self.lock:
            self.items[job_id].update(
                status="succeeded",
                result=result.get("qualification"),
                runs=_run_summaries(result.get("runs")),
            )

    def fail(self, job_id: str, error: BaseException) -> None:
        with self.lock:
            self.items[job_id].update(status="failed", error=type(error).__name__)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            item = self.items.get(job_id)
            return dict(item) if item is not None else None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _jobs(app: Any) -> _JobStore:
    store = getattr(app.state, "qualification_jobs", None)
    if store is not None:
        return store
    with _jobs_initialization_lock:
        store = getattr(app.state, "qualification_jobs", None)
        if store is None:
            store = _JobStore()
            app.state.qualification_jobs = store
    return store


def _dependency(app: Any, name: str, default: Callable[..., Any]) -> Callable[..., Any]:
    return getattr(app.state, name, default)


def _load_exploration(app: Any, run_id: str) -> tuple[dict[str, Any], Subtask]:
    service = app.state.run_service
    try:
        run = service.store.run(run_id)
    except (KeyError, OSError, ValueError):
        raise HTTPException(404, "exploration run not found") from None
    try:
        raw_subtask = run["plan"]["subtasks"][0]
        subtask = Subtask.from_dict(raw_subtask)
    except (KeyError, IndexError, TypeError, ContractError):
        raise HTTPException(
            422, "exploration run has no usable first subtask"
        ) from None
    return run, subtask


def _request_template(
    app: Any, run: Mapping[str, Any], subtask: Subtask
) -> dict[str, Any]:
    from Agents.browser_worker.config import load_sites

    sites_loader = _dependency(app, "qualification_sites_loader", load_sites)
    sites = sites_loader()
    request_id = (
        (run.get("interpreted") or {}).get("request_id")
        if isinstance(run.get("interpreted"), Mapping)
        else None
    )
    subtask_input = SubtaskInput(
        run_id=str(run["run_id"]),
        subtask=subtask,
        session_handle="worker-owned-qualification",
        budget=Budget(30, 120.0),
        mode="explore",
        bound_procedure=None,
        request_id=request_id or str(run["run_id"]),
    )
    result = to_subtask_request(subtask_input, sites).model_dump(mode="json")
    result["visual_fallback_available"] = False
    return result


async def _qualify(
    app: Any,
    raw: dict[str, Any],
    skill_id: str,
    version: int,
    inputs: list[dict[str, Any]],
) -> Mapping[str, Any]:
    from Agents.browser_worker.config import Settings, load_sites
    from Agents.browser_worker.ghost import GhostWorkflow
    from Agents.browser_worker.runner import Worker
    from ghostapi.client import GhostClient

    sites_loader = _dependency(app, "qualification_sites_loader", load_sites)
    settings_factory = _dependency(
        app, "qualification_settings_factory", Settings.from_env
    )
    worker_factory = _dependency(app, "qualification_worker_factory", Worker)
    workflow_factory = _dependency(app, "qualification_workflow_factory", GhostWorkflow)
    client_factory = _dependency(app, "qualification_client_factory", GhostClient)

    sites = sites_loader()
    settings = settings_factory()
    worker = worker_factory(sites=sites, settings=settings)
    client = client_factory(os.environ["GHOST_API_URL"])
    try:
        workflow = workflow_factory(worker, client)
        result = await workflow.qualify(raw, skill_id, version, inputs)
        if not isinstance(result, Mapping):
            raise TypeError("qualification result is not a mapping")
        return result
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            closed = close()
            if hasattr(closed, "__await__"):
                await closed


def _run_job(
    app: Any,
    store: _JobStore,
    job_id: str,
    raw: dict[str, Any],
    skill_id: str,
    version: int,
    inputs: list[dict[str, Any]],
) -> None:
    try:
        result = asyncio.run(_qualify(app, raw, skill_id, version, inputs))
    except Exception as exc:  # noqa: BLE001 - only the class is exposed
        store.fail(job_id, exc)
    else:
        store.finish(job_id, result)


def _run_summaries(value: Any) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for run in value if isinstance(value, list) else []:
        if not isinstance(run, Mapping):
            continue
        records = run.get("records")
        summaries.append(
            {
                "outcome": run.get("outcome"),
                "records": len(records) if isinstance(records, list) else 0,
                "metrics": dict(run.get("metrics"))
                if isinstance(run.get("metrics"), Mapping)
                else {},
            }
        )
    return summaries


@router.post(
    "/api/workflows/{skill_id}/versions/{version}/qualifications",
    status_code=202,
)
def start_qualification(
    skill_id: str, version: int, body: QualificationRequest, request: Request
):
    ghost_url = os.getenv("GHOST_API_URL")
    if not ghost_url:
        raise HTTPException(422, "GHOST_API_URL is required for qualification")
    run, subtask = _load_exploration(request.app, body.run_id)
    exploration = subtask.parameters if isinstance(subtask.parameters, dict) else {}
    if any(item == exploration for item in body.inputs):
        raise HTTPException(
            422, "every qualification input must differ from exploration parameters"
        )
    try:
        raw = _request_template(request.app, run, subtask)
    except Exception as exc:  # noqa: BLE001 - configuration detail stays private
        raise HTTPException(
            422, f"qualification request could not be built ({type(exc).__name__})"
        ) from None

    job_id = f"qualification-{uuid.uuid4().hex[:12]}"
    store = _jobs(request.app)
    store.create(job_id)
    thread = threading.Thread(
        target=_run_job,
        args=(
            request.app,
            store,
            job_id,
            raw,
            skill_id,
            version,
            list(body.inputs),
        ),
        name=job_id,
        daemon=True,
    )
    thread.start()
    return {"job_id": job_id, "status": "running"}


@router.get("/api/workflows/qualifications/{job_id}")
def qualification_status(job_id: str, request: Request):
    item = _jobs(request.app).get(job_id)
    if item is None:
        raise HTTPException(404, "qualification job not found")
    return item


@router.get("/api/workflows")
def workflows():
    ghost_url = os.getenv("GHOST_API_URL")
    if not ghost_url:
        raise HTTPException(422, "GHOST_API_URL is required for the workflow registry")
    url = ghost_url.rstrip("/") + "/v1/workflows"
    try:
        with urllib.request.urlopen(url, timeout=5.0) as response:
            if not 200 <= response.status < 300:
                raise ValueError("unexpected status")
            raw = response.read(_MAX_PROXY_BYTES + 1)
        if len(raw) > _MAX_PROXY_BYTES:
            raise ValueError("response too large")
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise TypeError("workflow list is not an array")
        return payload
    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        OSError,
        TypeError,
        ValueError,
    ):
        raise HTTPException(502, "Ghost workflow registry is unavailable") from None
