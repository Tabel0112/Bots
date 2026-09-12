from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from browser_worker.browser.adapter import BrowserAdapter
from browser_worker.config import Settings
from browser_worker.policy import validate_request


@pytest.mark.parametrize(
    "ownership,close_on_finish,expected_release,disposition",
    [
        ("argus", False, 0, "retained"),
        ("argus", True, 1, "released"),
        ("worker", False, 1, "released"),
    ],
)
async def test_session_ownership_cleanup(
    request_data, sites, ownership, close_on_finish, expected_release, disposition
):
    request_data["session"] = {"ownership": ownership, "close_on_finish": close_on_finish}
    if ownership == "argus":
        request_data["session"]["session_ref"] = "opaque-session"
    task, site = validate_request(request_data, sites)
    adapter = BrowserAdapter(task, site, Settings())
    adapter.session_ref = "opaque-session"
    adapter.steel = SimpleNamespace(
        sessions=SimpleNamespace(release=AsyncMock()), close=AsyncMock()
    )
    adapter.browser = SimpleNamespace(close=AsyncMock())
    adapter.playwright = SimpleNamespace(stop=AsyncMock())
    assert await adapter.close() == disposition
    assert adapter.steel.sessions.release.await_count == expected_release
    adapter.browser.close.assert_not_called()
    adapter.playwright.stop.assert_awaited_once()


async def test_cleanup_failure_is_reported(request_data, sites):
    task, site = validate_request(request_data, sites)
    adapter = BrowserAdapter(task, site, Settings())
    adapter.session_ref = "owned-session"
    adapter.steel = SimpleNamespace(
        sessions=SimpleNamespace(release=AsyncMock(side_effect=RuntimeError("secret"))),
        close=AsyncMock(),
    )
    assert await adapter.close() == "cleanup_failed"
    adapter.steel.close.assert_awaited_once()
