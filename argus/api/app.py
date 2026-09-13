"""FastAPI application serving ORION (Mission Control) and its run stream."""

from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .qualification import router as qualification_router
from .service import RunService

_OBSERVATION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_TERMINAL = {"succeeded", "failed", "cancelled", "needs_input"}


class RunRequest(BaseModel):
    scenario: str | None = None
    text: str | None = Field(default=None, max_length=4000)


class ClarificationRequest(BaseModel):
    answer: str = Field(min_length=1, max_length=2000)


def create_app(
    store_root: str | Path | None = None, *, service: RunService | None = None
) -> FastAPI:
    """Build the API. ``service`` lets tests inject a service with fake components."""
    load_dotenv()
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend" / "dist"
    if service is None:
        service = RunService(
            store_root or os.getenv("ARGUS_STORE", root / "argus-runs")
        )
    app = FastAPI(title="ORION", version="0.2-argus-3")
    app.state.run_service = service
    app.include_router(qualification_router)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "runtime": service.runtime,
            "problems": list(service.problems),
            "steel_configured": bool(os.getenv("STEEL_API_KEY")),
            "worker_browser": os.getenv("WORKER_BROWSER", "steel"),
            "ghost_api_url": os.getenv("GHOST_API_URL"),
            "argus_model": os.getenv("ARGUS_MODEL"),
            "moderator": os.getenv("ARGUS_MODERATOR") or "module",
            "open_world_search": service.runtime == "connected",
            "scenarios": ["shopping", "travel", "jobs"]
            if service.runtime != "connected"
            else [],
        }

    @app.post("/api/runs", status_code=202)
    def submit(body: RunRequest):
        try:
            return service.submit(body.scenario, body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/runs/{run_id}/clarifications", status_code=202)
    def clarify(run_id: str, body: ClarificationRequest):
        try:
            return service.clarify(run_id, body.answer)
        except (OSError, KeyError):
            raise HTTPException(404, "run not found") from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runs")
    def runs():
        return service.list_runs()

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        try:
            return service.get(run_id)
        except (OSError, ValueError, KeyError):
            raise HTTPException(404, "run not found") from None

    @app.get("/api/runs/{run_id}/event-log")
    def event_log(run_id: str):
        try:
            service.get(run_id)
            return service.events(run_id)
        except (OSError, ValueError, KeyError):
            raise HTTPException(404, "run not found") from None

    @app.get("/api/runs/{run_id}/evidence/{observation_id}")
    def evidence(run_id: str, observation_id: str):
        if not _OBSERVATION_ID.match(observation_id):
            raise HTTPException(404, "evidence not found")
        try:
            service.get(run_id)
        except (OSError, ValueError, KeyError):
            raise HTTPException(404, "run not found") from None
        path = service.evidence_path(run_id, observation_id)
        if path is None:
            raise HTTPException(404, "evidence not found")
        return FileResponse(path)

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        service.cancel(run_id)
        return {"run_id": run_id, "cancellation": "requested"}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        try:
            service.get(run_id)
        except (OSError, ValueError, KeyError):
            raise HTTPException(404, "run not found") from None
        last = int(
            request.headers.get("last-event-id")
            or request.query_params.get("after")
            or 0
        )

        async def stream():
            cursor = last
            while True:
                if await request.is_disconnected():
                    break
                available = [
                    event
                    for event in service.events(run_id)
                    if event["sequence"] > cursor
                ]
                for event in available:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: argus\ndata: {json.dumps(event)}\n\n"
                try:
                    snapshot = service.get(run_id)
                except (OSError, ValueError, KeyError):
                    break
                if snapshot.get("status") in _TERMINAL and not available:
                    break
                if not available:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html")

    app.mount("/assets", StaticFiles(directory=frontend), name="assets")
    return app
