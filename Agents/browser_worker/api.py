"""Small in-process API coordinator; worker logic remains transport-independent."""

import asyncio
import hashlib
import hmac
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .policy import validate_request
from .runner import Worker
from .schemas import SubtaskReport, SubtaskRequest, WorkerError, uid


@dataclass
class Job:
    job_id: str
    fingerprint: str
    request: SubtaskRequest
    status: str = "received"
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task | None = None
    report: SubtaskReport | None = None


def create_app(worker: Worker | None = None) -> FastAPI:
    load_dotenv()
    worker = worker or Worker()
    jobs: dict[str, Job] = {}
    identities: dict[tuple[str, str, str], str] = {}
    sessions: set[str] = set()

    @asynccontextmanager
    async def lifespan(app):
        yield
        active = [job for job in jobs.values() if job.task and not job.task.done()]
        for job in active:
            job.cancel.set()
        if active:
            await asyncio.gather(*(job.task for job in active), return_exceptions=True)

    app = FastAPI(title="ARGUS DOM browser worker", version="0.2.0", lifespan=lifespan)

    @app.middleware("http")
    async def request_limits(request: Request, call_next):
        token = os.getenv("WORKER_API_TOKEN")
        if token and not hmac.compare_digest(
            request.headers.get("authorization", ""), "Bearer " + token
        ):
            return JSONResponse(
                status_code=401,
                content={"error": {"code": "UNAUTHORIZED", "message": "Bearer token required."}},
            )
        if request.method == "POST":
            # Check streamed bytes, not merely the caller-controlled Content-Length header.
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 32768:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "error": {"code": "INVALID_INPUT", "message": "Request too large."}
                        },
                    )
                chunks.append(chunk)
            # Starlette's BaseHTTPMiddleware replays this cached body downstream
            # after stream() has been consumed. This private compatibility hook is
            # covered by API body-limit and valid-JSON tests; recheck on upgrades.
            request._body = b"".join(chunks)
        return await call_next(request)

    @app.exception_handler(RequestValidationError)
    async def malformed(request, exc):
        # FastAPI's default validation response can echo credentials in input fields.
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "INVALID_INPUT",
                    "message": "Request does not match SubtaskRequest 0.2.",
                    "retryable": False,
                    "evidence_refs": [],
                }
            },
        )

    @app.exception_handler(WorkerError)
    async def rejected(request, exc):
        return JSONResponse(status_code=422, content={"error": exc.failure.model_dump(mode="json")})

    async def execute(job):
        try:
            job.report = await worker.run(
                job.request, job.cancel, lambda s: setattr(job, "status", s)
            )
        finally:
            if job.request.session.session_ref:
                sessions.discard(job.request.session.session_ref)

    def submit(task):
        task, _ = validate_request(task, worker.sites)
        fingerprint = hashlib.sha256(task.model_dump_json().encode()).hexdigest()
        identity = (task.request_id, task.run_id, task.subtask_id)
        if identity in identities:
            existing = jobs[identities[identity]]
            if existing.fingerprint != fingerprint:
                raise HTTPException(409, "Request identifiers already used with different inputs.")
            return existing
        if task.session.session_ref and task.session.session_ref in sessions:
            raise HTTPException(409, "This session is already in use by a worker.")
        if sum(j.task is not None and not j.task.done() for j in jobs.values()) >= 4:
            raise HTTPException(429, "Worker concurrency limit reached.")
        if len(jobs) >= 100:
            # Bounded in-memory demo retention, oldest completed report first.
            old = next((j for j in jobs.values() if j.task and j.task.done()), None)
            if old:
                identities.pop((old.request.request_id, old.request.run_id, old.request.subtask_id))
                jobs.pop(old.job_id)
        job = Job(uid(), fingerprint, task)
        jobs[job.job_id] = job
        identities[identity] = job.job_id
        if task.session.session_ref:
            sessions.add(task.session.session_ref)
        job.task = asyncio.create_task(execute(job))
        return job

    def lookup(job_id):
        if job_id not in jobs:
            raise HTTPException(404, "Unknown job ID.")
        return jobs[job_id]

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "schema_version": "0.2",
            "browser": worker.settings.browser,
            "model": worker.settings.model,
            "sites": list(worker.sites),
            "openai_configured": bool(os.getenv("OPENAI_API_KEY")),
            "steel_configured": bool(os.getenv("STEEL_API_KEY")),
        }

    @app.post("/api/subtasks", status_code=202)
    async def start(task: SubtaskRequest):
        job = submit(task)
        return {
            "job_id": job.job_id,
            "status": job.status,
            "poll_url": f"/api/subtasks/{job.job_id}",
        }

    @app.post("/api/subtasks/execute", response_model=SubtaskReport)
    async def synchronous(task: SubtaskRequest):
        job = submit(task)
        # A disconnected caller must not skip owned-session cleanup.
        await asyncio.shield(job.task)
        return job.report

    @app.get("/api/subtasks/{job_id}")
    async def poll(job_id: str):
        job = lookup(job_id)
        return {"job_id": job_id, "status": job.status, "report": job.report}

    @app.post("/api/subtasks/{job_id}/cancel")
    async def cancel(job_id: str):
        job = lookup(job_id)
        if job.report is None:
            job.cancel.set()
        return {
            "job_id": job_id,
            "status": job.status,
            "cancellation_requested": job.cancel.is_set(),
        }

    @app.get("/api/schemas/subtask-request")
    async def request_schema():
        return SubtaskRequest.model_json_schema()

    @app.get("/api/schemas/subtask-report")
    async def report_schema():
        return SubtaskReport.model_json_schema()

    return app
