"""Stage 2: decide whether an interpreted request may execute.

Pure rules, no model call, no I/O.  The gate is the last point at which nothing
has happened yet: after it, sessions open and a browser acts.  So it is
deliberately unforgiving - an unknown filter is a rejection, never a silently
dropped extra - and every decision names the rule that fired.

Rules, in order; the first one that fires decides:

====  =========  ==================================================
Rule  Decision   Fires when
====  =========  ==================================================
G1    reject     the request produced no intent at all
G2    reject     an intent names a site that is not configured
G3    reject     an intent names an operation the site does not offer
G4    clarify    a required parameter is absent
G5    reject     a parameter's type or scope is invalid
G6    reject     a parameter is not part of the operation
G7    clarify    a required parameter is too uncertain to run on
G0    accept     none of the above
====  =========  ==================================================

``clarify`` ends the run as ``needs_input`` carrying ``questions`` and nothing
executes; ``reject`` ends it with ``INVALID_INPUT``.  Both are normal outcomes,
not errors: see :class:`~argus.contracts.GateDecision`.
"""

from __future__ import annotations

from typing import Any

from argus import registry
from argus.contracts import GateDecision, InterpretedRequest

__all__ = ["CONFIDENCE_FLOOR", "gate"]

#: A required parameter below this confidence is clarified, not guessed (G7).
CONFIDENCE_FLOOR = 0.6


def _known_sites() -> str:
    return ", ".join(sorted(registry.SITES))


def _plain_parameters(intent: Any) -> dict[str, Any]:
    """The intent's parameter values without their origins, for the registry."""
    return {name: origin.value for name, origin in intent.parameters.items()}


def _default_question(parameter: str, operation: str) -> str:
    return (
        f"What {parameter} should I use for {operation}? "
        f"The request did not say."
    )


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def gate(interpreted: InterpretedRequest) -> GateDecision:
    """Return the gate's decision for ``interpreted``.

    Never raises for a bad request: an unusable request is a ``reject`` or a
    ``clarify`` with the rule that fired, so the run can report why.
    """
    intents = list(interpreted.intents)

    # G1 - nothing to run.
    if not intents:
        return GateDecision(
            decision="reject",
            rule_id="G1",
            reason=(
                "No supported request was found in the text, so there is nothing "
                f"to run. The supported sites are {_known_sites()}."
            ),
        )

    # G2 - unknown site.
    for index, intent in enumerate(intents):
        if intent.site_id not in registry.SITES:
            return GateDecision(
                decision="reject",
                rule_id="G2",
                reason=(
                    f"Site {intent.site_id!r} is not configured; the supported "
                    f"sites are {_known_sites()}."
                    + (f" (intent {index})" if len(intents) > 1 else "")
                ),
            )

    # G3 - the site does not offer this operation.
    for index, intent in enumerate(intents):
        if intent.operation not in registry.OPERATIONS or not registry.site_supports(
            intent.site_id, intent.operation
        ):
            offered = ", ".join(registry.SITES[intent.site_id]["operations"])
            return GateDecision(
                decision="reject",
                rule_id="G3",
                reason=(
                    f"Operation {intent.operation!r} is not supported on site "
                    f"{intent.site_id!r}; it offers {offered}."
                    + (f" (intent {index})" if len(intents) > 1 else "")
                ),
            )

    # G4 - a required parameter is absent: ask, never guess.
    questions: list[str] = []
    absent: list[str] = []
    for index, intent in enumerate(intents):
        for parameter in registry.required_parameters(intent.operation):
            if parameter in intent.parameters:
                continue
            absent.append(f"{parameter!r} for {intent.operation}")
            asked = [
                item.question
                for item in interpreted.missing_required
                if item.intent_index == index and item.parameter == parameter
            ]
            questions.extend(asked or [_default_question(parameter, intent.operation)])
    if absent:
        return GateDecision(
            decision="clarify",
            rule_id="G4",
            reason=(
                "The request is missing required "
                f"{'parameters' if len(absent) > 1 else 'parameter'} "
                f"{', '.join(absent)}."
            ),
            questions=_dedupe(questions),
        )

    # G5 - types and scope. Unknown names are left to G6, so only the parameters
    # the operation declares are checked here; G4 has already run, so nothing
    # required is missing.
    for index, intent in enumerate(intents):
        declared = registry.operation_spec(intent.operation)["parameters"]
        values = {
            name: value
            for name, value in _plain_parameters(intent).items()
            if name in declared
        }
        problems = registry.validate_parameters(intent.operation, values)
        if problems:
            return GateDecision(
                decision="reject",
                rule_id="G5",
                reason=(
                    f"The parameters for {intent.operation} are not valid: "
                    f"{'; '.join(problems)}."
                    + (f" (intent {index})" if len(intents) > 1 else "")
                ),
            )

    # G6 - an unknown filter is never dropped silently.
    for index, intent in enumerate(intents):
        declared = registry.operation_spec(intent.operation)["parameters"]
        unknown = sorted(set(intent.parameters) - set(declared))
        if unknown:
            return GateDecision(
                decision="reject",
                rule_id="G6",
                reason=(
                    f"Operation {intent.operation} has no parameter "
                    f"{', '.join(repr(name) for name in unknown)}; it accepts "
                    f"{', '.join(declared)}. An unsupported filter is not "
                    "ignored, because the answer would silently be about "
                    "something else."
                    + (f" (intent {index})" if len(intents) > 1 else "")
                ),
            )

    # G7 - a required parameter this uncertain is worth one question.
    uncertain: list[str] = []
    questions = []
    for index, intent in enumerate(intents):
        for parameter in registry.required_parameters(intent.operation):
            origin = intent.parameters[parameter]
            if origin.confidence >= CONFIDENCE_FLOOR:
                continue
            uncertain.append(
                f"{parameter!r} read as {origin.value!r} "
                f"(confidence {origin.confidence})"
            )
            questions.append(
                f"Did you mean {parameter} = {origin.value!r} for "
                f"{intent.operation}? I am not confident I read that correctly."
            )
    if uncertain:
        ambiguities = list(interpreted.ambiguities)
        return GateDecision(
            decision="clarify",
            rule_id="G7",
            reason=(
                "The request is too uncertain to run: "
                f"{'; '.join(uncertain)}."
                + (f" Noted ambiguities: {' '.join(ambiguities)}" if ambiguities else "")
            ),
            questions=_dedupe(questions),
        )

    # G0 - nothing fired.
    described = ", ".join(
        f"{intent.operation} on {intent.site_id}" for intent in intents
    )
    return GateDecision(
        decision="accept",
        rule_id="G0",
        reason=(
            f"{len(intents)} supported "
            f"{'subrequest' if len(intents) == 1 else 'subrequests'} "
            f"({described}) with valid, sufficiently confident parameters."
        ),
    )
