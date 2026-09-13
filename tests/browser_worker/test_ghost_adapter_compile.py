"""Candidate compilation rules that need no browser: a synthetic successful trace."""

from datetime import datetime, timezone

import pytest

from Agents.browser_worker.ghost_adapter import NotCompilable, candidate_request
from Agents.browser_worker.policy import validate_request
from Agents.browser_worker.schemas import (
    Action,
    ActionRecord,
    CheckResult,
    Element,
    ExtractedRecord,
    FieldSource,
    Observation,
    RecordMapping,
    SubtaskReport,
    ValueOrigin,
)

TITLE_RECIPE = {
    "field": "title",
    "attribute": "text",
    "target": {"tag": "h3", "role": "generic", "attributes": {}},
}


def _element(ref, tag, role, name, parent=None, text=""):
    return Element(ref=ref, tag=tag, role=role, name=name, text=text, parent_ref=parent)


def _step(
    obs, kind, target_ref, semantic, *, value=None, origin=None, change="unchanged", records=()
):
    return ActionRecord(
        action_type=kind,
        arguments=Action(
            observation_id=obs.observation_id,
            target_ref=target_ref,
            semantic_target=semantic,
            value=value,
            value_origin=origin,
            url=None,
            expected_change=change,
            reason="test",
            records=list(records),
            failure_code=None,
            visual_question=None,
        ),
        before_observation_id=obs.observation_id,
        after_observation_id=obs.observation_id,
        outcome="succeeded",
        completed_at=datetime.now(timezone.utc),
    )


def _report(task, site, extract_target_ref, before_extract=None):
    """A validated exploration whose only variable is what extract_records pointed at."""
    obs = Observation(
        execution_id="exec-1",
        run_id=task.run_id,
        url=task.start_url or site.start_url,
        title="Catalog",
        content_hash="h",
        visible_text="",
        elements=[
            _element("e1", "input", "textbox", "Search products"),
            _element("e2", "select", "combobox", "Maximum price"),
            _element("e3", "button", "button", "Search", text="Search"),
            _element("e8", "button", "button", ""),  # a control with no accessible name
            _element("e9", "section", "generic", "", text="Results"),  # label-less wrapper
            _element("e10", "article", "generic", "Studio headphones", parent="e9"),
            _element("e11", "h3", "generic", "", parent="e10", text="Studio headphones"),
        ],
    )
    mapping = RecordMapping(
        container_ref="e10",
        fields=[FieldSource(field="title", element_ref="e11", attribute="text")],
    )
    params = task.parameters
    trace = [
        _step(
            obs,
            "fill",
            "e1",
            "search query input",
            value=str(params["query"]),
            origin=ValueOrigin(kind="parameter", key="query"),
            change="value_equals",
        ),
        _step(
            obs,
            "select",
            "e2",
            "maximum price select",
            value=str(params["max_price"]),
            origin=ValueOrigin(kind="parameter", key="max_price"),
            change="value_equals",
        ),
        _step(obs, "click", "e3", "search button", change="results_ready"),
    ]
    if before_extract:
        trace.append(before_extract(obs))
    trace.append(
        _step(
            obs, "extract_records", extract_target_ref, "product result records", records=[mapping]
        )
    )
    return SubtaskReport(
        request_id=task.request_id,
        run_id=task.run_id,
        subtask_id=task.subtask_id,
        outcome="succeeded",
        summary="ok",
        records=[
            ExtractedRecord(
                data={
                    "title": "Studio headphones",
                    "price": 129,
                    "currency": "USD",
                    "url": "http://127.0.0.1:8765/products/studio",
                },
                source_observation_id=obs.observation_id,
                container_ref="e10",
                field_sources=mapping.fields,
                retrieved_at=datetime.now(timezone.utc),
            )
        ],
        validation=[
            CheckResult(
                check_id="records_present",
                passed=True,
                detail="ok",
                evidence_refs=[obs.observation_id],
            )
        ],
        observations=[obs],
        action_trace=trace,
        evidence_refs=[obs.observation_id],
        session_disposition="released",
    )


@pytest.mark.parametrize(
    "extract_target_ref",
    [
        pytest.param(None, id="no-target"),
        pytest.param("e9", id="unnamed-results-wrapper"),
        pytest.param("e10", id="named-result-row"),
    ],
)
def test_extraction_step_never_carries_a_semantic_target(request_data, sites, extract_target_ref):
    """The model may point extract_records at anything; the compiled step must not depend on it.

    A label-less <section id="results"> has no accessible name, so requiring one blocked the
    candidate on every run where the model chose it. A named result row would compile, but
    its name is data ("Studio headphones"), so the replay would fail on any other query.
    Extraction replays through the field recipe over the page's record refs either way.
    """
    task, site = validate_request(request_data, sites)
    body = candidate_request(task, site, _report(task, site, extract_target_ref), "test")
    extract = [s for s in body["trace"] if s["action"] == "extract"]
    assert len(extract) == 1
    assert extract[0]["target"] is None
    assert extract[0]["expected_state"]["fields"] == [TITLE_RECIPE]
    fill = next(s for s in body["trace"] if s["action"] == "fill")
    assert fill["target"]["strategy"] == "semantic"
    assert fill["target"]["role"] == "textbox"
    assert fill["target"]["label"] == "Search products"
    assert fill["input_parameter"] == "query"


def test_unnamed_control_target_is_still_rejected(request_data, sites):
    """Only extraction is exempt: an interaction on a nameless element cannot be replayed."""
    task, site = validate_request(request_data, sites)
    report = _report(
        task,
        site,
        None,
        before_extract=lambda obs: _step(
            obs, "click", "e8", "mystery button", change="results_ready"
        ),
    )
    with pytest.raises(NotCompilable, match="unnamed targets"):
        candidate_request(task, site, report, "test")
