"""The sites and operations ARGUS supports.

This is the single source of truth for stage 1 (the interpreter lists these
sites, operations and parameters in its prompt), stage 2 (the gate rejects
anything not described here) and stage 3 (the planner reads the output schema
and the parameter defaults).  Adding a site or an operation is a data change in
this file; no stage needs new code.

A ``site_id`` resolves to a configured origin.  It is never an arbitrary browser
URL, and unknown filters are never dropped silently: a parameter that is not
described here is a problem, not an ignored extra.
"""

from __future__ import annotations

from typing import Any

from argus.contracts import ContractError

__all__ = [
    "SITES",
    "OPERATIONS",
    "operation_spec",
    "site_supports",
    "required_parameters",
    "defaults",
    "validate_parameters",
]

#: Configured sites.  ``origin`` is the only place a run may browse for a site.
SITES: dict[str, dict[str, Any]] = {
    "demo-catalog": {
        "site_id": "demo-catalog",
        "label": "Demo catalog",
        "origin": "https://demo-catalog.invalid",
        "description": "Controlled product catalog with a known expected result set.",
        "operations": ["search_products"],
    },
}

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
