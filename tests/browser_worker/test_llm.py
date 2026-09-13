from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import APITimeoutError

from Agents.browser_worker.config import Settings
from Agents.browser_worker.demo.run import action
from Agents.browser_worker.llm import OpenAIReasoner
from Agents.browser_worker.prompts import function_tools
from Agents.browser_worker.schemas import WorkerError


def response(arguments=None, name="report_success", status="completed", count=1):
    arguments = (
        arguments
        if arguments is not None
        else action({"observation_id": "latest"}).model_dump_json()
    )
    return SimpleNamespace(
        status=status,
        usage=SimpleNamespace(input_tokens=100, output_tokens=50),
        output=[SimpleNamespace(type="function_call", name=name, arguments=arguments)] * count,
    )


async def test_responses_strict_one_call_settings():
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=response())))
    reasoner = OpenAIReasoner(Settings(), client)
    decision = await reasoner.decide({"observation": {"observation_id": "latest"}})
    assert decision.action_type == "report_success"
    kwargs = client.responses.create.call_args.kwargs
    assert kwargs["model"] == "gpt-5.4"
    assert kwargs["reasoning"] == {"effort": "medium"}
    assert kwargs["parallel_tool_calls"] is False
    assert kwargs["tool_choice"] == "required"
    assert kwargs["store"] is False
    assert kwargs["max_output_tokens"] == 4096
    assert reasoner.input_tokens == 100 and reasoner.output_tokens == 50

    def strict(schema):
        if isinstance(schema, dict):
            if schema.get("type") == "object":
                assert schema["additionalProperties"] is False
                assert set(schema["required"]) == set(schema["properties"])
            for v in schema.values():
                strict(v)
        elif isinstance(schema, list):
            for v in schema:
                strict(v)

    for tool in function_tools():
        assert tool["strict"] is True
        strict(tool["parameters"])


@pytest.mark.parametrize(
    "reply",
    [
        response(arguments="{not json"),
        response(arguments='{"code":"steal"}'),
        response(name="arbitrary_javascript"),
        response(status="incomplete"),
        response(count=0),
        response(count=2),
    ],
)
async def test_malformed_tool_calls_are_retryable(reply):
    client = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock(return_value=reply)))
    with pytest.raises(WorkerError) as err:
        await OpenAIReasoner(Settings(), client).decide({})
    assert err.value.failure.code == "MODEL_ERROR" and err.value.failure.retryable


async def test_api_timeout_is_sanitized():
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(
                side_effect=APITimeoutError(
                    request=httpx.Request("POST", "https://example.com/?apiKey=secret")
                )
            )
        )
    )
    with pytest.raises(WorkerError) as err:
        await OpenAIReasoner(Settings(), client).decide({})
    assert err.value.failure.code == "MODEL_TIMEOUT"
    assert "secret" not in str(err.value)


async def test_malformed_then_valid_recovery():
    client = SimpleNamespace(
        responses=SimpleNamespace(
            create=AsyncMock(side_effect=[response(arguments="oops"), response()])
        )
    )
    reasoner = OpenAIReasoner(Settings(), client)
    with pytest.raises(WorkerError):
        await reasoner.decide({})
    assert (
        await reasoner.decide({"recent_errors": [{"code": "MODEL_ERROR"}]})
    ).action_type == "report_success"
    assert reasoner.input_tokens == 200
