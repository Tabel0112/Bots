from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .models import CandidateRequest, LookupRequest, QualificationRequest, RunReport
from .service import compile_candidate, lookup
from .storage import WorkflowStore

MODULE_ROOT = Path(__file__).resolve().parents[1]


def create_app(database_path: Path | None = None) -> FastAPI:
    path = database_path or Path(
        os.environ.get("GHOST_DATABASE_PATH", MODULE_ROOT / "ghostapi.sqlite3")
    )
    store = WorkflowStore(path)
    app = FastAPI(
        title="Ghost API",
        version="0.2.0",
        description="Reusable workflow memory for Steel-powered agents.",
    )
    app.state.store = store

    @app.exception_handler(ValueError)
    async def invalid_operation(request, exc):
        if str(exc) == "IDEMPOTENCY_CONFLICT":
            return JSONResponse(
                status_code=409,
                content={"error": {"code": "IDEMPOTENCY_CONFLICT", "retryable": False}},
            )
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "INVALID_INPUT", "retryable": False}},
        )

    def get_store(request: Request) -> WorkflowStore:
        return request.app.state.store

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/graph")

    @app.get("/graph", include_in_schema=False)
    def graph() -> FileResponse:
        return FileResponse(Path(__file__).with_name("graph.html"))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/activity")
    def activity(
        limit: int = Query(100, ge=1, le=500),
        service: WorkflowStore = Depends(get_store),
    ) -> list[dict]:
        return service.activity(limit)

    @app.post("/v1/workflows/lookup")
    def find_workflow(
        body: LookupRequest, service: WorkflowStore = Depends(get_store)
    ) -> dict:
        result = lookup(service, body)
        stage = "reuse" if result["decision"] == "reuse" else "explore"
        service.event(
            stage,
            f"{body.agent_id}: workflow lookup decided {result['decision']}.",
            {
                "task_id": body.task_id,
                "site_id": body.site_id,
                "operation": body.operation,
                "skill_id": (result.get("workflow") or {}).get("skill_id"),
            },
        )
        return result

    @app.post("/v1/workflows/candidates", status_code=status.HTTP_201_CREATED)
    def save_candidate(
        body: CandidateRequest, service: WorkflowStore = Depends(get_store)
    ) -> dict:
        definition = compile_candidate(body)
        skill_id, version = service.save_candidate(definition, body.idempotency_key)
        service.event(
            "candidate",
            f"{body.agent_id}: saved {skill_id} v{version} as candidate.",
            {
                "task_id": body.task_id,
                "skill_id": skill_id,
                "version": version,
            },
        )
        return {
            "schema_version": body.schema_version,
            "skill_id": skill_id,
            "version": version,
            "status": "candidate",
        }

    @app.post(
        "/v1/workflows/{skill_id}/versions/{version}/runs",
        status_code=status.HTTP_201_CREATED,
    )
    def report_run(
        skill_id: str,
        version: int,
        body: RunReport,
        service: WorkflowStore = Depends(get_store),
    ) -> dict:
        if not service.workflow(skill_id, version):
            raise HTTPException(
                status_code=404,
                detail={"code": "WORKFLOW_NOT_FOUND", "retryable": False},
            )
        run_id = service.save_run(
            skill_id, version, body.model_dump(mode="json"), body.kind
        )
        service.event(
            "run",
            f"{body.agent_id}: reported {body.status} execution of {skill_id} v{version}.",
            {
                "task_id": body.task_id,
                "run_id": run_id,
                "skill_id": skill_id,
                "version": version,
                "status": body.status,
            },
        )
        return {"schema_version": body.schema_version, "run_id": run_id, "status": body.status}

    @app.post("/v1/workflows/{skill_id}/versions/{version}/qualification")
    def qualify(
        skill_id: str,
        version: int,
        body: QualificationRequest,
        service: WorkflowStore = Depends(get_store),
    ) -> dict:
        reports = [report.model_dump(mode="json") for report in body.reports]
        workflow_status = service.qualify(skill_id, version, reports)
        if workflow_status is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "WORKFLOW_NOT_FOUND", "retryable": False},
            )
        service.event(
            "qualified" if workflow_status == "qualified" else "candidate",
            f"{body.agent_id}: qualification left {skill_id} v{version} {workflow_status}.",
            {"skill_id": skill_id, "version": version, "status": workflow_status},
        )
        return {
            "schema_version": body.schema_version,
            "skill_id": skill_id,
            "version": version,
            "status": workflow_status,
        }

    @app.get("/v1/workflows")
    def list_workflows(service: WorkflowStore = Depends(get_store)) -> list[dict]:
        return service.workflows()

    @app.get("/v1/workflows/{skill_id}/versions/{version}")
    def get_workflow(
        skill_id: str, version: int, service: WorkflowStore = Depends(get_store)
    ) -> dict:
        workflow = service.workflow(skill_id, version)
        if not workflow:
            raise HTTPException(
                status_code=404,
                detail={"code": "WORKFLOW_NOT_FOUND", "retryable": False},
            )
        return workflow

    return app


app = create_app()
