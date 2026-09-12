"""Stage 1: turn a request into an :class:`~argus.contracts.InterpretedRequest`.

This is the only model-backed step the controller owns.  It asks Claude to map
free text onto the sites and operations in :mod:`argus.registry`, and it returns
the handoff artifact every later stage and every teammate reads.

What this module guarantees, whatever the model returns:

* The response is schema-valid JSON.  ``client.messages.parse`` is called with a
  pydantic output model that mirrors the ``InterpretedRequest`` shape, so the SDK
  validates before this module sees anything.
* A refusal never reaches the content.  ``stop_reason`` is checked first and a
  ``refusal`` raises :class:`~argus.contracts.ContractError` with the typed code
  ``MODEL_REFUSED``.
* Every ``text_span`` really is a span of the request.  A span that does not
  match the raw text is dropped: that parameter's source becomes ``structured``
  with confidence ``0`` and the discrepancy is recorded in ``ambiguities`` so the
  gate (stage 2) can see it instead of a fabricated citation.

What it deliberately does not do: it does not judge the request.  An unknown
site, an unsupported operation or a missing parameter is reported faithfully and
:mod:`argus.gate` decides.  It also does not fill in registry defaults; the
planner (stage 3) does that from :func:`argus.registry.defaults`.

Environment: ``ANTHROPIC_API_KEY`` for the SDK, ``ARGUS_MODEL`` to override the
model (default :data:`DEFAULT_MODEL`).  The ``anthropic`` SDK is imported lazily
inside the call, so importing this module - and the rest of ``argus`` - works
without it installed; tests inject a fake client instead of calling the network.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Mapping

from argus import registry
from argus.contracts import (
    PARAMETER_SOURCES,
    ContractError,
    Intent,
    InterpretedRequest,
    MissingParameter,
    ParameterOrigin,
)

__all__ = [
    "DEFAULT_MODEL",
    "MAX_TOKENS",
    "MODEL_ENV_VAR",
    "interpret",
    "system_prompt",
]

#: Model used when neither the caller nor ``ARGUS_MODEL`` names one.
DEFAULT_MODEL = "claude-opus-5"

#: Environment variable that overrides :data:`DEFAULT_MODEL`.
MODEL_ENV_VAR = "ARGUS_MODEL"

#: Interpretation is a small JSON document; this is ample and bounds the call.
MAX_TOKENS = 4096

_SENTINEL = object()


# --------------------------------------------------------------------------- #
# The prompt
# --------------------------------------------------------------------------- #


def _parameter_line(name: str, rule: Mapping[str, Any]) -> str:
    parts = [f"{rule['type']}", "required" if rule.get("required") else "optional"]
    if "default" in rule:
        parts.append(f"default {rule['default']!r}")
    if "fixed" in rule:
        parts.append(f"fixed at {rule['fixed']!r}")
    if "minimum" in rule:
        parts.append(f"minimum {rule['minimum']}")
    if "maximum" in rule:
        parts.append(f"maximum {rule['maximum']}")
    return f"      - {name}: {', '.join(parts)}. {rule.get('description', '')}".rstrip()


def system_prompt() -> str:
    """The instructions sent with every interpretation.

    Built from :mod:`argus.registry` so adding a site or an operation there
    changes the prompt with no edit here.  Exposed for tests and for review.
    """
    lines = [
        "You are the interpreter of ARGUS, a controller that runs browser tasks "
        "against a fixed set of sites. You do not browse and you do not act. You "
        "read one request and report what it asks for, in JSON.",
        "",
        "Supported sites:",
    ]
    for site in registry.SITES.values():
        lines.append(
            f"  - {site['site_id']} ({site['label']}) at {site['origin']}: "
            f"{site['description']}"
        )
        lines.append(f"    operations: {', '.join(site['operations'])}")
    lines.append("")
    lines.append("Supported operations:")
    for spec in registry.OPERATIONS.values():
        lines.append(
            f"  - {spec['operation']} on site {spec['site_id']} "
            f"({spec['action_class']}): {spec['description']}"
        )
        lines.append("    parameters:")
        for name, rule in spec["parameters"].items():
            lines.append(_parameter_line(name, rule))
    lines.extend(
        [
            "",
            "Rules:",
            "1. Split a compound request into one intent per site operation. "
            '"Find headphones and also find keyboards" is two intents, not one '
            "intent with two queries.",
            "2. Use only the site_id and operation strings listed above. If the "
            "request asks for a site or an action that is not listed, report the "
            "site_id or operation the user actually asked for, verbatim and in "
            "lower case, and add an ambiguity saying it is not in the list. Do "
            "not silently substitute a supported one.",
            "3. Every parameter must come from the request. Set source to "
            '"text_span" and give span_start and span_end as the zero-based, '
            "half-open character range of the request text the value came from, "
            "so request_text[span_start:span_end] is exactly the words you read "
            "the value from. Set source to \"structured\" with span_start and "
            "span_end null only when the value came from a structured field "
            'rather than prose. Never use "default": the controller fills '
            "defaults itself.",
            "4. Never invent a parameter. Do not add a parameter the request does "
            "not mention, do not fill in a default, and do not translate an "
            "unsupported filter into a supported one.",
            "5. If a required parameter is absent, leave it out of parameters and "
            "add an entry to missing_required with the intent's index, the "
            "parameter name, and one short question that would get it. Do not "
            "guess the value.",
            "6. If the request is ambiguous - two readings, a vague quantity, an "
            "unsupported filter, a site or operation that is not listed - add a "
            "plain sentence to ambiguities. Listing an ambiguity is always better "
            "than guessing.",
            "7. confidence is 0.0 to 1.0: how sure you are of that value, and of "
            "that intent, given the words in the request.",
            "8. Report only what the request says. You are not deciding whether "
            "it can run; a later stage does that.",
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Output models (pydantic, built lazily so this module imports without it)
# --------------------------------------------------------------------------- #


_OUTPUT_MODEL: Any = None


def _output_model() -> Any:
    """The pydantic model ``messages.parse`` validates the response against.

    Mirrors ``InterpretedRequest`` minus the fields the controller owns
    (``request_id``, ``raw_text``, ``model``, ``interpreted_at``).  Parameters
    are a list rather than a mapping because a structured-output schema may not
    allow free-form object keys, and ``site_id``/``operation`` are plain strings
    rather than enums so an unsupported request can still be reported honestly.
    """
    global _OUTPUT_MODEL
    if _OUTPUT_MODEL is not None:
        return _OUTPUT_MODEL

    try:
        from pydantic import BaseModel, ConfigDict, Field
    except ImportError as exc:  # pragma: no cover - depends on the environment
        raise ContractError(
            "interpretation needs pydantic (installed with the anthropic SDK): "
            "pip install anthropic",
            code="PRECONDITION_FAILED",
        ) from exc

    from typing import Literal, Optional, Union

    class _Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class ParameterOut(_Strict):
        """One parameter value and the characters of the request behind it."""

        name: str = Field(description="Parameter name exactly as the operation declares it.")
        value: Union[str, int, float, bool] = Field(description="The value the request asks for.")
        source: Literal["text_span", "structured"] = Field(
            description='"text_span" when the value was read from the request text.'
        )
        confidence: float = Field(description="0.0 to 1.0 confidence in this value.")
        span_start: Optional[int] = Field(
            description="Zero-based start of the span, or null when source is not text_span."
        )
        span_end: Optional[int] = Field(
            description="Exclusive end of the span, or null when source is not text_span."
        )

    class IntentOut(_Strict):
        """One supported site operation the request asks for."""

        site_id: str
        operation: str
        parameters: list[ParameterOut]
        confidence: float

    class MissingParameterOut(_Strict):
        """A required parameter the request did not supply."""

        intent_index: int
        parameter: str
        question: str

    class InterpretationOut(_Strict):
        """What the model understood; the controller adds the run metadata."""

        intents: list[IntentOut]
        missing_required: list[MissingParameterOut]
        ambiguities: list[str]

    _OUTPUT_MODEL = InterpretationOut
    return _OUTPUT_MODEL


# --------------------------------------------------------------------------- #
# Reading the response
# --------------------------------------------------------------------------- #


def _field(obj: Any, name: str, default: Any = _SENTINEL) -> Any:
    """Read ``name`` from a pydantic model, a namespace or a mapping."""
    if isinstance(obj, Mapping):
        value = obj.get(name, _SENTINEL)
    else:
        value = getattr(obj, name, _SENTINEL)
    if value is _SENTINEL:
        if default is not _SENTINEL:
            return default
        raise ContractError(
            f"the interpretation response is missing {name!r}",
            code="EXTRACTION_FAILED",
        )
    return value


def _sequence(obj: Any, name: str) -> list[Any]:
    value = _field(obj, name, [])
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise ContractError(
            f"the interpretation response field {name!r} is not a list",
            code="EXTRACTION_FAILED",
        )
    return list(value)


def _float(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError(
            f"{label} must be a number, got {value!r}", code="EXTRACTION_FAILED"
        )
    return float(value)


def _normalise_value(operation: str, name: str, value: Any) -> Any:
    """Make a JSON number match the type the registry declares.

    JSON has one number type, so an integer parameter can arrive as ``5.0``.
    Narrowing it is a representation fix, not a repair of the model's answer:
    nothing else about the value changes, and a non-integral float is left alone
    so the gate still rejects it.
    """
    spec = registry.OPERATIONS.get(operation)
    if not spec:
        return value
    rule = spec["parameters"].get(name)
    if not rule or rule.get("type") != "integer":
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _span_matches(raw_text: str, span: tuple[int, int], value: Any) -> bool:
    """True when ``raw_text[start:end]`` really is where ``value`` came from.

    Whitespace and letter case are ignored, and a numeric value is compared
    numerically after dropping a leading currency symbol and thousands commas,
    so ``"$150"`` cited for ``150`` counts as a match while ``"under"`` does not.
    """
    start, end = span
    if start < 0 or end < start or end > len(raw_text):
        return False
    quoted = raw_text[start:end].strip()
    if not quoted:
        return False
    if isinstance(value, bool):
        return quoted.lower() == str(value).lower()
    if isinstance(value, (int, float)):
        cleaned = quoted.lstrip("$£€").replace(",", "").rstrip("%").strip()
        try:
            return float(cleaned) == float(value)
        except ValueError:
            return False
    if isinstance(value, str):
        return quoted.casefold() == value.strip().casefold()
    return False


def _read_span(raw: Any) -> tuple[int, int] | None:
    start = _field(raw, "span_start", None)
    end = _field(raw, "span_end", None)
    if start is None or end is None:
        return None
    if isinstance(start, bool) or isinstance(end, bool):
        return None
    if not isinstance(start, int) or not isinstance(end, int):
        return None
    return (start, end)


def _origin(
    raw: Any,
    *,
    raw_text: str,
    operation: str,
    name: str,
    intent_index: int,
    ambiguities: list[str],
) -> ParameterOrigin:
    """Build one :class:`ParameterOrigin`, dropping a span that does not match."""
    source = _field(raw, "source")
    if source not in PARAMETER_SOURCES:
        raise ContractError(
            f"parameter {name!r} has unsupported source {source!r}",
            code="EXTRACTION_FAILED",
        )
    value = _normalise_value(operation, name, _field(raw, "value"))
    confidence = _float(_field(raw, "confidence", 0.0), f"parameter {name!r} confidence")
    span = _read_span(raw)

    if source != "text_span":
        # Only a text span carries a span; see ParameterOrigin's contract.
        return ParameterOrigin(value=value, source=source, confidence=confidence)

    if span is None or not _span_matches(raw_text, span, value):
        quoted = "" if span is None else raw_text[max(span[0], 0) : max(span[1], 0)]
        where = "no span was given" if span is None else f"characters {span[0]}-{span[1]} ({quoted!r})"
        ambiguities.append(
            f"intent {intent_index}: the value {value!r} for {name!r} was cited "
            f"to {where}, which is not where it appears in the request; the "
            f"citation was dropped, so this value is unverified."
        )
        return ParameterOrigin(value=value, source="structured", confidence=0.0)

    return ParameterOrigin(
        value=value, source="text_span", confidence=confidence, span=span
    )


def _intent(
    raw: Any, *, raw_text: str, intent_index: int, ambiguities: list[str]
) -> Intent:
    site_id = _field(raw, "site_id")
    operation = _field(raw, "operation")
    if not isinstance(site_id, str) or not isinstance(operation, str):
        raise ContractError(
            "an interpreted intent has a non-string site_id or operation",
            code="EXTRACTION_FAILED",
        )
    parameters: dict[str, ParameterOrigin] = {}
    for raw_parameter in _sequence(raw, "parameters"):
        name = _field(raw_parameter, "name")
        if not isinstance(name, str) or not name:
            raise ContractError(
                "an interpreted parameter has no name", code="EXTRACTION_FAILED"
            )
        if name in parameters:
            ambiguities.append(
                f"intent {intent_index}: {name!r} was interpreted more than once; "
                f"the first reading was kept."
            )
            continue
        parameters[name] = _origin(
            raw_parameter,
            raw_text=raw_text,
            operation=operation,
            name=name,
            intent_index=intent_index,
            ambiguities=ambiguities,
        )
    return Intent(
        site_id=site_id,
        operation=operation,
        parameters=parameters,
        confidence=_float(
            _field(raw, "confidence", 0.0), f"intent {intent_index} confidence"
        ),
    )


def _missing_parameter(raw: Any) -> MissingParameter:
    intent_index = _field(raw, "intent_index")
    parameter = _field(raw, "parameter")
    question = _field(raw, "question")
    if isinstance(intent_index, bool) or not isinstance(intent_index, int):
        raise ContractError(
            "missing_required.intent_index must be an integer",
            code="EXTRACTION_FAILED",
        )
    if not isinstance(parameter, str) or not isinstance(question, str):
        raise ContractError(
            "missing_required.parameter and .question must be strings",
            code="EXTRACTION_FAILED",
        )
    return MissingParameter(
        intent_index=intent_index, parameter=parameter, question=question
    )


# --------------------------------------------------------------------------- #
# The call
# --------------------------------------------------------------------------- #


def _resolve_model(model: str | None) -> str:
    if model:
        return model
    return os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL


def _default_client() -> Any:
    """The real SDK client, imported here so the package imports without it."""
    try:
        import anthropic
    except ImportError as exc:
        raise ContractError(
            "interpretation needs the anthropic SDK: pip install anthropic",
            code="PRECONDITION_FAILED",
        ) from exc
    return anthropic.Anthropic()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def interpret(
    text: str,
    request_id: str,
    client: Any | None = None,
    model: str | None = None,
) -> InterpretedRequest:
    """Interpret ``text`` into the stage 1 handoff artifact.

    ``client`` defaults to a real ``anthropic.Anthropic()``; tests inject a fake
    whose ``messages.parse`` returns a canned response, so no test calls the
    network.  ``model`` defaults to ``$ARGUS_MODEL`` then :data:`DEFAULT_MODEL`.

    Raises :class:`~argus.contracts.ContractError` with code ``MODEL_REFUSED``
    when the model declined the request, ``PRECONDITION_FAILED`` when the SDK is
    not installed, ``EXTRACTION_FAILED`` when the response cannot be read, and
    ``INVALID_INPUT`` when ``text`` is empty.
    """
    if not isinstance(text, str) or not text.strip():
        raise ContractError(
            "the request text is empty; there is nothing to interpret",
            code="INVALID_INPUT",
        )
    if not isinstance(request_id, str) or not request_id:
        raise ContractError("request_id is required", code="INVALID_INPUT")

    resolved_model = _resolve_model(model)
    active_client = _default_client() if client is None else client

    response = active_client.messages.parse(
        model=resolved_model,
        max_tokens=MAX_TOKENS,
        system=system_prompt(),
        messages=[{"role": "user", "content": text}],
        output_format=_output_model(),
    )

    # Checked before anything reads the content: a refusal must not be parsed.
    stop_reason = _field(response, "stop_reason", None)
    if stop_reason == "refusal":
        details = _field(response, "stop_details", None)
        category = _field(details, "category", None) if details is not None else None
        suffix = f" (category {category})" if category else ""
        raise ContractError(
            f"the model declined to interpret this request{suffix}",
            code="MODEL_REFUSED",
        )
    if stop_reason == "max_tokens":
        raise ContractError(
            "the interpretation was cut off by the output limit",
            code="EXTRACTION_FAILED",
        )

    parsed = _field(response, "parsed_output", None)
    if parsed is None:
        raise ContractError(
            "the model returned no parsed interpretation", code="EXTRACTION_FAILED"
        )

    ambiguities: list[str] = []
    intents = [
        _intent(raw, raw_text=text, intent_index=index, ambiguities=ambiguities)
        for index, raw in enumerate(_sequence(parsed, "intents"))
    ]
    missing_required = [
        _missing_parameter(raw) for raw in _sequence(parsed, "missing_required")
    ]
    model_ambiguities = [
        item for item in _sequence(parsed, "ambiguities") if isinstance(item, str)
    ]

    return InterpretedRequest(
        request_id=request_id,
        raw_text=text,
        intents=intents,
        model=resolved_model,
        interpreted_at=_utc_now(),
        missing_required=missing_required,
        # The model's own ambiguities first, then the ones this module found.
        ambiguities=model_ambiguities + ambiguities,
    )
