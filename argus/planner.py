"""Stage 3: turn an accepted :class:`InterpretedRequest` into a :class:`Plan`.

The planner is deterministic and makes no model call.  One subtask is created
per intent, in intent order, with plain parameter values (registry defaults
filled in) and the fixed success conditions for its operation.

Concurrency semantics, shared with :mod:`argus.controller`:

* ``depends_on`` lists subtasks whose reports must be *accepted* before this
  one may start.  No supported operation consumes another operation's output
  yet, so the planner leaves it empty; the controller still honours it for
  plans built elsewhere (tests, later operations).
* ``concurrency_group`` is a mutual-exclusion class.  The controller runs at
  most one subtask per group at a time, so subtasks in *distinct* groups may run
  together.  Two intents share a group when they target the same site and
  give the same value for a parameter the user actually supplied (defaults
  such as ``currency`` do not count), because they would compete for the same
  search state.  Everything else gets its own group.

``validate_plan`` is the rule check that follows planning (ARGUS.md stage 3):
no cycles, every subtask maps to a supported operation, unique IDs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus import registry
from argus.contracts import ContractError, Intent, InterpretedRequest, Plan, Subtask

__all__ = ["SUCCESS_CONDITIONS", "DEFAULT_PREFERRED_TOOL", "plan", "validate_plan"]

#: Fixed success conditions per operation.  The moderator judges reports and the
#: controller verifies against exactly these strings; they are not generated.
SUCCESS_CONDITIONS: dict[str, tuple[str, ...]] = {
    "search_products": (
        "results present or explicit empty state",
        "every record has title, price, currency, url",
        "price within max_price when given",
        "query visibly applied",
    ),
}

#: Interpretation tool a fresh subtask prefers; the controller flips it on
#: ``retry_other_path``.
DEFAULT_PREFERRED_TOOL = "dom"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _resolved_parameters(intent: Intent) -> dict[str, Any]:
    """Plain values for the subtask: the intent's values over the registry defaults."""
    values = dict(registry.defaults(intent.operation))
    for name, origin in intent.parameters.items():
        values[name] = origin.value
    return values


def _supplied_parameters(intent: Intent) -> set[tuple[str, Any]]:
    """``(name, value)`` pairs the user supplied, hashable so they can be compared."""
    pairs: set[tuple[str, Any]] = set()
    for name, origin in intent.parameters.items():
        if origin.source == "default":
            continue
        value = origin.value
        if isinstance(value, (list, dict)):
            value = repr(value)
        pairs.add((name, value))
    return pairs


def _shares_parameters(a: Intent, b: Intent) -> bool:
    return a.site_id == b.site_id and bool(_supplied_parameters(a) & _supplied_parameters(b))


def _concurrency_groups(intents: list[Intent]) -> list[str]:
    """Assign ``group-N`` labels; intents that share parameters share a label.

    Union-find over intent indices, then labels are numbered in order of first
    appearance so the output is stable for the same input.
    """
    parent = list(range(len(intents)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(intents)):
        for j in range(i + 1, len(intents)):
            if _shares_parameters(intents[i], intents[j]):
                parent[find(j)] = find(i)

    labels: dict[int, str] = {}
    groups: list[str] = []
    for i in range(len(intents)):
        root = find(i)
        if root not in labels:
            labels[root] = f"group-{len(labels) + 1}"
        groups.append(labels[root])
    return groups


def plan(interpreted: InterpretedRequest, plan_id: str, created_at: str | None = None) -> Plan:
    """Build the plan for an accepted request; one subtask per intent.

    Raises :class:`ContractError` when an intent names an operation the
    registry does not support on its site.  ``created_at`` may be supplied to
    make the output fully reproducible.
    """
    intents = list(interpreted.intents)
    for index, intent in enumerate(intents):
        if not registry.site_supports(intent.site_id, intent.operation):
            raise ContractError(
                f"intent {index}: site {intent.site_id!r} does not support "
                f"operation {intent.operation!r}"
            )
    groups = _concurrency_groups(intents)
    subtasks = [
        Subtask(
            subtask_id=f"subtask-{index + 1}",
            intent_index=index,
            site_id=intent.site_id,
            operation=intent.operation,
            parameters=_resolved_parameters(intent),
            concurrency_group=groups[index],
            output_schema_id=registry.operation_spec(intent.operation)["output_schema_id"],
            depends_on=[],
            success_conditions=list(SUCCESS_CONDITIONS.get(intent.operation, ())),
            preferred_tool=DEFAULT_PREFERRED_TOOL,
        )
        for index, intent in enumerate(intents)
    ]
    built = Plan(
        plan_id=plan_id,
        request_id=interpreted.request_id,
        subtasks=subtasks,
        created_at=created_at or _utc_now(),
    )
    validate_plan(built)
    return built


def validate_plan(plan: Plan) -> None:
    """Raise :class:`ContractError` on duplicate IDs, unsupported operations,
    dependencies on unknown subtasks, or a dependency cycle."""
    seen: set[str] = set()
    for subtask in plan.subtasks:
        if subtask.subtask_id in seen:
            raise ContractError(f"plan {plan.plan_id}: duplicate subtask_id {subtask.subtask_id!r}")
        seen.add(subtask.subtask_id)

    for subtask in plan.subtasks:
        if not registry.site_supports(subtask.site_id, subtask.operation):
            raise ContractError(
                f"plan {plan.plan_id}: subtask {subtask.subtask_id} uses unsupported "
                f"operation {subtask.operation!r} on site {subtask.site_id!r}"
            )
        for dependency in subtask.depends_on:
            if dependency not in seen:
                raise ContractError(
                    f"plan {plan.plan_id}: subtask {subtask.subtask_id} depends on "
                    f"unknown subtask {dependency!r}"
                )

    edges = {s.subtask_id: list(s.depends_on) for s in plan.subtasks}
    state: dict[str, int] = {}  # 1 = on the current path, 2 = finished

    def visit(node: str, path: list[str]) -> None:
        mark = state.get(node, 0)
        if mark == 2:
            return
        if mark == 1:
            cycle = path[path.index(node):] + [node]
            raise ContractError(f"plan {plan.plan_id}: dependency cycle {' -> '.join(cycle)}")
        state[node] = 1
        for dependency in edges[node]:
            visit(dependency, path + [node])
        state[node] = 2

    for subtask_id in edges:
        visit(subtask_id, [])
