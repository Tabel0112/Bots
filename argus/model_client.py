"""The one boundary between ARGUS and a language model.

Every model call in the controller - stage 1 interpretation and the open-world
planner - goes through :class:`ModelClient`.  Nothing else in ``argus`` imports
a vendor SDK, so swapping the backend is a change here and nowhere else.
See the "Model access for ARGUS" entry in ``docs/ai/DECISIONS.md``: the team
connects models through OpenAI-compatible APIs.

What the boundary promises its callers:

* One method, :meth:`ModelClient.parse_json`, which asks for JSON that matches a
  pydantic model and hands back a :class:`ModelResult`.  The backend enforces
  the model's JSON schema, so a caller never parses free text.
* Four outcomes, never an SDK-shaped surprise: ``ok``, ``refusal``,
  ``truncated`` and ``invalid``.  The caller maps them to its own typed errors
  (:class:`~argus.contracts.ContractError` codes ``MODEL_REFUSED`` and
  ``EXTRACTION_FAILED``); it never inspects a ``finish_reason`` itself.
* A refusal is detected before any content is read.  ``parsed`` is ``None`` on
  every non-``ok`` status, so there is nothing to accidentally trust.

Environment (:class:`OpenAICompatibleClient`): ``OPENAI_API_KEY`` and
``OPENAI_BASE_URL`` are read by the ``openai`` SDK itself - this module does not
re-parse them - and ``ARGUS_MODEL`` names the model.  There is no default model:
a run that names none fails with ``PRECONDITION_FAILED`` rather than silently
calling something the team did not choose.  The SDK is imported inside the
constructor, so importing this module - and the rest of ``argus`` - works
without it installed; tests inject a fake instead of calling the network.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from argus.contracts import ContractError

__all__ = [
    "API_KEY_ENV_VAR",
    "BASE_URL_ENV_VAR",
    "MODEL_ENV_VAR",
    "MODEL_STATUSES",
    "ModelClient",
    "ModelResult",
    "OpenAICompatibleClient",
]

#: Names the model to call.  Deliberately without a default; see the module doc.
MODEL_ENV_VAR = "ARGUS_MODEL"

#: Read by the ``openai`` SDK, not by this module.  Named here for documentation
#: and so the CLI and README can point at one place.
API_KEY_ENV_VAR = "OPENAI_API_KEY"
BASE_URL_ENV_VAR = "OPENAI_BASE_URL"

#: The four outcomes of a structured-output call.
#:
#: ``ok``         the backend returned JSON that validated against the model.
#: ``refusal``    the model declined; there is no content to read.
#: ``truncated``  the output limit was reached, so the JSON is incomplete.
#: ``invalid``    something came back but it is not the requested shape.
MODEL_STATUSES = ("ok", "refusal", "truncated", "invalid")


@dataclass
class ModelResult:
    """One structured-output call's outcome.

    ``parsed`` is the validated model's ``model_dump()`` and is only ever set
    when ``status`` is ``"ok"``.  ``raw_text`` is whatever text the backend gave
    alongside it - the refusal sentence, the truncated JSON, the unparseable
    body - kept for the error message and the run log, never for parsing.
    ``model`` is the model that answered, as the backend reported it.
    """

    status: str
    parsed: dict[str, Any] | None = None
    raw_text: str | None = None
    model: str = field(default="")

    def __post_init__(self) -> None:
        if self.status not in MODEL_STATUSES:
            raise ContractError(
                f"unknown model result status {self.status!r}; expected one of "
                f"{', '.join(MODEL_STATUSES)}"
            )
        if self.parsed is not None and not isinstance(self.parsed, dict):
            raise ContractError("ModelResult.parsed must be a dict or None")
        if self.status != "ok" and self.parsed is not None:
            raise ContractError(
                f"a {self.status!r} result must not carry parsed content"
            )
        if not isinstance(self.model, str):
            raise ContractError("ModelResult.model must be a string")


@runtime_checkable
class ModelClient(Protocol):
    """Ask a model for JSON matching ``output_model``.

    ``system`` is the instructions, ``user`` the request.  ``output_model`` is a
    pydantic ``BaseModel`` subclass whose JSON schema the backend enforces, so
    an implementation returns ``parsed`` only when the response validated
    against it.  ``max_tokens`` bounds the output; reaching it is reported as
    ``truncated``, never as a short answer the caller might trust.

    An implementation returns a :class:`ModelResult` for anything the model did
    (including refusing) and raises only for what it could not attempt at all -
    a missing SDK, a missing model name, a transport failure.
    """

    def parse_json(
        self, system: str, user: str, output_model: type, max_tokens: int
    ) -> ModelResult:  # pragma: no cover - a Protocol has no behaviour
        ...


# --------------------------------------------------------------------------- #
# The OpenAI-compatible backend
# --------------------------------------------------------------------------- #


_SDK_ERRORS: dict[str, tuple[type[BaseException], ...]] | None = None


def _sdk_errors() -> dict[str, tuple[type[BaseException], ...]]:
    """The SDK exceptions that are really statuses, resolved once and cached.

    ``openai`` raises rather than returns for two of the four outcomes:
    ``LengthFinishReasonError`` when ``finish_reason`` was ``"length"`` and
    ``ContentFilterFinishReasonError`` when the backend's filter stopped the
    response.  Catching them here is what keeps those outcomes statuses instead
    of exceptions escaping the boundary.  An empty tuple - the SDK is not
    installed, or an older one lacks the class - never matches, which is exactly
    the behaviour wanted when a fake SDK is injected instead.
    """
    global _SDK_ERRORS
    if _SDK_ERRORS is None:
        truncated: tuple[type[BaseException], ...] = ()
        refused: tuple[type[BaseException], ...] = ()
        try:
            import openai
        except ImportError:  # pragma: no cover - depends on the environment
            pass
        else:
            length = getattr(openai, "LengthFinishReasonError", None)
            content_filter = getattr(openai, "ContentFilterFinishReasonError", None)
            if isinstance(length, type) and issubclass(length, BaseException):
                truncated = (length,)
            if isinstance(content_filter, type) and issubclass(
                content_filter, BaseException
            ):
                refused = (content_filter,)
        _SDK_ERRORS = {"truncated": truncated, "refusal": refused}
    return _SDK_ERRORS


class OpenAICompatibleClient:
    """:class:`ModelClient` over any OpenAI-compatible chat completions endpoint.

    Works against OpenAI itself and against a self-hosted server that speaks the
    same API (the team's local vLLM box), because the only call made is
    ``chat.completions.parse`` with ``response_format`` set to the pydantic
    model - the structured-output helper in current ``openai-python``.

    ``api_key`` and ``base_url`` are passed to the SDK only when given; omitted,
    the SDK reads ``OPENAI_API_KEY`` and ``OPENAI_BASE_URL`` itself, so there is
    one parser for them and it is not this one.  ``model`` falls back to
    ``$ARGUS_MODEL`` and then fails: there is no default.

    ``_sdk`` is a test seam.  Passing an object shaped like an ``openai.OpenAI``
    client (``.chat.completions.parse``) skips both the import and the
    credential check, which is how the tests exercise every status offline.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        *,
        _sdk: Any = None,
    ) -> None:
        resolved = model or os.environ.get(MODEL_ENV_VAR) or ""
        if not resolved.strip():
            raise ContractError(
                f"no model was named for the call: pass model= or set "
                f"${MODEL_ENV_VAR}. There is no default model, so that a run "
                f"never quietly calls one the team did not choose.",
                code="PRECONDITION_FAILED",
            )
        self.model = resolved.strip()

        if _sdk is not None:
            self._sdk = _sdk
            return

        try:
            import openai
        except ImportError as exc:
            raise ContractError(
                "this call needs the openai package: pip install openai",
                code="PRECONDITION_FAILED",
            ) from exc

        options: dict[str, Any] = {}
        if api_key is not None:
            options["api_key"] = api_key
        if base_url is not None:
            options["base_url"] = base_url
        try:
            self._sdk = openai.OpenAI(**options)
        except openai.OpenAIError as exc:
            # Almost always "the api_key client option must be set": a missing
            # credential, which is a precondition, not a model failure.
            raise ContractError(
                f"the openai client could not be created: {exc}",
                code="PRECONDITION_FAILED",
            ) from exc

    # -- the call ---------------------------------------------------------- #

    def parse_json(
        self, system: str, user: str, output_model: type, max_tokens: int
    ) -> ModelResult:
        """One bounded structured-output call; see :class:`ModelClient`."""
        if not hasattr(output_model, "model_validate"):
            raise ContractError(
                "output_model must be a pydantic BaseModel subclass",
                code="INVALID_INPUT",
            )
        errors = _sdk_errors()
        try:
            completion = self._sdk.chat.completions.parse(
                model=self.model,
                max_completion_tokens=max_tokens,
                response_format=output_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            )
        except errors["truncated"]:
            return ModelResult(status="truncated", model=self.model)
        except errors["refusal"] as exc:
            return ModelResult(
                status="refusal", raw_text=str(exc) or None, model=self.model
            )
        except (ValueError, TypeError) as exc:
            # pydantic's ValidationError and json's JSONDecodeError are both
            # ValueError: the backend answered, but not in the asked-for shape.
            # Transport and authentication failures are not caught; they are not
            # something the model did, so they stay exceptions.
            return ModelResult(status="invalid", raw_text=str(exc), model=self.model)

        return self._read(completion, output_model)

    def _read(self, completion: Any, output_model: type) -> ModelResult:
        """Turn one completion into a :class:`ModelResult`, refusal first."""
        model = getattr(completion, "model", None) or self.model
        if not isinstance(model, str) or not model:
            model = self.model

        choices = getattr(completion, "choices", None) or []
        if not choices:
            return ModelResult(status="invalid", raw_text=None, model=model)
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            return ModelResult(status="invalid", raw_text=None, model=model)

        # First, before a single byte of content is read: did it decline?
        refusal = getattr(message, "refusal", None)
        if refusal:
            return ModelResult(status="refusal", raw_text=str(refusal), model=model)

        # Then: was it cut off?  Current openai-python raises
        # LengthFinishReasonError instead of returning this, but an
        # OpenAI-compatible server reached through a thinner client may not.
        if getattr(choice, "finish_reason", None) == "length":
            return ModelResult(
                status="truncated",
                raw_text=_text_of(message),
                model=model,
            )

        parsed = getattr(message, "parsed", None)
        if parsed is None:
            return ModelResult(
                status="invalid", raw_text=_text_of(message), model=model
            )

        try:
            if isinstance(parsed, output_model):
                payload = parsed.model_dump()
            else:
                payload = output_model.model_validate(parsed).model_dump()
        except (ValueError, TypeError) as exc:
            return ModelResult(status="invalid", raw_text=str(exc), model=model)

        if not isinstance(payload, dict):  # pragma: no cover - pydantic returns a dict
            return ModelResult(
                status="invalid", raw_text=_text_of(message), model=model
            )

        return ModelResult(
            status="ok", parsed=payload, raw_text=_text_of(message), model=model
        )


def _text_of(message: Any) -> str | None:
    """The message's text, if it has any; only ever read after the refusal check."""
    content = getattr(message, "content", None)
    return content if isinstance(content, str) else None
