"""Offline visual connection tests. No Steel or model requests are made."""

import base64
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from Agents.browser_worker.policy import validate_request
from Agents.browser_worker.schemas import SubtaskReport
from Agents.visual.browser_subagent.ghost_adapter import normalize_visual_trace, run_visual_handoff
from Agents.visual.browser_subagent.steel_browser import SteelBrowser
from Agents.visual.browser_subagent.uitars_agent import UITarsSubagent


def test_attached_visual_session_disconnects_without_release():
    browser = object.__new__(SteelBrowser)
    browser.client = MagicMock()
    browser._browser = MagicMock()
    browser._pw = MagicMock()
    browser._page = None
    browser.task = None
    browser.owns_session = False
    browser.session_ref = "existing-session"
    assert browser.stop() == "retained"
    browser.client.sessions.release.assert_not_called()
    browser._browser.close.assert_not_called()
    browser._pw.stop.assert_called_once()


def test_visual_trace_rejects_coordinates_and_failed_actions(tmp_path):
    visual = {
        "actions": [
            {
                "action": {"name": "click"},
                "outcome": "succeeded",
                "ghost_action": {"action": "click", "target": None},
            }
        ]
    }
    with pytest.raises(ValueError, match="semantic"):
        normalize_visual_trace(visual, tmp_path)
    visual["actions"][0]["outcome"] = "failed"
    with pytest.raises(ValueError, match="failed"):
        normalize_visual_trace(visual, tmp_path)


async def test_existing_uitars_loop_consumes_dom_handoff(monkeypatch, request_data, sites):
    import asyncio

    import Agents.visual.browser_subagent.steel_browser as module

    task, site = validate_request(request_data, sites)
    task = task.model_copy(
        update={
            "session": task.session.model_copy(
                update={"ownership": "argus", "session_ref": "shared"}
            ),
            "start_url": None,
        }
    )
    site = site.model_copy(update={"extraction_fields": []})
    calls = []

    class Browser:
        def __init__(self, **kwargs):
            calls.append(("attach", kwargs["session_ref"]))
            self.viewer_url, self.current_url = (
                "https://app.steel.dev/sessions/shared",
                site.start_url,
            )
            self.network_log = []
            self.ghost_action = None

        def start(self):
            return SimpleNamespace(id="shared", session_viewer_url=self.viewer_url)

        def screenshot_b64(self):
            return base64.b64encode(b"test-screenshot").decode()

        def stop(self):
            calls.append(("disconnect", "shared"))
            return "retained"

    class Agent(UITarsSubagent):
        def _chat(self, messages):
            self._model_calls += 1
            return "Thought: Read the canvas.\nAction: finished(content='Canvas shows 42.')"

    monkeypatch.setattr(module, "SteelBrowser", Browser)
    dom = SubtaskReport(
        request_id=task.request_id,
        run_id=task.run_id,
        subtask_id=task.subtask_id,
        outcome="needs_visual",
        session_ref="shared",
        session_disposition="retained",
    )
    report = await run_visual_handoff(task, site, dom, asyncio.Event(), agent_factory=Agent)
    assert calls == [("attach", "shared"), ("disconnect", "shared")]
    assert report.summary == "Canvas shows 42."
    assert report.outcome == "inconclusive"
    assert report.visual_report["actions"][-1]["action"]["name"] == "finished"
    assert report.metrics.model_calls == 1
    assert report.session_disposition == "retained"


def test_typed_visual_inputs_capture_origins(request_data, sites):
    task, site = validate_request(request_data, sites)
    browser = object.__new__(SteelBrowser)
    browser.task, browser.site, browser.view_offset = task, site, (0, 0)
    browser._page = MagicMock()
    browser._page.evaluate.side_effect = [
        True,
        [{"parameter": "query", "selector": "input[name=q]"}],
    ]
    browser._page.locator.return_value.bounding_box.return_value = {
        "x": 1,
        "y": 1,
        "width": 10,
        "height": 10,
    }
    browser.element_at = lambda *args: {"role": "textbox", "label": "Search products"}
    browser._guard_action("type", {"text": "headphones"})
    assert browser.ghost_action["input_parameter"] == "query"
    assert browser.ghost_action["action"] == "fill"
    browser._page.evaluate.side_effect = [
        True,
        [{"parameter": "query", "selector": "input[name=q]"}],
    ]
    with pytest.raises(ValueError):
        browser._guard_action("type", {"text": "unrequested value"})


def test_visual_trace_normalizes_time_and_embeds_evidence(tmp_path):
    before, after = tmp_path / "before.png", tmp_path / "after.png"
    before.write_bytes(b"synthetic-before")
    after.write_bytes(b"synthetic-after")
    visual = {
        "actions": [
            {
                "step_id": "step-001",
                "action": {"name": "type"},
                "ghost_action": {
                    "action": "fill",
                    "target": {"strategy": "semantic", "role": "textbox", "label": "Search"},
                    "value": "headphones",
                    "input_parameter": "query",
                    "expected_state": {"change": "value_equals"},
                },
                "observation_before": str(before),
                "observation_after": str(after),
                "timestamp": 1789237040.0,
                "outcome": "succeeded",
            }
        ]
    }
    trace, artifacts = normalize_visual_trace(visual, tmp_path)
    assert trace[0]["timestamp"].endswith("+00:00")
    assert trace[0]["input_parameter"] == "query"
    assert trace[0]["observation_before"] in artifacts
    assert len(artifacts) == 2
    assert (
        base64.b64decode(artifacts[trace[0]["observation_after"]]["base64"]) == b"synthetic-after"
    )
