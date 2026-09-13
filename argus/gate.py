"""Stage 2: decide whether an interpreted request may execute.

Pure rules, no model call, no I/O.  The gate is the last point at which nothing
has happened yet: after it, sessions open and a browser acts.  So it is
deliberately unforgiving - an unknown filter is a rejection, never a silently
dropped extra - and every decision names the rule that fired.

A registry intent is judged by the catalog rules G1 to G7, unchanged from phase
1.  An open-world intent has no catalog entry to check, so it is judged by the
S rules instead: a public target, a goal worth running and criteria specific
enough to rank on.  A mixed request runs the catalog rules first, then the S
rules; the first rule that fires decides.

====  =========  ======================================================
Rule  Decision   Fires when
====  =========  ======================================================
G1    reject     the request produced no intent at all
G2    reject     a registry intent names a site that is not configured
G3    reject     a registry intent names an operation the site lacks
G4    clarify    a required parameter is absent
G5    reject     a parameter's type or scope is invalid
G6    reject     a parameter is not part of the operation
G7    clarify    a required parameter is too uncertain to run on
S1    clarify    an open intent has no target domain
S2    reject     an open intent's target domain fails the domain policy
S3    clarify    an open intent has no goal to pursue
S4    clarify    a ranking criterion is too uncertain to rank on
S5    reject     the wording asks ARGUS to perform a state-changing action
G0    accept     nothing fired and every intent is a registry match
S0    accept     nothing fired and the request has an open intent
====  =========  ======================================================

``clarify`` ends the run as ``needs_input`` carrying ``questions`` and nothing
executes; ``reject`` ends it with ``INVALID_INPUT``, except that the controller
carries the two open-world rejections under their own codes
(``DOMAIN_NOT_ALLOWED`` for S2, ``ACTION_CLASS_NOT_ALLOWED`` for S5; see
``controller.GATE_REJECT_CODES``).  Both are normal outcomes, not errors: see
:class:`~argus.contracts.GateDecision`.

Two limits of the S rules, stated plainly because the wording is easy to trust
too much:

* S2 is :func:`argus.registry.domain_allowed`, an offline hostname preflight.
  It says nothing about the addresses that hostname resolves to.  The transport
  still has to check every resolved address, redirect and subsequent request.
* S5 is a wording preflight, a first cut, not an action classifier.  It matches
  a small set of verb-plus-object phrases ("log in", "pay the invoice", "submit
  the form") and exempts one that a documentation frame governs, so "how do I
  log in to X" reads *about* a login while "log in to X and download my
  invoices" asks for one.  It will miss paraphrases and will sometimes reject an
  innocent sentence.  Execution must independently refuse to log in, pay or
  submit whatever this rule concluded; the gate only keeps the obvious cases
  from ever opening a session.
"""

from __future__ import annotations

import re
from typing import Any

from argus import registry
from argus.contracts import GateDecision, InterpretedRequest

__all__ = [
    "CONFIDENCE_FLOOR",
    "RANK_QUESTION",
    "gate",
]

#: A required parameter (G7) or a ranking criterion (S4) below this confidence
#: is clarified, not guessed.
CONFIDENCE_FLOOR = 0.6

#: Exactly the clarification approved in docs/hackathon/ARGUS.md, with the
#: user's own ranking words in place of "best".  The examples are there to be
#: chosen between; the gate never selects one as a default.
RANK_QUESTION = (
    "What should '{text}' mean? For example, highest salary, remote-only roles, "
    "or closest match to your experience."
)

#: Verb-plus-object phrases that ask for an action a read-only run never takes,
#: paired with the class named in the rejection.  Applied in this order.  The
#: objects are required wherever the verb has an innocent reading: "pay" needs a
#: bill, "book" needs a thing booked, "check out" needs a cart, because "jobs
#: that pay the highest salary" and "check out this page" are not transactions.
_ACTION_PATTERNS: tuple[tuple[str, str], ...] = (
    ("a login", r"\b(?:log|sign)[\s-]?(?:in|into|on)\b"),
    ("a login", r"\b(?:log-?in|sign-?in|authenticate|re-?authenticate)\b"),
    (
        "an account creation",
        r"\b(?:creat\w+|register|registering|sign[\s-]?up|open|opening)\b"
        r"[^.;]{0,20}?\b(?:account|profile|membership)\b",
    ),
    (
        "a payment",
        r"\b(?:make|makes|making|made|send|sends|sending|sent|enter|entering|"
        r"submit|submitting)\b[^.;]{0,20}?\b(?:payment|payments|card details|"
        r"payment details|bank details)\b",
    ),
    (
        "a payment",
        r"\bpay(?:s|ing|ed)?\b[^.;]{0,20}?\b(?:invoice|invoices|bill|bills|"
        r"balance|fee|fees|amount|total|subscription|rent|premium|tuition|"
        r"order|orders)\b",
    ),
    (
        "a purchase",
        r"\b(?:buy|buys|buying|bought|purchase|purchases|purchasing|purchased)\b",
    ),
    ("a purchase", r"\b(?:place|places|placing|placed)\b[^.;]{0,15}?\border\b"),
    (
        "a purchase",
        r"\b(?:book|books|booking|booked|reserve|reserves|reserving|reserved|"
        r"rent|renting|rented)\s+(?:a|an|the|my|our|\d+)\s",
    ),
    ("a purchase", r"\b(?:subscribe|subscribes|subscribing|subscribed)\s+to\b"),
    ("a checkout", r"\bcheckout\b"),
    (
        "a checkout",
        r"\bcheck[\s-]?out\b(?=[^.;]{0,25}\b(?:cart|basket|bag|order|payment|"
        r"purchase)\b)",
    ),
    (
        "a checkout",
        r"\b(?:complete|completing|completed|finish|finishing|finished)\b"
        r"[^.;]{0,15}?\b(?:purchase|checkout|order)\b",
    ),
    (
        "a checkout",
        r"\badd(?:s|ing|ed)?\b[^.;]{0,25}?\bto\s+(?:the\s+|my\s+|your\s+)?"
        r"(?:cart|basket|bag)\b",
    ),
    (
        "a form submission",
        r"\b(?:submit|submits|submitting|submitted|file|files|filing|filed)\b"
        r"[^.;]{0,25}?\b(?:form|forms|application|applications|request|review|"
        r"comment|complaint|report|claim|ticket|resume|cv|bid|offer|answer|"
        r"response)\b",
    ),
    ("a form submission", r"\bappl(?:y|ies|ying|ied)\s+(?:for|to)\b"),
    (
        "a post that changes state",
        r"\b(?:post|posts|posting|posted|publish|publishes|publishing|published|"
        r"upload|uploads|uploading|uploaded)\b[^.;]{0,25}?\b(?:comment|comments|"
        r"review|reviews|reply|replies|message|messages|answer|answers|listing|"
        r"listings|ad|photo|photos|file|files|document|documents|resume|cv|"
        r"thread|tweet)\b",
    ),
    (
        "a post that changes state",
        r"\b(?:send|sends|sending|sent)\b[^.;]{0,25}?\b(?:message|messages|"
        r"email|emails|dm|reply|invite|invitation)\b",
    ),
)

#: Wordings that make a following action phrase a topic being read about rather
#: than an action being asked for.
_DOCUMENTATION_FRAMES = (
    "how do i", "how do you", "how does", "how can i", "how would i", "how to",
    "how-to", "what is", "what are", "what happens", "where do i", "why does",
    "documentation", "docs", "guide", "instructions", "tutorial", "faq",
    "help page", "help article", "help centre", "help center", "support page",
    "policy", "explain", "explaining", "read about", "learn about", "learn how",
    "find out how", "steps to", "article about", "instructions for",
)

#: Nouns that make the phrase before them the name of something to read, so
#: "summarise the checkout instructions" is a reading request.  Kept short and
#: unambiguously documentary: a word like "page" would exempt far too much.
_DOCUMENTATION_NOUNS = (
    "documentation", "docs", "guide", "instructions", "tutorial", "faq",
    "policy", "article", "steps", "help",
)

#: A documentation frame governs only its own clause; these end it.
_CLAUSE_BREAKS = (" and ", " then ", " after ", " also ", " but ", ",", ";", ".")

#: How far S5 looks either side of a phrase for the wording governing it.
_FRAME_LOOKBACK = 80
_NOUN_LOOKAHEAD = 40


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


def _suffix(index: int, total: int) -> str:
    """Name the intent a rule fired on, but only when there is more than one."""
    return f" (intent {index})" if total > 1 else ""


# --------------------------------------------------------------------------- #
# Catalog rules: G1 to G7
# --------------------------------------------------------------------------- #


def _catalog_rules(
    interpreted: InterpretedRequest,
    intents: list[tuple[int, Any]],
    total: int,
) -> GateDecision | None:
    """Apply G2 to G7 to the registry intents, or return ``None`` if all pass."""
    # G2 - unknown site.
    for index, intent in intents:
        if intent.site_id not in registry.SITES:
            return GateDecision(
                decision="reject",
                rule_id="G2",
                reason=(
                    f"Site {intent.site_id!r} is not configured; the supported "
                    f"sites are {_known_sites()}."
                    + _suffix(index, total)
                ),
            )

    # G3 - the site does not offer this operation.
    for index, intent in intents:
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
                    + _suffix(index, total)
                ),
            )

    # G4 - a required parameter is absent: ask, never guess.
    questions: list[str] = []
    absent: list[str] = []
    for index, intent in intents:
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
    for index, intent in intents:
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
                    + _suffix(index, total)
                ),
            )

    # G6 - an unknown filter is never dropped silently.
    for index, intent in intents:
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
                    + _suffix(index, total)
                ),
            )

    # G7 - a required parameter this uncertain is worth one question.
    uncertain: list[str] = []
    questions = []
    for index, intent in intents:
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
    return None


# --------------------------------------------------------------------------- #
# Open-world rules: S1 to S5
# --------------------------------------------------------------------------- #


def _frame_governs(text: str, start: int, end: int) -> bool:
    """True when documentation wording governs the action phrase at ``start``.

    Only the clause the phrase sits in counts: the lookback stops at the last
    clause break before it, so "read the docs and then log in" still asks for a
    login while "how do I log in" asks about one.  A documentation noun just
    after the phrase counts too, for "the checkout instructions".
    """
    before = text[max(0, start - _FRAME_LOOKBACK) : start]
    cut = 0
    for separator in _CLAUSE_BREAKS:
        found = before.rfind(separator)
        if found >= 0:
            cut = max(cut, found + len(separator))
    if any(frame in before[cut:] for frame in _DOCUMENTATION_FRAMES):
        return True

    after = text[end : end + _NOUN_LOOKAHEAD]
    stop = len(after)
    for separator in _CLAUSE_BREAKS:
        found = after.find(separator)
        if found >= 0:
            stop = min(stop, found)
    return any(noun in after[:stop] for noun in _DOCUMENTATION_NOUNS)


def _requested_action(text: str) -> tuple[str, str] | None:
    """The first state-changing action this wording asks for, else ``None``.

    Returns the action class and the words that matched, in
    :data:`_ACTION_PATTERNS` order.  A wording preflight only; see the module
    docstring for what it does not do.
    """
    if not text:
        return None
    lowered = text.lower()
    for action, pattern in _ACTION_PATTERNS:
        for match in re.finditer(pattern, lowered):
            if not _frame_governs(lowered, match.start(), match.end()):
                return action, match.group(0).strip()
    return None


def _open_rules(
    interpreted: InterpretedRequest,
    intents: list[tuple[int, Any]],
    total: int,
) -> GateDecision | None:
    """Apply S1 to S5 to the open intents, or return ``None`` if all pass."""
    # S1 - nothing to browse. The interpreter never invents a domain, so this is
    # a question, not a failure.
    for index, intent in intents:
        if intent.target_domain is None:
            return GateDecision(
                decision="clarify",
                rule_id="S1",
                reason=(
                    "The request does not name a site to browse, and a site is "
                    "never guessed from a name."
                    + _suffix(index, total)
                ),
                questions=[
                    "Which site should I use? Give the domain or a link, for "
                    "example jobs.example.com."
                ],
            )

    # S2 - the offline domain policy. The reason is the policy's own, so the
    # user learns why rather than just that.
    for index, intent in intents:
        allowed, reason = registry.domain_allowed(intent.target_domain)
        if not allowed:
            return GateDecision(
                decision="reject",
                rule_id="S2",
                reason=(
                    f"{reason[:1].upper()}{reason[1:]} (DOMAIN_NOT_ALLOWED). "
                    "ARGUS browses public sites only."
                    + _suffix(index, total)
                ),
            )

    # S3 - a target without a goal cannot be run or validated.
    for index, intent in intents:
        if intent.goal is None or not intent.goal.strip():
            return GateDecision(
                decision="clarify",
                rule_id="S3",
                reason=(
                    f"The request names {intent.target_domain} but not what to "
                    "do there, so there is nothing to look for or to validate."
                    + _suffix(index, total)
                ),
                questions=[
                    f"What should I look for on {intent.target_domain}?"
                ],
            )

    # S4 - a vague ranking is asked about, never resolved by the gate. The
    # examples in the question are suggestions; none of them is selected.
    for index, intent in intents:
        for criterion in intent.criteria:
            if criterion.kind != "rank" or criterion.confidence >= CONFIDENCE_FLOOR:
                continue
            return GateDecision(
                decision="clarify",
                rule_id="S4",
                reason=(
                    f"The ranking {criterion.text!r} is too vague to order "
                    f"results by (confidence {criterion.confidence}), and it is "
                    "kept rather than dropped, so it has to be asked about."
                    + _suffix(index, total)
                ),
                questions=[RANK_QUESTION.format(text=criterion.text)],
            )

    # S5 - the wording asks for an action a read-only run never performs. Both
    # the goal and the request text are checked, because either can carry it.
    for index, intent in intents:
        for source, text in (("goal", intent.goal), ("request", interpreted.raw_text)):
            found = _requested_action(text)
            if found is None:
                continue
            action, phrase = found
            return GateDecision(
                decision="reject",
                rule_id="S5",
                reason=(
                    f"The {source} asks ARGUS to perform {action} "
                    f"({phrase!r}), which a read-only run never does "
                    "(ACTION_CLASS_NOT_ALLOWED). Reading or searching public "
                    "documentation about it is allowed, so ask for that "
                    "instead if that is what you meant."
                    + _suffix(index, total)
                ),
            )
    return None


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #


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

    total = len(intents)
    registry_intents = [
        (index, intent)
        for index, intent in enumerate(intents)
        if intent.kind == "registry"
    ]
    open_intents = [
        (index, intent) for index, intent in enumerate(intents) if intent.kind == "open"
    ]

    decision = _catalog_rules(interpreted, registry_intents, total)
    if decision is not None:
        return decision
    decision = _open_rules(interpreted, open_intents, total)
    if decision is not None:
        return decision

    # G0/S0 - nothing fired. S0 whenever an open intent is present, because an
    # accepted open run carries the weaker guarantees of the S rules.
    described = ", ".join(
        f"{intent.operation} on {intent.target_domain or intent.site_id}"
        for intent in intents
    )
    if not open_intents:
        return GateDecision(
            decision="accept",
            rule_id="G0",
            reason=(
                f"{total} supported "
                f"{'subrequest' if total == 1 else 'subrequests'} "
                f"({described}) with valid, sufficiently confident parameters."
            ),
        )
    return GateDecision(
        decision="accept",
        rule_id="S0",
        reason=(
            f"{total} read-only "
            f"{'subrequest' if total == 1 else 'subrequests'} "
            f"({described}): every open target is a public domain allowed by "
            "policy, with a goal to pursue and criteria specific enough to "
            "apply. Domain resolution and action limits are still enforced "
            "during execution."
        ),
    )
