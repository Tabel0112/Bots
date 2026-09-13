"""FastAPI application serving ARGUS Mission Control and its run stream."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from dotenv import load_dotenv

from .service import RunService


class RunRequest(BaseModel):
    scenario: str | None = None
    text: str | None = Field(default=None, max_length=4000)


def create_app(store_root: str | Path | None = None) -> FastAPI:
    load_dotenv()
    root = Path(__file__).resolve().parents[2]
    frontend = root / "frontend" / "dist"
    service = RunService(store_root or os.getenv("ARGUS_STORE", root / "argus-runs"))
    app = FastAPI(title="ARGUS Mission Control", version="0.1-demo")
    app.state.run_service = service

    @app.get("/api/health")
    def health():
        return {"status": "ok", "runtime": service.runtime, "steel_configured": bool(os.getenv("STEEL_API_KEY")), "scenarios": ["shopping", "travel", "jobs"]}

    @app.post("/api/runs", status_code=202)
    def submit(body: RunRequest):
        try:
            return service.submit(body.scenario, body.text)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/api/runs")
    def runs():
        return service.list_runs()

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        try:
            return service.get(run_id)
        except (OSError, ValueError):
            raise HTTPException(404, "run not found") from None

    @app.get("/api/runs/{run_id}/event-log")
    def event_log(run_id: str):
        try:
            service.get(run_id)
            return service.events(run_id)
        except (OSError, ValueError):
            raise HTTPException(404, "run not found") from None

    @app.post("/api/runs/{run_id}/cancel", status_code=202)
    def cancel(run_id: str):
        service.cancel(run_id)
        return {"run_id": run_id, "cancellation": "requested"}

    @app.get("/api/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        try:
            service.get(run_id)
        except (OSError, ValueError):
            raise HTTPException(404, "run not found") from None
        last = int(request.headers.get("last-event-id") or request.query_params.get("after") or 0)

        async def stream():
            cursor = last
            while True:
                if await request.is_disconnected():
                    break
                available = [event for event in service.events(run_id) if event["sequence"] > cursor]
                for event in available:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: argus\ndata: {json.dumps(event)}\n\n"
                try:
                    snapshot = service.get(run_id)
                except (OSError, ValueError):
                    break
                if snapshot.get("status") in {"succeeded", "failed", "cancelled", "needs_input"} and not available:
                    break
                if not available:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.2)

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/")
    def index():
        return FileResponse(frontend / "index.html")

    app.mount("/assets", StaticFiles(directory=frontend), name="assets")
    return app
