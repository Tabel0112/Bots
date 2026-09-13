"""Suggest a public read-only site for open intents that name none.

The suggestion boundary makes exactly one structured model call per unresolved
open intent. Code validates every returned hostname against the interpreter's
syntax rules and ARGUS domain policy; model output never grants authority by
itself. Refusal, invalid output, transport failure, or no allowed candidate
leaves the request untouched so the existing gate asks the user which site.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from argus import registry
from argus.contracts import InterpretedRequest, ParameterOrigin
from argus.interpreter import _hostname, _malformed_domain

__all__ = ["suggest_sites"]

logger = logging.getLogger(__name__)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _Candidate(_StrictModel):
    domain: Annotated[str, Field(min_length=1, max_length=253)]
    reason: Annotated[str, Field(min_length=1, max_length=400)]
    confidence: Annotated[float, Field(ge=0, le=1)] = 0.5


class _Suggestions(_StrictModel):
    candidates: Annotated[list[_Candidate], Field(min_length=1, max_length=3)]


def _belongs_to_selected_site_ambiguity(
    text: str, selected: set[int], unresolved_count: int
) -> bool:
    lowered = text.casefold()
    if not any(word in lowered for word in ("site", "website", "domain")):
        return False
    indexed = re.search(r"\bintent\s+(\d+)\b", lowered)
    if indexed:
        return int(indexed.group(1)) in selected
    return unresolved_count == 1 and bool(selected)


def suggest_sites(
    interpreted: InterpretedRequest, client, *, max_tokens: int = 400
) -> InterpretedRequest:
    """Return a copy with allowed model-suggested domains filled where possible."""
    result = copy.deepcopy(interpreted)
    unresolved = [
        index
        for index, intent in enumerate(result.intents)
        if intent.kind == "open" and intent.target_domain is None
    ]
    selected: set[int] = set()
    for index in unresolved:
        intent = result.intents[index]
        prompt = (
            "Suggest 1 to 3 well-known public websites where this goal can be "
            "answered using read-only browsing. Return hostnames only, never URLs, "
            "login pages, private hosts, or invented domains. Give a short reason "
            "and confidence from 0 to 1."
        )
        try:
            response = client.parse_json(
                system=prompt,
                user=json.dumps(
                    {
                        "goal": intent.goal,
                        "operation": intent.operation,
                        "criteria": [
                            criterion.to_dict() for criterion in intent.criteria
                        ],
                        "expected_record_shape": list(intent.expected_record_shape),
                    }
                ),
                output_model=_Suggestions,
                max_tokens=max_tokens,
            )
            if response.status != "ok" or response.parsed is None:
                continue
            suggestions = _Suggestions.model_validate(response.parsed)
        except Exception as exc:  # noqa: BLE001 - model boundary must fall back safely
            logger.info(
                "site suggestion unavailable for intent %d: %s",
                index,
                type(exc).__name__,
            )
            continue
        for candidate in suggestions.candidates:
            domain = _hostname(candidate.domain).strip().lower().rstrip(".")
            if not domain or _malformed_domain(domain) is not None:
                continue
            allowed, _reason = registry.domain_allowed(domain)
            if not allowed:
                continue
            intent.target_domain = domain
            intent.site_id = f"open:{domain}"
            intent.parameters["site_choice"] = ParameterOrigin(
                value=domain,
                source="suggested",
                confidence=candidate.confidence,
                span=None,
            )
            selected.add(index)
            break
    if selected:
        result.ambiguities = [
            ambiguity
            for ambiguity in result.ambiguities
            if not _belongs_to_selected_site_ambiguity(
                ambiguity, selected, len(unresolved)
            )
        ]
    return result
