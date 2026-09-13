import copy

import pytest
from pydantic import ValidationError

from Agents.browser_worker.config import Settings
from Agents.browser_worker.demo.run import action
from Agents.browser_worker.policy import guard_url, validate_action, validate_request
from Agents.browser_worker.schemas import Decision, Element, Observation, WorkerError


@pytest.mark.parametrize(
    "update",
    [
        {"schema_version": "0.1"},
        {"request_id": ""},
        {"operation": "buy"},
        {"output_schema_id": "unknown"},
        {"site_id": "unknown"},
        {"parameters": {"query": "headphones", "max_price": "150"}},
        {"parameters": {"query": "headphones", "max_price": 150, "password": "secret"}},
        {"allowed_actions": ["execute_javascript"]},
        {"session": {"ownership": "argus"}},
        {"session": {"ownership": "argus", "session_ref": "wss://secret"}},
        {"budgets": {"max_actions": 0}},
        {"budgets": {"max_model_calls": 10000}},
        {"objective": "Purchase a product"},
        {"prerequisites": [{"subtask_id": "earlier", "status": "pending"}]},
        {"success_conditions": [{"kind": "field_equals", "field": "unknown", "value": "x"}]},
        {"success_conditions": [{"kind": "min_records", "value": "two"}]},
    ],
)
def test_invalid_requests_rejected_before_execution(request_data, sites, update):
    request_data.update(update)
    with pytest.raises(WorkerError):
        validate_request(request_data, sites)


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.example/",
        "http://127.0.0.1.evil.example/",
        "http://127.0.0.1:9876/",
        "http://user:password@127.0.0.1:8765/",
        "javascript:alert(1)",
        "file:///etc/passwd",
        "http://127.0.0.1:8765/admin",
        "http://127.0.0.1:8765/?token=secret",
        "http://127.0.0.1:8765/products/%2e%2e/admin",
        "http://127.0.0.1:8765/#secret",
        "http://127.0.0.1:8765/products/../admin",
    ],
)
def test_domain_escape_prevention(request_data, sites, url):
    task, site = validate_request(request_data, sites)
    with pytest.raises(WorkerError, match="outside"):
        guard_url(url, site, task)


def observation():
    return Observation(
        execution_id="execution",
        run_id="run",
        url="http://127.0.0.1:8765/",
        title="Catalog",
        content_hash="hash",
        visible_text="Search",
        elements=[
            Element(
                ref="e1",
                tag="input",
                role="textbox",
                name="Search products",
                text="",
                permitted_actions=["fill"],
                attributes={"bound_parameter": "query"},
            )
        ],
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"observation_id": "old"}, "STALE_OBSERVATION"),
        ({"target_ref": "e99"}, "TARGET_NOT_FOUND"),
        ({"value": "invented"}, "ACTION_REJECTED"),
        ({"value_origin": {"kind": "parameter", "key": "missing"}}, "ACTION_REJECTED"),
    ],
)
def test_action_validation(request_data, sites, change, code):
    task, site = validate_request(request_data, sites)
    obs = observation()
    args = action(
        obs.model_dump(),
        target_ref="e1",
        semantic_target="Search products",
        value="headphones",
        value_origin={"kind": "parameter", "key": "query"},
    ).model_dump()
    args.update(change)
    decision = Decision.model_validate({"action_type": "fill", "arguments": args})
    with pytest.raises(WorkerError) as error:
        validate_action(decision, task, site, obs)
    assert error.value.failure.code == code


def test_ambiguous_semantic_target(request_data, sites):
    task, site = validate_request(request_data, sites)
    obs = observation()
    second = copy.deepcopy(obs.elements[0])
    second.ref = "e2"
    obs.elements.append(second)
    decision = Decision(
        action_type="fill",
        arguments=action(
            obs.model_dump(),
            target_ref="e1",
            value="headphones",
            semantic_target="Search products",
            value_origin={"kind": "parameter", "key": "query"},
        ),
    )
    with pytest.raises(WorkerError) as error:
        validate_action(decision, task, site, obs)
    assert error.value.failure.code == "TARGET_AMBIGUOUS"


def test_request_cannot_widen_site_permission(request_data, sites):
    task, site = validate_request(request_data, sites)
    obs = observation()
    decision = Decision(action_type="click", arguments=action(obs.model_dump(), target_ref="e1"))
    with pytest.raises(WorkerError):
        validate_action(decision, task, site, obs)


def test_config_preserves_requested_model():
    assert Settings().model == "gpt-5.4"
    assert Settings(model="gpt-5.4-2026-03-05").reasoning_effort == "medium"
    with pytest.raises(ValidationError):
        Settings(model="some-other-model")
