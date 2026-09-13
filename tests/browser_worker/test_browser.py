import asyncio

import pytest

from Agents.browser_worker.browser.adapter import BrowserAdapter
from Agents.browser_worker.demo.run import (
    CatalogHandler,
    ScriptedCatalogReasoner,
    action,
    catalog_server,
)
from Agents.browser_worker.policy import validate_request
from Agents.browser_worker.runner import Worker
from Agents.browser_worker.schemas import Decision, WorkerError
from Agents.browser_worker.verifier import verify

pytestmark = pytest.mark.browser


@pytest.fixture
def catalog():
    with catalog_server() as server:
        yield server


async def run_local(data, sites, settings, reasoner=ScriptedCatalogReasoner, cancel=None):
    return await Worker(sites=sites, settings=settings, reasoner_factory=reasoner).run(data, cancel)


async def test_valid_request_semantic_discovery_extraction_and_cleanup(
    catalog, request_data, sites, local_settings
):
    report = await run_local(request_data, sites, local_settings)
    assert report.outcome == "succeeded", report.model_dump_json()
    assert [r.data["title"] for r in report.records] == ["Studio headphones", "Travel headphones"]
    assert [r.data["price"] for r in report.records] == [129, 79]
    assert report.metrics.actions == 4 and report.metrics.model_calls == 5
    assert report.session_disposition == "released"
    assert {v.key for v in report.parameter_origins.values()} == {"query", "max_price"}
    assert all(c.passed for c in report.validation)
    assert all(o.execution_id == report.execution_id for o in report.observations)
    assert report.action_trace[0].expected_change_observed
    # Tampered data and transplanted evidence must not verify.
    task, site = validate_request(request_data, sites)
    report.records[0].data["price"] = 1
    checks = verify(
        task, site, report, report.observations[-1], report.observations[-1].content_hash
    )
    assert not next(c for c in checks if c.check_id == "field_provenance").passed
    report.observations[0].execution_id = "another-execution"
    checks = verify(
        task, site, report, report.observations[-1], report.observations[-1].content_hash
    )
    assert not next(c for c in checks if c.check_id == "current_run").passed


async def test_verified_empty_state(catalog, request_data, sites, local_settings):
    request_data["parameters"]["query"] = "unobtainium"
    report = await run_local(request_data, sites, local_settings)
    assert report.outcome == "succeeded"
    assert report.records == []
    assert report.observations[-1].signals["empty"]


async def test_failed_success_check_is_inconclusive(catalog, request_data, sites, local_settings):
    request_data["success_conditions"] = [{"kind": "min_records", "value": 3}]
    report = await run_local(request_data, sites, local_settings)
    assert report.outcome == "inconclusive"
    assert report.failures[-1].code == "VALIDATION_FAILED"
    assert report.session_disposition == "released"


class EarlySuccess:
    def __init__(self, settings):
        pass

    async def decide(self, context):
        return Decision(action_type="report_success", arguments=action(context["observation"]))


async def test_model_cannot_claim_unextracted_success(catalog, request_data, sites, local_settings):
    report = await run_local(request_data, sites, local_settings, EarlySuccess)
    assert report.outcome == "inconclusive"
    assert not next(c for c in report.validation if c.check_id == "fresh_extraction").passed


async def test_budget_exhaustion_cleanup(catalog, request_data, sites, local_settings):
    request_data["budgets"]["max_actions"] = 1
    report = await run_local(request_data, sites, local_settings)
    assert report.outcome == "failed"
    assert report.failures[-1].code == "BUDGET_EXHAUSTED"
    assert report.metrics.actions == 1
    assert report.session_disposition == "released"


class VisualReasoner(EarlySuccess):
    async def decide(self, context):
        return Decision(
            action_type="request_visual_fallback",
            arguments=action(
                context["observation"],
                semantic_target="unlabeled chart",
                visual_question="Which chart area is selected?",
                failure_code="INSUFFICIENT_DOM",
            ),
        )


async def test_visual_handoff(catalog, request_data, sites, local_settings):
    request_data["visual_fallback_available"] = False
    report = await run_local(request_data, sites, local_settings, VisualReasoner)
    assert report.outcome == "needs_visual"
    assert report.visual_handoff.run_id == request_data["run_id"]
    assert report.visual_handoff.question == "Which chart area is selected?"
    assert not report.visual_handoff.available


class SlowReasoner(EarlySuccess):
    async def decide(self, context):
        await asyncio.sleep(30)


async def test_cancellation_interrupts_model_and_cleans_up(
    catalog, request_data, sites, local_settings
):
    cancel = asyncio.Event()
    worker = Worker(sites=sites, settings=local_settings, reasoner_factory=SlowReasoner)

    def stage(status):
        if status == "reasoning":
            asyncio.get_running_loop().call_later(0.05, cancel.set)

    report = await worker.run(request_data, cancel, stage)
    assert report.outcome == "cancelled"
    assert report.session_disposition == "released"
    assert report.metrics.model_calls == 1


async def test_model_timeouts_are_bounded(catalog, request_data, sites, local_settings):
    local_settings.model_timeout_seconds = 0.05
    request_data["budgets"]["max_retries"] = 1
    report = await run_local(request_data, sites, local_settings, SlowReasoner)
    assert report.outcome == "failed"
    assert report.metrics.model_calls == 2
    assert any(f.code == "MODEL_TIMEOUT" for f in report.failures)
    assert report.session_disposition == "released"


class NoProgress(EarlySuccess):
    async def decide(self, context):
        e = next(e for e in context["observation"]["elements"] if e["role"] == "textbox")
        return Decision(
            action_type="inspect_element",
            arguments=action(
                context["observation"], target_ref=e["ref"], semantic_target=e["name"]
            ),
        )


async def test_no_progress_detected(catalog, request_data, sites, local_settings):
    report = await run_local(request_data, sites, local_settings, NoProgress)
    assert report.failures[-1].code == "NO_PROGRESS"
    assert report.metrics.actions < request_data["budgets"]["max_actions"]


class MissingThenRecover(ScriptedCatalogReasoner):
    def __init__(self, settings):
        super().__init__(settings)
        self.first = True

    async def decide(self, context):
        if self.first:
            self.first = False
            return Decision(
                action_type="click", arguments=action(context["observation"], target_ref="e999")
            )
        assert context["recent_errors"][0]["code"] == "TARGET_NOT_FOUND"
        return await super().decide(context)


async def test_missing_target_reobserves_and_recovers(catalog, request_data, sites, local_settings):
    report = await run_local(request_data, sites, local_settings, MissingThenRecover)
    assert report.outcome == "succeeded"
    assert report.metrics.retries == 1
    assert report.action_trace[0].outcome == "rejected"


async def test_stale_dom_and_secrets(catalog, request_data, sites, local_settings):
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        await browser.page.evaluate("""() => {
            const el = document.createElement('input'); el.type='password';el.value='never-log-this';document.body.append(el);
            const hidden = document.createElement('div');hidden.hidden=true;hidden.textContent='hidden-secret';document.body.append(hidden);
            const token = document.createElement('input');token.name='api_key';token.value='secret-key';document.body.append(token);
        }""")
        obs = await browser.observe("test")
        assert all(
            s not in obs.model_dump_json()
            for s in ["never-log-this", "hidden-secret", "secret-key"]
        )
        assert obs.signals["auth_required"]
        await browser.page.locator("input[name='q']").fill("new state")
        with pytest.raises(WorkerError) as error:
            await browser.assert_fresh(obs)
        assert error.value.failure.code == "STALE_OBSERVATION"
    finally:
        assert await browser.close() == "released"


async def test_background_write_is_aborted_without_failing_run(
    catalog, request_data, sites, local_settings, monkeypatch
):
    received = []
    monkeypatch.setattr(
        CatalogHandler, "do_POST", lambda handler: received.append(True), raising=False
    )
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        assert await browser.page.evaluate(
            "fetch('/',{method:'POST',body:'blocked'}).then(()=>false).catch(()=>true)"
        )
        assert not received
        assert (await browser.observe("test")).title == "ARGUS test catalog"
    finally:
        await browser.close()


async def test_worker_action_write_is_terminal(catalog, request_data, sites, local_settings):
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        # The configured read-only search control is deliberately made unsafe.
        await browser.page.evaluate("""() => {
            document.querySelector('button').addEventListener('click', event => {
                event.preventDefault();
                fetch('/', {method: 'POST', body: 'blocked'}).catch(() => null);
            });
        }""")
        obs = await browser.observe("test")
        target = next(e for e in obs.elements if "click" in e.permitted_actions)
        decision = Decision(
            action_type="click",
            arguments=action(
                obs.model_dump(mode="json"), target_ref=target.ref, semantic_target=target.name
            ),
        )
        with pytest.raises(WorkerError) as error:
            await browser.execute(decision, obs)
        assert error.value.failure.code == "ACTION_REJECTED"
        assert not browser.action_in_progress
    finally:
        await browser.close()


async def test_post_navigation_is_terminal(catalog, request_data, sites, local_settings):
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        async with browser.page.expect_event("requestfailed"):
            await browser.page.evaluate("""() => {
                const form = document.createElement('form');
                form.method = 'POST'; form.action = '/';
                document.body.append(form); form.submit();
            }""")
        with pytest.raises(WorkerError) as error:
            await browser.observe("test")
        assert error.value.failure.code == "ACTION_REJECTED"
    finally:
        await browser.close()


@pytest.mark.parametrize(
    "code", ["CANCELLED", "BUDGET_EXHAUSTED", "INTERNAL_ERROR", "AUTH_REQUIRED"]
)
async def test_model_failure_cannot_claim_runtime_status(
    catalog, request_data, sites, local_settings, code
):
    class FailedReasoner(EarlySuccess):
        async def decide(self, context):
            return Decision(
                action_type="report_failure",
                arguments=action(context["observation"], failure_code=code),
            )

    report = await run_local(request_data, sites, local_settings, FailedReasoner)
    assert report.outcome == "failed"
    assert report.failures[-1].code == (
        "AUTH_REQUIRED" if code == "AUTH_REQUIRED" else "PRECONDITION_FAILED"
    )
    assert report.session_disposition == "released"


async def test_redirect_chain_cannot_escape_policy(
    catalog, request_data, sites, local_settings, monkeypatch
):
    original = CatalogHandler.do_GET
    escaped = []

    def do_get(handler):
        if handler.path == "/products/redirect":
            handler.send_response(302)
            handler.send_header("Location", "/products/second-redirect")
            handler.end_headers()
        elif handler.path == "/products/second-redirect":
            handler.send_response(302)
            handler.send_header("Location", "/admin")
            handler.end_headers()
        elif handler.path == "/admin":
            escaped.append(True)
            original(handler)
        else:
            original(handler)

    monkeypatch.setattr(CatalogHandler, "do_GET", do_get)
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        with pytest.raises(WorkerError) as error:
            await browser.navigate("http://127.0.0.1:8765/products/redirect")
        assert error.value.failure.code == "DOMAIN_NOT_ALLOWED"
        assert not escaped, "Blocked redirect must never reach the forbidden server path"
    finally:
        await browser.close()


class MalformedThenRecover(ScriptedCatalogReasoner):
    def __init__(self, settings):
        super().__init__(settings)
        self.first = True

    async def decide(self, context):
        if self.first:
            self.first = False
            return {"action_type": "execute_arbitrary_code"}
        assert context["recent_errors"][0]["code"] == "MODEL_ERROR"
        return await super().decide(context)


async def test_malformed_model_call_recovery_in_worker(
    catalog, request_data, sites, local_settings
):
    report = await run_local(request_data, sites, local_settings, MalformedThenRecover)
    assert report.outcome == "succeeded"
    assert report.metrics.retries == 1


async def test_fastapi_to_real_browser_report(catalog, request_data, sites, local_settings):
    import httpx

    from Agents.browser_worker.api import create_app

    app = create_app(
        Worker(sites=sites, settings=local_settings, reasoner_factory=ScriptedCatalogReasoner)
    )
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app), base_url="http://worker"
        ) as client:
            response = await client.post("/api/subtasks/execute", json=request_data)
            assert response.status_code == 200, response.text
            report = response.json()
            assert report["outcome"] == "succeeded"
            assert len(report["records"]) == 2
            assert report["session_disposition"] == "released"


async def test_allowed_redirect_preserves_final_url(
    catalog, request_data, sites, local_settings, monkeypatch
):
    original = CatalogHandler.do_GET

    def do_get(handler):
        if handler.path == "/products/redirect":
            handler.send_response(302)
            handler.send_header("Location", "/")
            handler.end_headers()
        else:
            original(handler)

    monkeypatch.setattr(CatalogHandler, "do_GET", do_get)
    task, site = validate_request(request_data, sites)
    browser = BrowserAdapter(task, site, local_settings)
    try:
        await browser.start()
        await browser.navigate("http://127.0.0.1:8765/products/redirect")
        obs = await browser.observe("test")
        assert obs.url == "http://127.0.0.1:8765/"
        assert obs.title == "ARGUS test catalog"
    finally:
        await browser.close()


async def test_model_call_budget(catalog, request_data, sites, local_settings):
    request_data["budgets"]["max_model_calls"] = 1
    report = await run_local(request_data, sites, local_settings)
    assert report.metrics.model_calls == 1
    assert report.failures[-1].code == "BUDGET_EXHAUSTED"


async def test_runtime_budget(catalog, request_data, sites, local_settings):
    request_data["budgets"]["max_runtime_seconds"] = 1
    report = await run_local(request_data, sites, local_settings, SlowReasoner)
    assert report.failures[-1].code == "BUDGET_EXHAUSTED"
    assert report.session_disposition in {"released", "not_started"}
    assert report.metrics.elapsed_ms < 17000


async def test_pre_cancelled_request_never_starts_browser(request_data, sites, local_settings):
    cancel = asyncio.Event()
    cancel.set()
    report = await run_local(request_data, sites, local_settings, cancel=cancel)
    assert report.outcome == "cancelled"
    assert report.session_ref is None
    assert report.session_disposition == "not_started"
    assert report.metrics.model_calls == 0
