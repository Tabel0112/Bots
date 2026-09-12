import asyncio

import httpx

from browser_worker.api import create_app
from browser_worker.config import Settings
from browser_worker.schemas import SubtaskReport


class StubWorker:
    def __init__(self, sites):
        self.sites = sites
        self.settings = Settings()
        self.calls = 0

    async def run(self, task, cancel, on_status):
        self.calls += 1
        on_status("reasoning")
        await cancel.wait()
        on_status("cancelled")
        return SubtaskReport(
            request_id=task.request_id,
            run_id=task.run_id,
            subtask_id=task.subtask_id,
            outcome="cancelled",
            summary="Cancelled by caller.",
        )


async def test_api_validation_idempotency_poll_cancel(request_data, sites):
    worker = StubWorker(sites)
    app = create_app(worker)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://worker"
        ) as client:
            bad = await client.post("/api/subtasks", json={"password": "never-echo"})
            assert bad.status_code == 422 and "never-echo" not in bad.text
            assert worker.calls == 0
            started = await client.post("/api/subtasks", json=request_data)
            assert started.status_code == 202
            job = started.json()["job_id"]
            duplicate = await client.post("/api/subtasks", json=request_data)
            assert duplicate.json()["job_id"] == job
            changed = {**request_data, "objective": "A different read-only search"}
            assert (await client.post("/api/subtasks", json=changed)).status_code == 409
            status = await client.get(f"/api/subtasks/{job}")
            assert status.json()["report"] is None
            assert (await client.post(f"/api/subtasks/{job}/cancel")).json()[
                "cancellation_requested"
            ]
            await asyncio.sleep(0)
            status = await client.get(f"/api/subtasks/{job}")
            assert status.json()["report"]["outcome"] == "cancelled"
            assert worker.calls == 1
            assert (await client.get("/api/subtasks/missing")).status_code == 404


async def test_auth_size_limits_and_session_exclusion(request_data, sites, monkeypatch):
    monkeypatch.setenv("WORKER_API_TOKEN", "test-token")
    app = create_app(StubWorker(sites))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://worker"
        ) as client:
            assert (await client.get("/health")).status_code == 401
            client.headers["Authorization"] = "Bearer test-token"
            assert (await client.get("/health")).status_code == 200
            assert (await client.post("/api/subtasks", content="x" * 33000)).status_code == 413
            request_data["session"] = {"ownership": "argus", "session_ref": "shared-session"}
            assert (await client.post("/api/subtasks", json=request_data)).status_code == 202
            request_data["subtask_id"] = "second"
            assert (await client.post("/api/subtasks", json=request_data)).status_code == 409
