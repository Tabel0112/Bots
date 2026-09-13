from __future__ import annotations

import json
from pathlib import Path

import pytest

from Agents.browser_worker.demo.run import action, catalog_server
from Agents.browser_worker.policy import validate_action, validate_request
from Agents.browser_worker.runner import Worker
from Agents.browser_worker.schemas import (
    Decision,
    FieldSource,
    RecordMapping,
    ValueOrigin,
    WorkerError,
)

pytestmark = pytest.mark.browser


def open_request(port: int) -> dict:
    origin = f"http://127.0.0.1:{port}"
    return {
        "schema_version": "0.2",
        "request_id": "request-open-test",
        "run_id": "run-open-test",
        "subtask_id": "open-catalog-test",
        "objective": "Search the public catalog for headphones.",
        "site_id": "open:127.0.0.1",
        "operation": "open_search",
        "start_url": origin + "/",
        "session": {"ownership": "worker"},
        "parameters": {"query": "headphones", "max_price": 150},
        "output_schema_id": "open-records.v1",
        "success_conditions": [],
        "allowed_actions": [
            "navigate",
            "fill",
            "select",
            "click",
            "wait_for",
            "inspect_element",
            "extract_records",
            "report_success",
            "report_failure",
            "request_visual_fallback",
        ],
        "allowed_domains": ["127.0.0.1"],
        "allowed_url_patterns": [origin + "/*"],
        "budgets": {
            "max_actions": 20,
            "max_model_calls": 30,
            "max_retries": 3,
            "max_runtime_seconds": 30,
            "no_progress_limit": 4,
        },
        "required_evidence": ["observations", "action_trace", "field_sources"],
        "prerequisites": [],
        "visual_fallback_available": True,
        "expected_record_shape": ["title", "price", "currency", "url"],
    }


class OpenCatalogReasoner:
    model_id = "scripted_open_fixture"
    fill_query = True

    def __init__(self, settings=None):
        self.clicked = False
        self.extracted = False

    async def decide(self, context):
        obs = context["observation"]
        params = context["task"]["parameters"]
        elements = obs["elements"]
        query = next(element for element in elements if element["input_type"] == "search")
        if self.fill_query and query["value"] != params["query"]:
            return Decision(
                action_type="fill",
                arguments=action(
                    obs,
                    target_ref=query["ref"],
                    semantic_target=query["name"],
                    value=params["query"],
                    value_origin=ValueOrigin(kind="parameter", key="query"),
                    expected_change="value_equals",
                ),
            )
        price = next(element for element in elements if element["role"] == "combobox")
        if price["value"] != str(params["max_price"]):
            return Decision(
                action_type="select",
                arguments=action(
                    obs,
                    target_ref=price["ref"],
                    semantic_target=price["name"],
                    value=str(params["max_price"]),
                    value_origin=ValueOrigin(kind="parameter", key="max_price"),
                    expected_change="value_equals",
                ),
            )
        if not self.clicked:
            self.clicked = True
            button = next(element for element in elements if element["role"] == "button")
            return Decision(
                action_type="click",
                arguments=action(
                    obs,
                    target_ref=button["ref"],
                    semantic_target=button["name"],
                    expected_change="changed",
                ),
            )
        if not self.extracted:
            self.extracted = True
            mappings = []
            for container in (element for element in elements if element["role"] == "article"):
                children = [
                    element for element in elements if element["parent_ref"] == container["ref"]
                ]
                mappings.append(
                    RecordMapping(
                        container_ref=container["ref"],
                        fields=[
                            FieldSource(
                                field="title",
                                element_ref=next(
                                    element["ref"] for element in children if element["tag"] == "h3"
                                ),
                                attribute="text",
                            ),
                            FieldSource(
                                field="price",
                                element_ref=next(
                                    element["ref"]
                                    for element in children
                                    if element["attributes"].get("data-testid") == "price"
                                ),
                                attribute="text",
                            ),
                            FieldSource(
                                field="currency",
                                element_ref=next(
                                    element["ref"]
                                    for element in children
                                    if element["attributes"].get("data-testid") == "currency"
                                ),
                                attribute="text",
                            ),
                            FieldSource(
                                field="url",
                                element_ref=next(
                                    element["ref"]
                                    for element in children
                                    if element["role"] == "link"
                                ),
                                attribute="href",
                            ),
                        ],
                    )
                )
            return Decision(action_type="extract_records", arguments=action(obs, records=mappings))
        return Decision(action_type="report_success", arguments=action(obs))


class NeverFillQuery(OpenCatalogReasoner):
    fill_query = False


async def run_open(local_settings, reasoner=OpenCatalogReasoner):
    with catalog_server(0) as server:
        request = open_request(server.server_address[1])
        report = await Worker(sites={}, settings=local_settings, reasoner_factory=reasoner).run(
            request
        )
    return request, report


def test_open_request_validates_without_configured_site():
    request = open_request(8765)
    task, site = validate_request(request, {})
    assert task.operation == "open_search"
    assert site.open_site
    assert site.results_selector is None
    assert site.record_schema["properties"]["price"]["type"] == "number"


async def test_open_search_allows_generic_controls_and_preserves_provenance(local_settings):
    request, report = await run_open(local_settings)
    assert report.outcome == "succeeded", report.model_dump_json()
    assert [record.data["title"] for record in report.records] == [
        "Studio headphones",
        "Travel headphones",
    ]
    assert all(record.field_sources for record in report.records)
    assert all(record.source_observation_id for record in report.records)
    assert all(
        trace.outcome == "succeeded"
        for trace in report.action_trace
        if trace.action_type in {"fill", "select", "click"}
    )
    checks = {check.check_id: check for check in report.validation}
    assert checks["query_applied"].passed
    assert set(checks) == {
        "fresh_extraction",
        "current_run",
        "output_schema",
        "field_provenance",
        "approved_links",
        "query_applied",
    }
    assert "applied:max_price: not applicable on an open site" in report.limitations
    assert not any(
        check.check_id == "visible_record_coverage" and check.passed for check in report.validation
    )
    assert report.final_url == request["start_url"]


async def test_missing_query_fill_is_inconclusive(local_settings):
    _, report = await run_open(local_settings, NeverFillQuery)
    assert report.outcome == "inconclusive"
    assert not next(
        check for check in report.validation if check.check_id == "query_applied"
    ).passed
    assert report.failures[-1].code == "VALIDATION_FAILED"


def test_open_search_rejects_off_domain_navigation():
    request = open_request(8765)
    task, site = validate_request(request, {})
    from Agents.browser_worker.schemas import Observation

    observation = Observation(
        execution_id="execution",
        run_id=task.run_id,
        url=request["start_url"],
        title="Catalog",
        content_hash="hash",
        visible_text="Catalog",
        elements=[],
    )
    decision = Decision(
        action_type="navigate",
        arguments=action(observation.model_dump(mode="json"), url="https://example.net/"),
    )
    with pytest.raises(WorkerError) as caught:
        validate_action(decision, task, site, observation)
    assert caught.value.failure.code == "DOMAIN_NOT_ALLOWED"


def test_open_site_allows_non_sensitive_query_keys_and_blocks_sensitive_ones():
    from Agents.browser_worker import policy as policy_module
    from Agents.browser_worker.schemas import WorkerError

    raw = json.loads(
        (
            Path(__file__).resolve().parents[2] / "Agents/browser_worker/examples/open_search.json"
        ).read_text()
    )
    task, site = policy_module.validate_request(raw, {})
    host = site.allowed_domains[0]
    policy_module.guard_url(
        f"https://{host}/w/index.php?search=toronto&title=Special:Search", site, task
    )
    try:
        policy_module.guard_url(f"https://{host}/w/index.php?token=abc", site, task)
    except WorkerError:
        pass
    else:
        raise AssertionError("sensitive query key must be rejected on open sites")
