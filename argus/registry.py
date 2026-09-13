"""The sites and operations ARGUS supports.

This is the single source of truth for qualified stage 1 registry matches and
for the open-world domain policy.  The interpreter lists the registry in its
prompt, the gate applies registry or open-world rules, and the planner reads
qualified operations' output schemas and parameter defaults.

A ``site_id`` resolves to a configured origin.  It is never an arbitrary browser
URL, and unknown filters are never dropped silently: a parameter that is not
described here is a problem, not an ignored extra.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any

from argus.contracts import ContractError

__all__ = [
    "DOMAIN_POLICY",
    "OPERATIONS",
    "SITES",
    "defaults",
    "domain_allowed",
    "operation_spec",
    "required_parameters",
    "site_supports",
    "sites_carrying",
    "validate_parameters",
]

#: Configured sites.  ``origin`` is the only place a run may browse for a site.
SITES: dict[str, dict[str, Any]] = {
    "demo-catalog": {
        "site_id": "demo-catalog",
        "label": "Demo catalog",
        "origin": "https://demo-catalog.invalid",
        "description": "Controlled product catalog with a known expected result set.",
        # What the site carries. The interpreter maps a request for these products
        # to this site even when the request names no site, instead of treating it
        # as an open-world search that the runtime would then aim at a public
        # retailer.
        "subjects": ["headphones", "keyboard"],
        "operations": ["search_products"],
    },
}


def sites_carrying(text: str) -> list[dict[str, Any]]:
    """Configured sites whose declared ``subjects`` the request text mentions.

    Word-level and case-insensitive, tolerating an English plural ("keyboards"
    mentions "keyboard").  The interpreter uses it to keep a request for a
    carried product on the registry tier when no site is named, and site
    suggestion uses it to avoid aiming such a request at a public retailer.
    Returns ``[{"site_id", "operations", "subjects"}]``, empty when nothing
    matches or no site declares subjects.
    """
    lowered = text.casefold()
    matches: list[dict[str, Any]] = []
    for site in SITES.values():
        found = [
            subject
            for subject in site.get("subjects", [])
            if re.search(rf"\b{re.escape(subject.casefold())}s?\b", lowered)
        ]
        if found:
            matches.append(
                {
                    "site_id": site["site_id"],
                    "operations": list(site["operations"]),
                    "subjects": found,
                }
            )
    return matches


#: Supported operations.  Each parameter declares its type, whether it is
#: required, its default and its scope (``minimum``, ``fixed``).
OPERATIONS: dict[str, dict[str, Any]] = {
    "search_products": {
        "operation": "search_products",
        "site_id": "demo-catalog",
        "description": "Search the catalog and return the matching products.",
        "action_class": "read_only",
        "output_schema_id": "product-list.v1",
        "parameters": {
            "query": {
                "type": "string",
                "required": True,
                "description": "Text typed into the catalog search field.",
            },
            "max_price": {
                "type": "number",
                "required": False,
                "minimum": 0,
                "description": "Upper bound on price, in the operation's currency.",
            },
            "max_results": {
                "type": "integer",
                "required": False,
                "default": 5,
                "minimum": 1,
                "description": "Largest number of records the run may return.",
            },
            "currency": {
                "type": "string",
                "required": False,
                "default": "USD",
                "fixed": "USD",
                "description": "Fixed by the operation; prices are never converted.",
            },
        },
    },
}

#: Offline preflight for public read-only browsing.  Block dedicated login and
#: checkout hosts (including descendants), not whole providers: public docs on
#: stripe.com, paypal.com and auth0.com remain eligible.  An allowed hostname
#: does not authorize an action or establish that its resolved address is safe.
DOMAIN_POLICY: dict[str, Any] = {
    "blocklist": (
        "accounts.google.com",
        "login.microsoftonline.com",
        "checkout.stripe.com",
    ),
    "non_public_suffixes": (
        "localhost",
        "local",
        "internal",
        "lan",
        "home",
        "home.arpa",
        "corp",
        "intranet",
        "test",
        "invalid",
        "example",
        "onion",
    ),
    "allow_any_other": True,
}

_DOMAIN_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z",
    re.IGNORECASE,
)
_NUMERIC_LABEL = re.compile(r"(?:[0-9]+|0x[0-9a-f]+)\Z", re.IGNORECASE)


def operation_spec(operation: str) -> dict[str, Any]:
    """Return the operation's definition, or raise ``ContractError`` if unknown."""
    try:
        return OPERATIONS[operation]
    except KeyError:
        raise ContractError(f"unsupported operation {operation!r}") from None


def site_supports(site_id: str, operation: str) -> bool:
    """True when the site is configured and offers this operation."""
    site = SITES.get(site_id)
    return bool(site) and operation in site["operations"]


def domain_allowed(domain: str) -> tuple[bool, str]:
    """Apply the open-world domain policy without performing network I/O.

    ``domain`` is a hostname or unbracketed IP literal, not a URL.  IDNA, case
    and one trailing DNS dot are normalised before policy checks.  Local names,
    non-public IPs and ambiguous browser numeric-address spellings are rejected.

    This is only an offline preflight.  Before real browsing, the transport must
    resolve and check *all* destination addresses and enforce the same boundary
    on redirects, requests and connections (including DNS rebinding).  Runtime
    action checks must independently prevent login, payment and submissions;
    this helper cannot decide action safety from a hostname.
    """
    if not isinstance(domain, str) or not domain.strip():
        return False, "target domain is required"
    try:
        normalised = (
            domain.strip().encode("idna").decode("ascii").lower().removesuffix(".")
        )
    except UnicodeError:
        return False, f"target domain {domain!r} is not a valid hostname"

    # Brackets, ports and IPv6 scope IDs belong to URL/transport syntax, not
    # this contract.  In particular, a scope ID could select a local interface.
    if any(character in normalised for character in "%[]"):
        return False, f"target domain {domain!r} is not a valid hostname"
    try:
        address = ipaddress.ip_address(normalised)
    except ValueError:
        address = None
    if address is not None:
        if not address.is_global or address.is_multicast or address.is_reserved:
            return (
                False,
                f"target domain {normalised!r} is blocked: non-public IP address",
            )
        if DOMAIN_POLICY["allow_any_other"]:
            return True, f"target domain {normalised!r} is allowed by policy"
        return False, f"target domain {normalised!r} is not on the allowlist"

    if not _DOMAIN_PATTERN.fullmatch(normalised):
        return False, f"target domain {domain!r} is not a valid hostname"
    try:
        # Encoding an already-ASCII string alone does not validate xn-- labels.
        normalised.encode("ascii").decode("idna")
    except UnicodeError:
        return False, f"target domain {domain!r} is not a valid IDNA hostname"

    # Browsers can reinterpret shortened, integer, octal or hexadecimal IPv4
    # forms, e.g. 127.1 or 0x7f000001.  Never send those to DNS as ordinary names.
    if _NUMERIC_LABEL.fullmatch(normalised.rsplit(".", 1)[-1]):
        return (
            False,
            f"target domain {normalised!r} is blocked: ambiguous numeric address",
        )
    if "." not in normalised or any(
        normalised == suffix or normalised.endswith(f".{suffix}")
        for suffix in DOMAIN_POLICY["non_public_suffixes"]
    ):
        return False, f"target domain {normalised!r} is blocked: non-public hostname"

    for blocked in DOMAIN_POLICY["blocklist"]:
        blocked = blocked.lower().removesuffix(".")
        if normalised == blocked or normalised.endswith(f".{blocked}"):
            return False, f"target domain {normalised!r} is blocked by policy"

    if DOMAIN_POLICY["allow_any_other"]:
        return True, f"target domain {normalised!r} is allowed by policy"
    return False, f"target domain {normalised!r} is not on the allowlist"


def required_parameters(operation: str) -> list[str]:
    """Names the caller must supply for this operation, in declaration order."""
    spec = operation_spec(operation)
    return [name for name, rule in spec["parameters"].items() if rule.get("required")]


def defaults(operation: str) -> dict[str, Any]:
    """Values the planner fills in when the request did not mention them."""
    spec = operation_spec(operation)
    return {
        name: rule["default"]
        for name, rule in spec["parameters"].items()
        if "default" in rule
    }


def _type_problem(name: str, rule: dict[str, Any], value: Any) -> str | None:
    expected = rule["type"]
    if expected == "string":
        if not isinstance(value, str):
            return f"parameter {name!r} must be a string"
        return None
    if isinstance(value, bool):
        article = "an" if expected == "integer" else "a"
        return f"parameter {name!r} must be {article} {expected}"
    if expected == "integer":
        if not isinstance(value, int):
            return f"parameter {name!r} must be an integer"
        return None
    if expected == "number":
        if not isinstance(value, (int, float)):
            return f"parameter {name!r} must be a number"
        return None
    return f"parameter {name!r} has unsupported declared type {expected!r}"


def validate_parameters(operation: str, params: dict[str, Any]) -> list[str]:
    """Return every problem with these parameters; an empty list means valid.

    Used by the gate (stage 2) before anything executes.  Reports unknown
    operations, unknown parameters, missing required parameters, wrong types and
    values outside the declared scope.  It never raises and never repairs the
    input: the caller decides whether to clarify or reject.
    """
    problems: list[str] = []
    spec = OPERATIONS.get(operation)
    if spec is None:
        return [f"unsupported operation {operation!r}"]
    if not isinstance(params, dict):
        return ["parameters must be an object"]

    rules = spec["parameters"]
    for name in sorted(set(params) - set(rules)):
        problems.append(f"unknown parameter {name!r} for operation {operation!r}")
    for name in required_parameters(operation):
        if name not in params:
            problems.append(f"missing required parameter {name!r}")

    for name, rule in rules.items():
        if name not in params:
            continue
        value = params[name]
        if value is None:
            problems.append(f"parameter {name!r} must not be null")
            continue
        problem = _type_problem(name, rule, value)
        if problem:
            problems.append(problem)
            continue
        if "fixed" in rule and value != rule["fixed"]:
            problems.append(f"parameter {name!r} is fixed at {rule['fixed']!r}")
        if "minimum" in rule and value < rule["minimum"]:
            problems.append(f"parameter {name!r} must be at least {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            problems.append(f"parameter {name!r} must be at most {rule['maximum']}")
    return problems
