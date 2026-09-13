"""Stage 1: turn a request into an :class:`~argus.contracts.InterpretedRequest`.

It asks a model to prefer qualified operations in :mod:`argus.registry`, or
describe an open-world goal when no qualified operation fits, and returns
the handoff artifact every later stage and every teammate reads.

What this module guarantees, whatever the model returns:

* The response is schema-valid JSON.  :meth:`ModelClient.parse_json
  <argus.model_client.ModelClient.parse_json>` is called with a pydantic output
  model that mirrors the ``InterpretedRequest`` shape, so the backend validates
  before this module sees anything.
* A refusal never reaches the content.  The model boundary reports a refusal as
  a status with no parsed content at all, and that status raises
  :class:`~argus.contracts.ContractError` with the typed code ``MODEL_REFUSED``.
* Every ``text_span`` really is a span of the request.  A span that does not
  match the raw text is dropped: that parameter's source becomes ``structured``
  with confidence ``0`` and the discrepancy is recorded in ``ambiguities`` so the
  gate (stage 2) can see it instead of a fabricated citation.
* Open-world context is grounded, not invented.  ``target_domain`` is only what
  the model returned; a hostname may be read out of an explicit URL with
  ``urllib.parse``, but a site *name* is never turned into a domain here, and a
  malformed hostname becomes ``None`` with an ambiguity.  A criterion's span is
  verified exactly like a parameter's, and a criterion is never dropped: an
  unverifiable one keeps its text at confidence ``0`` with no span.

What it deliberately does not do: it does not judge the request.  An unknown
site, an unsupported operation or a missing parameter is reported faithfully and
:mod:`argus.gate` decides.  It also does not fill in registry defaults; the
planner (stage 3) does that from :func:`argus.registry.defaults`.

Which model is called, and how, is :mod:`argus.model_client`'s business, not
this module's: ``OPENAI_API_KEY``, ``OPENAI_BASE_URL`` and ``ARGUS_MODEL`` are
documented there.  Tests inject a fake :class:`~argus.model_client.ModelClient`
instead of calling the network.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from argus import registry
from argus.contracts import (
    CRITERION_KINDS,
    INTENT_KINDS,
    PARAMETER_SOURCES,
    ContractError,
    Criterion,
    Intent,
    InterpretedRequest,
    MissingParameter,
    ParameterOrigin,
)
from argus.model_client import ModelClient, OpenAICompatibleClient

__all__ = [
    "MAX_TOKENS",
    "interpret",
    "system_prompt",
]

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
        (
            "You are the interpreter of ARGUS, a controller that runs browser tasks "
            "on public websites. You do not browse and you do not act. You "
            "read one request and report what it asks for, in JSON."
        ),
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
            (
                "1. Split a compound request into one intent per site operation. "
                '"Find headphones and also find keyboards" is two intents, not one '
                "intent with two queries."
            ),
            (
                '2. Prefer kind="registry" and the listed site_id/operation when a '
                'qualified operation fits. Otherwise use kind="open", a short '
                "operation name, target_domain, goal, criteria and expected_record_shape. "
                "Set site_id to the named site (or an empty string if none). Unknown "
                "sites are valid open requests, not unsupported registry requests. "
                "Never invent a domain. Extract the hostname from an explicit URL or "
                "domain; a named site may resolve only if unambiguous. If no site is "
                "named, leave target_domain null and add an ambiguity asking for it."
            ),
            (
                "3. Every parameter must come from the request. Set source to "
                '"text_span" and give span_start and span_end as the zero-based, '
                "half-open character range of the request text the value came from, "
                "so request_text[span_start:span_end] is exactly the words you read "
                'the value from. Set source to "structured" with span_start and '
                "span_end null only when the value came from a structured field "
                'rather than prose. Never use "default": the controller fills '
                "defaults itself."
            ),
            (
                "4. Never invent a parameter. Do not add a parameter the request does "
                "not mention, do not fill in a default, and do not translate an "
                "unsupported filter into a supported one."
            ),
            (
                "5. If a required parameter is absent, leave it out of parameters and "
                "add an entry to missing_required with the intent's index, the "
                "parameter name, and one short question that would get it. Do not "
                "guess the value."
            ),
            (
                "6. If the request is ambiguous - two readings, a vague quantity, an "
                "unsupported registry filter or an unresolved site name - add a "
                "plain sentence to ambiguities. Listing an ambiguity is always better "
                "than guessing."
            ),
            (
                "7. confidence is 0.0 to 1.0: how sure you are of that value, and of "
                "that intent, given the words in the request."
            ),
            (
                "8. Report only what the request says. You are not deciding whether "
                "it can run; a later stage does that."
            ),
            (
                "9. For open intents preserve every ranking, filter and limit as a "
                "separate criterion with the exact quoted text and character span. "
                "'best 10 jobs' gives rank 'best' with confidence below 0.6 and limit "
                "'10' with parameter 10. 'cheapest' is a price rank; 'top 10' has both "
                "rank and limit. Vague ranking must stay uncertain. 'Highest salary, "
                "remote only' gives rank parameter 'salary' and filter parameter "
                "'remote', with their own spans. Never drop or guess criteria, and "
                "never select a suggested clarification example for the user. "
                "Criteria parameter holds its resolved scalar value or null. "
                "Registry intents use null open context and empty criteria/shape."
            ),
            (
                "10. expected_record_shape lists field names as short snake_case "
                "identifiers without spaces (title, company, url, salary, remote, "
                "points), one per field a record should carry; never sentences."
            ),
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Output models (pydantic, built lazily so this module imports without it)
# --------------------------------------------------------------------------- #


_OUTPUT_MODEL: Any = None


def _output_model() -> Any:
    """The pydantic model the backend validates the response against.

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
            "interpretation needs pydantic (installed with the openai package): "
            "pip install openai",
            code="PRECONDITION_FAILED",
        ) from exc

    from typing import Literal

    class _Strict(BaseModel):
        model_config = ConfigDict(extra="forbid")

    class ParameterOut(_Strict):
        """One parameter value and the characters of the request behind it."""

        name: str = Field(
            description="Parameter name exactly as the operation declares it."
        )
        value: str | int | float | bool = Field(
            description="The value the request asks for."
        )
        source: Literal["text_span", "structured"] = Field(
            description='"text_span" when the value was read from the request text.'
        )
        confidence: float = Field(description="0.0 to 1.0 confidence in this value.")
        span_start: int | None = Field(
            description="Zero-based start of the span, or null when source is not text_span."
        )
        span_end: int | None = Field(
            description="Exclusive end of the span, or null when source is not text_span."
        )

    class CriterionOut(_Strict):
        text: str
        kind: Literal["rank", "filter", "limit"]
        parameter: str | int | float | bool | None
        span_start: int | None
        span_end: int | None
        confidence: float

    class IntentOut(_Strict):
        """A qualified operation or a grounded open-world request."""

        site_id: str
        operation: str
        parameters: list[ParameterOut]
        confidence: float
        kind: Literal["registry", "open"]
        target_domain: str | None
        goal: str | None
        criteria: list[CriterionOut]
        expected_record_shape: list[str]

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
    confidence = _float(
        _field(raw, "confidence", 0.0), f"parameter {name!r} confidence"
    )
    span = _read_span(raw)

    if source != "text_span":
        # Only a text span carries a span; see ParameterOrigin's contract.
        return ParameterOrigin(value=value, source=source, confidence=confidence)

    if span is None or not _span_matches(raw_text, span, value):
        quoted = "" if span is None else raw_text[max(span[0], 0) : max(span[1], 0)]
        where = (
            "no span was given"
            if span is None
            else f"characters {span[0]}-{span[1]} ({quoted!r})"
        )
        ambiguities.append(
            f"intent {intent_index}: the value {value!r} for {name!r} was cited "
            f"to {where}, which is not where it appears in the request; the "
            f"citation was dropped, so this value is unverified."
        )
        return ParameterOrigin(value=value, source="structured", confidence=0.0)

    return ParameterOrigin(
        value=value, source="text_span", confidence=confidence, span=span
    )


# --------------------------------------------------------------------------- #
# Open-world context
# --------------------------------------------------------------------------- #


#: ``registry.domain_allowed`` reasons that mean "this is not a hostname at
#: all", as opposed to "this hostname is blocked by policy".  Only the former
#: is the interpreter's business; see :func:`_malformed_domain`.
_MALFORMED_DOMAIN_MARKERS = ("is not a valid", "is required")


def _hostname(value: str) -> str:
    """The hostname of an explicit URL, or ``value`` unchanged.

    Pure syntax: :func:`urllib.parse.urlsplit` reads the host out of a URL the
    model already returned, dropping a scheme, userinfo, port, path and query.
    A site *name* is never turned into a domain here - that would invent a
    target the request never stated.
    """
    candidate = value.strip()
    if not any(character in candidate for character in ":/?#"):
        return candidate
    parsed = urlsplit(candidate if "//" in candidate else f"//{candidate}")
    try:
        host = parsed.hostname
    except ValueError:  # an unparseable authority, e.g. a stray bracket
        return candidate
    return host or candidate


def _malformed_domain(domain: str) -> str | None:
    """The reason ``domain`` is not a hostname at all, else ``None``.

    The interpreter only drops syntactic nonsense.  A well-formed hostname that
    the policy blocks - a private address, a non-public suffix, a login host -
    is reported as the model returned it, so the gate can reject it by rule and
    tell the user the policy's own reason; deciding that here would hide it.
    """
    allowed, reason = registry.domain_allowed(domain)
    if allowed:
        return None
    if any(marker in reason for marker in _MALFORMED_DOMAIN_MARKERS):
        return reason
    return None


def _target_domain(
    raw: Any, *, intent_index: int, ambiguities: list[str]
) -> str | None:
    """The open intent's target domain, or ``None`` with an ambiguity."""
    value = _field(raw, "target_domain", None)
    if value is None:
        ambiguities.append(
            f"intent {intent_index}: no target domain was identified for this "
            f"open request, so the site to browse still has to be asked for."
        )
        return None
    if not isinstance(value, str):
        raise ContractError(
            f"intent {intent_index} target_domain must be a string or null",
            code="EXTRACTION_FAILED",
        )
    domain = _hostname(value)
    problem = _malformed_domain(domain)
    if problem is not None:
        ambiguities.append(
            f"intent {intent_index}: the target domain {value!r} was dropped "
            f"because {problem}; the site to browse has to be asked for."
        )
        return None
    return domain


def _goal(raw: Any, *, intent_index: int) -> str | None:
    """The open intent's plain-language goal, reported as the model wrote it."""
    value = _field(raw, "goal", None)
    if value is not None and not isinstance(value, str):
        raise ContractError(
            f"intent {intent_index} goal must be a string or null",
            code="EXTRACTION_FAILED",
        )
    return value


def _record_shape(raw: Any, *, intent_index: int) -> list[str]:
    """The field names each open-world record should carry, in order."""
    names: list[str] = []
    for name in _sequence(raw, "expected_record_shape"):
        if not isinstance(name, str) or not name.strip():
            raise ContractError(
                f"intent {intent_index} expected_record_shape contains {name!r}, "
                f"which is not a field name",
                code="EXTRACTION_FAILED",
            )
        if name not in names:
            names.append(name)
    return names


def _criterion(
    raw: Any, *, raw_text: str, intent_index: int, ambiguities: list[str]
) -> Criterion:
    """Build one :class:`Criterion`, dropping a span that does not match.

    A criterion is never discarded, however it is cited: an unverifiable span
    becomes ``None`` at confidence ``0`` and the discrepancy goes to
    ``ambiguities``, so the gate sees an uncertain ranking rather than a
    ranking that quietly disappeared.  The span is checked against
    ``Criterion.text`` - the words the model quoted - with the same rule as a
    parameter span.
    """
    text = _field(raw, "text")
    if not isinstance(text, str) or not text.strip():
        raise ContractError(
            f"intent {intent_index} has a criterion with no text",
            code="EXTRACTION_FAILED",
        )
    kind = _field(raw, "kind")
    if kind not in CRITERION_KINDS:
        raise ContractError(
            f"criterion {text!r} has unsupported kind {kind!r}",
            code="EXTRACTION_FAILED",
        )
    parameter = _field(raw, "parameter", None)
    confidence = _float(
        _field(raw, "confidence", 0.0), f"criterion {text!r} confidence"
    )
    if not 0.0 <= confidence <= 1.0:
        raise ContractError(
            f"criterion {text!r} confidence {confidence} is outside 0.0 to 1.0",
            code="EXTRACTION_FAILED",
        )
    span = _read_span(raw)

    if span is None or not _span_matches(raw_text, span, text):
        quoted = "" if span is None else raw_text[max(span[0], 0) : max(span[1], 0)]
        where = (
            "no span was given"
            if span is None
            else f"characters {span[0]}-{span[1]} ({quoted!r})"
        )
        ambiguities.append(
            f"intent {intent_index}: the {kind} criterion {text!r} was cited to "
            f"{where}, which is not where it appears in the request; the "
            f"citation was dropped, so this criterion is unverified."
        )
        return Criterion(
            text=text, kind=kind, parameter=parameter, span=None, confidence=0.0
        )

    return Criterion(
        text=text, kind=kind, parameter=parameter, span=span, confidence=confidence
    )


def _note_unused_open_context(
    raw: Any, *, intent_index: int, ambiguities: list[str]
) -> None:
    """Record open-world context returned on a registry intent before dropping it.

    A registry match runs a qualified operation with declared parameters, so a
    target domain, goal, criteria or record shape has nowhere to go.  Dropping
    that silently would lose a ranking the user actually asked for.
    """
    supplied = [
        name
        for name in ("target_domain", "goal", "criteria", "expected_record_shape")
        if _field(raw, name, None)
    ]
    if supplied:
        ambiguities.append(
            f"intent {intent_index}: the registry match also returned "
            f"{', '.join(supplied)}, which a qualified operation cannot apply; "
            f"it was dropped, so say so explicitly to get an open-world run."
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
    kind = _field(raw, "kind", "registry")
    if kind not in INTENT_KINDS:
        raise ContractError(
            f"intent {intent_index} has unsupported kind {kind!r}",
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
    confidence = _float(
        _field(raw, "confidence", 0.0), f"intent {intent_index} confidence"
    )

    # A registry intent is built exactly as it was before tier two existed: the
    # qualified operation and its declared parameters are the whole contract.
    if kind == "registry":
        _note_unused_open_context(
            raw, intent_index=intent_index, ambiguities=ambiguities
        )
        return Intent(
            site_id=site_id,
            operation=operation,
            parameters=parameters,
            confidence=confidence,
        )

    return Intent(
        site_id=site_id,
        operation=operation,
        parameters=parameters,
        confidence=confidence,
        kind="open",
        target_domain=_target_domain(
            raw, intent_index=intent_index, ambiguities=ambiguities
        ),
        goal=_goal(raw, intent_index=intent_index),
        criteria=[
            _criterion(
                item,
                raw_text=raw_text,
                intent_index=intent_index,
                ambiguities=ambiguities,
            )
            for item in _sequence(raw, "criteria")
        ],
        expected_record_shape=_record_shape(raw, intent_index=intent_index),
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


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def interpret(
    text: str,
    request_id: str,
    client: ModelClient | None = None,
    model: str | None = None,
) -> InterpretedRequest:
    """Interpret ``text`` into the stage 1 handoff artifact.

    ``client`` is a :class:`~argus.model_client.ModelClient`.  It defaults to an
    :class:`~argus.model_client.OpenAICompatibleClient` built for ``model``;
    tests inject a fake that returns a canned
    :class:`~argus.model_client.ModelResult`, so no test calls the network.  The
    model name is the client's business - which is why the returned
    ``InterpretedRequest.model`` is the model the client says answered, not a
    name this module resolved.

    Raises :class:`~argus.contracts.ContractError` with code ``MODEL_REFUSED``
    when the model declined the request, ``PRECONDITION_FAILED`` when the SDK or
    the model name is missing, ``EXTRACTION_FAILED`` when the response was cut
    off or cannot be read, and ``INVALID_INPUT`` when ``text`` is empty.
    """
    if not isinstance(text, str) or not text.strip():
        raise ContractError(
            "the request text is empty; there is nothing to interpret",
            code="INVALID_INPUT",
        )
    if not isinstance(request_id, str) or not request_id:
        raise ContractError("request_id is required", code="INVALID_INPUT")

    active_client = OpenAICompatibleClient(model=model) if client is None else client

    result = active_client.parse_json(
        system=system_prompt(),
        user=text,
        output_model=_output_model(),
        max_tokens=MAX_TOKENS,
    )

    # Checked before anything reads the content: a refusal must not be parsed.
    # The boundary already guarantees ``parsed`` is None here; this is the
    # translation into the controller's typed vocabulary.
    if result.status == "refusal":
        detail = result.raw_text.strip() if result.raw_text else ""
        suffix = f": {detail}" if detail else ""
        raise ContractError(
            f"the model declined to interpret this request{suffix}",
            code="MODEL_REFUSED",
        )
    if result.status == "truncated":
        raise ContractError(
            "the interpretation was cut off by the output limit",
            code="EXTRACTION_FAILED",
        )
    if result.status == "invalid":
        detail = result.raw_text.strip() if result.raw_text else ""
        suffix = f": {detail}" if detail else ""
        raise ContractError(
            f"the model did not return a readable interpretation{suffix}",
            code="EXTRACTION_FAILED",
        )

    parsed = result.parsed
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
        model=result.model,
        interpreted_at=_utc_now(),
        missing_required=missing_required,
        # The model's own ambiguities first, then the ones this module found.
        ambiguities=model_ambiguities + ambiguities,
    )
