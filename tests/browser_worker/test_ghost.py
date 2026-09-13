"""Connection tests: real local Chromium, real Ghost HTTP API, temporary SQLite."""

import asyncio
from copy import deepcopy

import httpx
import pytest

from Agents.browser_worker.demo.run import ScriptedCatalogReasoner, catalog_server
from Agents.browser_worker.ghost import GhostWorkflow, session_lease
from Agents.browser_worker.ghost_adapter import NotCompilable, candidate_request
from Agents.browser_worker.runner import Worker
from Agents.browser_worker.schemas import (
    SubtaskReport,
    WorkerError,
)
from ghostapi.api.app import create_app
from ghostapi.client import GhostClient, GhostError


@pytest.fixture
async def ghost(tmp_path):
    app = create_app(tmp_path / "ghost.sqlite3")
    async with GhostClient("http://ghost", transport=httpx.ASGITransport(app=app)) as client:
        yield client, app.state.store


@pytest.mark.browser
async def test_explore_qualify_changed_input_reuse(ghost, request_data, sites, local_settings):
    client, store = ghost
    worker = Worker(sites=sites, settings=local_settings, reasoner_factory=ScriptedCatalogReasoner)
    connection = GhostWorkflow(worker, client)
    with catalog_server():
        initial = await connection.run(request_data)
        assert initial.outcome == "succeeded", initial.model_dump_json()
        identity = initial.ghost["candidate"]
        assert identity and identity["status"] == "candidate", initial.ghost
        version = store.workflow(identity["skill_id"], identity["version"])
        steps = version["definition"]["steps"]
        assert steps[0]["value"] == {"parameter": "query"}
        assert steps[1]["value"] == {"parameter": "max_price"}
        assert "target_ref" not in str(steps)
        assert "container_ref" not in str(steps)
        qualification = await connection.qualify(
            request_data,
            identity["skill_id"],
            identity["version"],
            [
                {"query": "keyboard", "max_price": 100},
                {"query": "headphones", "max_price": 100},
                {"query": "unobtainium", "max_price": 200},
            ],
        )
        assert qualification["qualification"]["status"] == "qualified", qualification
        assert all(r["metrics"]["model_calls"] == 0 for r in qualification["runs"])
        assert len({r["session_ref"] for r in qualification["runs"]}) == 3
        changed = deepcopy(request_data)
        changed["parameters"] = {"query": "keyboard", "max_price": 150}
        changed["run_id"] = "reuse-test"
        reused = await connection.run(changed)
        assert reused.outcome == "succeeded", reused.model_dump_json()
        assert reused.ghost["mode"] == "reuse"
        assert reused.ghost["run_id"]
        assert reused.metrics.model_calls == 0
        assert all("keyboard" in r.data["title"].lower() for r in reused.records)
        assert reused.session_ref != initial.session_ref
        assert reused.session_disposition == "released"


@pytest.mark.browser
async def test_candidate_idempotency_and_missing_origins(
    ghost, request_data, sites, local_settings
):
    from Agents.browser_worker.policy import validate_request

    client, store = ghost
    worker = Worker(sites=sites, settings=local_settings, reasoner_factory=ScriptedCatalogReasoner)
    with catalog_server():
        report = await worker._run(request_data)
    task, site = validate_request(request_data, sites)
    body = candidate_request(task, site, report, "test")
    first = await client.create_candidate(body)
    assert await client.create_candidate(body) == first
    assert len(store.workflows()) == 1
    bad = deepcopy(body)
    bad["description"] = "changed"
    with pytest.raises(GhostError) as exc:
        await client.create_candidate(bad)
    assert exc.value.status == 409
    body["trace"][0]["input_parameter"] = None
    with pytest.raises(GhostError) as exc:
        await client.create_candidate(body)
    assert exc.value.status == 422
    report.validation[0].passed = False
    with pytest.raises(NotCompilable):
        candidate_request(task, site, report, "test")


@pytest.mark.browser
async def test_ghost_offline_keeps_worker_result(request_data, sites, local_settings):
    async def broken(request):
        raise httpx.ConnectError("unavailable")

    async with GhostClient("http://offline", transport=httpx.MockTransport(broken)) as client:
        connection = GhostWorkflow(
            Worker(
                sites=sites,
                settings=local_settings,
                reasoner_factory=ScriptedCatalogReasoner,
            ),
            client,
        )
        with catalog_server():
            report = await connection.run(request_data)
    assert report.outcome == "succeeded"
    assert report.ghost["errors"] == ["GHOST_UNAVAILABLE", "GHOST_UNAVAILABLE"]


async def test_exclusive_session_ownership():
    async with session_lease("one"):
        with pytest.raises(WorkerError):
            async with session_lease("one"):
                pass
    async with session_lease("one"):
        pass


async def test_dom_first_then_visual_same_session(ghost, request_data, sites, local_settings):
    client, _ = ghost
    order = []
    request_data["session"] = {"ownership": "argus", "session_ref": "shared-session"}
    request_data["visual_fallback_available"] = True

    class Dom:
        settings = local_settings

        async def _run(self, task, cancel, on_status):
            order.append("dom")
            return SubtaskReport(
                request_id=task.request_id,
                run_id=task.run_id,
                subtask_id=task.subtask_id,
                outcome="needs_visual",
                session_ref=task.session.session_ref,
                session_disposition="retained",
            )

    worker = Dom()
    worker.sites = sites

    async def visual(task, site, report, cancel):
        order.append("visual")
        assert task.session.session_ref == "shared-session"
        assert task.start_url is None
        assert task.budgets.max_runtime_seconds <= request_data["budgets"]["max_runtime_seconds"]
        report.outcome = "inconclusive"
        report.summary = "Read the canvas; structured validation is unavailable."
        report.visual_report = {"summary": report.summary}
        return report

    report = await GhostWorkflow(worker, client, visual_runner=visual).run(request_data)
    assert order == ["dom", "visual"]
    assert report.visual_report
    assert report.ghost["visual_used"]
    assert report.ghost["candidate"] is None
    assert report.session_disposition == "retained"


async def test_cancel_before_lookup_never_starts_worker(ghost, request_data, sites, local_settings):
    client, _ = ghost
    cancelled = asyncio.Event()
    cancelled.set()
    report = await GhostWorkflow(Worker(sites=sites, settings=local_settings), client).run(
        request_data, cancelled
    )
    assert report.outcome == "cancelled"
    assert report.session_ref is None


async def test_cancel_during_lookup_never_starts_worker(request_data, sites, local_settings):
    cancel = asyncio.Event()

    async def slow(request):
        cancel.set()
        await asyncio.sleep(10)

    async with GhostClient("http://slow", transport=httpx.MockTransport(slow)) as client:
        report = await GhostWorkflow(Worker(sites=sites, settings=local_settings), client).run(
            request_data, cancel
        )
    assert report.outcome == "cancelled"
    assert report.session_ref is None


@pytest.mark.parametrize(
    "body", [{"decision": "reuse", "workflow": {"bound_steps": []}}, {"decision": "maybe"}, []]
)
async def test_malformed_lookup_is_a_typed_error(body):
    async def respond(request):
        return httpx.Response(200, json=body)

    async with GhostClient("http://bad", transport=httpx.MockTransport(respond)) as client:
        with pytest.raises(GhostError):
            await client.lookup({})


async def test_owned_steel_session_released_once_after_visual(
    ghost, request_data, sites, local_settings
):
    client, _ = ghost
    events = []

    class Sessions:
        async def create(self, **kwargs):
            self.identity = kwargs["session_id"]
            events.append("created")

        async def release(self, identity):
            assert identity == self.identity
            events.append("released")

    class Steel:
        sessions = Sessions()

        async def close(self):
            events.append("client_closed")

    class Dom:
        async def _run(self, task, cancel, on_status):
            assert task.session.ownership == "argus"
            events.append("dom")
            return SubtaskReport(
                request_id=task.request_id,
                run_id=task.run_id,
                subtask_id=task.subtask_id,
                outcome="needs_visual",
                session_ref=task.session.session_ref,
                session_disposition="retained",
            )

    worker = Dom()
    worker.settings = local_settings.model_copy(update={"browser": "steel"})
    worker.sites = sites

    async def visual(task, site, report, cancel):
        events.append("visual")
        report.outcome = "inconclusive"
        return report

    report = await GhostWorkflow(worker, client, session_factory=Steel, visual_runner=visual).run(
        request_data
    )
    assert events == ["created", "dom", "visual", "released", "client_closed"]
    assert report.session_disposition == "released"


@pytest.mark.browser
async def test_qualification_cannot_use_unstored_reports(
    ghost, request_data, sites, local_settings
):
    client, _ = ghost
    worker = Worker(sites=sites, settings=local_settings, reasoner_factory=ScriptedCatalogReasoner)
    with catalog_server():
        result = await GhostWorkflow(worker, client).run(request_data)
    identity = result.ghost["candidate"]
    outcome = await client.submit_qualification(
        identity["skill_id"],
        identity["version"],
        {
            "schema_version": "0.2",
            "agent_id": "test",
            "reports": [
                {
                    "parameters": {"query": value, "max_price": 100},
                    "validation_status": "passed",
                    "empty_result": value == "empty",
                    "evidence_refs": ["claimed"],
                    "run_id": "not-stored",
                }
                for value in ("keyboard", "mouse", "empty")
            ],
        },
    )
    assert outcome["status"] == "candidate"


@pytest.mark.browser
async def test_changed_target_fails_replay_without_exploration(
    ghost, request_data, sites, local_settings
):
    from Agents.browser_worker.policy import validate_request
    from ghostapi.api.service import bind

    client, _ = ghost
    worker = Worker(sites=sites, settings=local_settings, reasoner_factory=ScriptedCatalogReasoner)
    connection = GhostWorkflow(worker, client)
    with catalog_server():
        result = await connection.run(request_data)
        identity = result.ghost["candidate"]
        saved = await client.get_workflow(identity["skill_id"], identity["version"])
        definition = saved["definition"]
        task, site = validate_request(request_data, sites)
        workflow = {
            k: definition[k] for k in ("compatibility_key", "output_schema_id", "preconditions")
        }
        workflow["bound_steps"] = bind(definition, task.parameters)
        workflow["bound_steps"][0]["target"]["label"] = "Removed search field"
        failed = await connection.replay(task, site, workflow)
    assert failed.outcome == "failed"
    assert failed.failures[-1].code == "TARGET_NOT_FOUND"
    assert failed.metrics.model_calls == 0
    assert failed.metrics.actions == 0


@pytest.mark.browser
async def test_canvas_without_dom_results_requests_visual_before_model(
    request_data, sites, local_settings
):
    from Agents.browser_worker.browser.adapter import BrowserAdapter

    class CanvasBrowser(BrowserAdapter):
        async def start(self):
            await super().start()
            await self.page.evaluate(
                "document.body.innerHTML = '<canvas width=300 height=150></canvas>'"
            )

    class NoModel:
        def __init__(self, settings):
            pass

        async def decide(self, context):
            raise AssertionError(
                "Canvas-only page should request visual help after DOM inspection."
            )

    with catalog_server():
        report = await Worker(
            sites=sites,
            settings=local_settings,
            browser_factory=CanvasBrowser,
            reasoner_factory=NoModel,
        )._run(request_data)
    assert report.outcome == "needs_visual"
    assert report.visual_handoff.reason_code == "INSUFFICIENT_DOM"
    assert report.metrics.model_calls == 0


async def test_busy_session_is_never_released_by_duplicate(
    ghost, request_data, sites, local_settings, monkeypatch
):
    from unittest.mock import MagicMock

    import steel

    client, _ = ghost
    factory = MagicMock()
    monkeypatch.setattr(steel, "AsyncSteel", factory)
    request_data["session"] = {"ownership": "argus", "session_ref": "busy", "close_on_finish": True}
    async with session_lease("busy"):
        report = await GhostWorkflow(Worker(sites=sites, settings=local_settings), client).run(
            request_data
        )
    assert report.outcome == "failed"
    factory.assert_not_called()
