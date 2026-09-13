"""One Responses function call per step; SDK retries disabled so budgets stay honest."""

import json
import os
from typing import Protocol

from openai import APIError, APITimeoutError, AsyncOpenAI

from .config import Settings
from .prompts import INSTRUCTIONS, function_tools
from .schemas import Decision, WorkerError
from .schemas import FailureCode as C


class Reasoner(Protocol):
    async def decide(self, context: dict) -> Decision: ...


class OpenAIReasoner:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self.model_id = settings.model
        self.client = client
        self.input_tokens = self.output_tokens = None

    async def decide(self, context: dict) -> Decision:
        if self.client is None:
            if not os.getenv("OPENAI_API_KEY"):
                raise WorkerError(C.MODEL_ERROR, "OPENAI_API_KEY is not configured.")
            self.client = AsyncOpenAI(max_retries=0, timeout=self.settings.model_timeout_seconds)
        try:
            response = await self.client.responses.create(
                model=self.settings.model,
                reasoning={"effort": self.settings.reasoning_effort},
                instructions=INSTRUCTIONS,
                input=[{"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                tools=function_tools(),
                tool_choice="required",
                parallel_tool_calls=False,
                max_output_tokens=self.settings.max_output_tokens,
                store=False,
            )
        except APITimeoutError:
            raise WorkerError(C.MODEL_TIMEOUT, "Model request timed out.", True) from None
        except APIError as exc:
            retryable = getattr(exc, "status_code", None) in {None, 408, 429, 500, 502, 503, 504}
            raise WorkerError(C.MODEL_ERROR, "Model API request failed.", retryable) from None
        if response.usage:
            self.input_tokens = (self.input_tokens or 0) + response.usage.input_tokens
            self.output_tokens = (self.output_tokens or 0) + response.usage.output_tokens
        calls = [item for item in response.output if item.type == "function_call"]
        if response.status != "completed" or len(calls) != 1:
            raise WorkerError(C.MODEL_ERROR, "Expected exactly one complete function call.", True)
        try:
            args = json.loads(calls[0].arguments)
            return Decision.model_validate({"action_type": calls[0].name, "arguments": args})
        except (ValueError, TypeError):
            raise WorkerError(
                C.MODEL_ERROR, "Malformed function arguments; use the strict schema.", True
            ) from None

    async def close(self):
        if self.client:
            await self.client.close()
