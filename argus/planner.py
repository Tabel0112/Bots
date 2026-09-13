"""Stage 3: turn an accepted :class:`InterpretedRequest` into a :class:`Plan`.

Registry planning - :func:`plan` - is deterministic and makes no model call.
One subtask is created per intent, in intent order, with plain parameter values
(registry defaults filled in) and the fixed success conditions for its
operation.  An open intent has no qualified operation to expand, so
:func:`plan_open` makes one bounded call through the
:class:`~argus.model_client.ModelClient` boundary for the *scheduling* only;
everything the user actually asked for is still copied by code.

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

import copy
import json
from datetime import datetime, timezone
from typing import Any

from argus import registry
from argus.contracts import ContractError, Intent, InterpretedRequest, Plan, Subtask
from argus.model_client import ModelClient, OpenAICompatibleClient

__all__ = [
    "SUCCESS_CONDITIONS",
    "DEFAULT_PREFERRED_TOOL",
    "PLAN_MAX_TOKENS",
    "plan",
    "plan_open",
    "validate_plan",
]

#: Bounds the one planning call; a plan is at most four scheduling steps.
PLAN_MAX_TOKENS = 4096

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


def plan(interpreted: InterpretedRequest, plan_id: str, created_at: str | None = None,
         *, client: ModelClient | None = None, model: str | None = None) -> Plan:
    """Build the plan for an accepted request; one subtask per intent.

    Raises :class:`ContractError` when an intent names an operation the
    registry does not support on its site.  ``created_at`` may be supplied to
    make the output fully reproducible.
    """
    if any(intent.kind == "open" for intent in interpreted.intents):
        return plan_open(interpreted, plan_id, client=client, model=model, created_at=created_at)
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
    plan.validate_limits()
    seen: set[str] = set()
    for subtask in plan.subtasks:
        if subtask.subtask_id in seen:
            raise ContractError(f"plan {plan.plan_id}: duplicate subtask_id {subtask.subtask_id!r}")
        seen.add(subtask.subtask_id)

    for subtask in plan.subtasks:
        Subtask.from_dict(subtask.to_dict())  # recheck mutable contract fields
        if subtask.kind == "open":
            allowed, reason = registry.domain_allowed(subtask.target_domain)
            if not allowed:
                raise ContractError(reason, code="DOMAIN_NOT_ALLOWED")
            if not subtask.goal or not subtask.goal.strip():
                raise ContractError("open subtask requires a goal")
        elif not registry.site_supports(subtask.site_id, subtask.operation):
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

    positions = {s.subtask_id: i for i, s in enumerate(plan.subtasks)}
    tasks = {s.subtask_id: s for s in plan.subtasks}
    depths: dict[str, int] = {}
    def depth(node: str) -> int:
        if node not in depths:
            depths[node] = 1 + max((depth(dep) for dep in edges[node]), default=0)
        return depths[node]
    is_open = plan.planned_by == "model" or any(s.kind == "open" for s in plan.subtasks)
    for subtask in plan.subtasks:
        if is_open and depth(subtask.subtask_id) > plan.caps["max_depth"]:
            raise ContractError("Open plan exceeds max_depth", code="PLAN_TOO_LARGE")
        for source in subtask.inputs_from.values():
            upstream = tasks[source["subtask_id"]]
            if positions[upstream.subtask_id] >= positions[subtask.subtask_id]:
                raise ContractError("inputs_from requires an earlier subtask")
            if (source["field"] != "findings" and upstream.kind == "open"
                    and source["field"] not in upstream.expected_record_shape):
                raise ContractError("inputs_from field is absent from source expected_record_shape")


_PLAN_MODEL: Any = None


def _output_model() -> Any:
    global _PLAN_MODEL
    if _PLAN_MODEL is not None:
        return _PLAN_MODEL
    try:
        from pydantic import BaseModel, ConfigDict
    except ImportError as exc:
        raise ContractError(
            "planning needs pydantic (installed with the openai package): pip install openai",
            code="PRECONDITION_FAILED",
        ) from exc
    from typing import Literal
    class Strict(BaseModel):
        model_config = ConfigDict(extra="forbid", strict=True)
    class Binding(Strict):
        parameter: str
        subtask_id: str
        field: str
    class Step(Strict):
        subtask_id: str
        intent_index: int
        operation: Literal["search", "open_results", "extract", "navigate"]
        depends_on: list[str]
        inputs_from: list[Binding]
        concurrency_group: str
        success_conditions: list[str]
        preferred_tool: Literal["dom", "vision"]
    class Output(Strict):
        subtasks: list[Step]
    _PLAN_MODEL = Output
    return Output


def plan_open(interpreted: InterpretedRequest, plan_id: str, client: ModelClient | None = None,
              model: str | None = None, *, created_at: str | None = None) -> Plan:
    """One bounded model call for open intents; the model owns scheduling only.

    ``client`` is a :class:`~argus.model_client.ModelClient`, defaulting to an
    :class:`~argus.model_client.OpenAICompatibleClient` built for ``model``, the
    same way :func:`argus.interpreter.interpret` takes one.  Accepted
    parameters, target, goal, criteria and output fields are copied by code.
    Registry intents are planned deterministically and included in the same
    four-task budget. Fake clients exercise the identical parsing checks.
    """
    active = OpenAICompatibleClient(model=model) if client is None else client
    schema = _output_model()
    prompt = (
        "Plan read-only open intents only, retaining their original intent_index. "
        "Return at least one step for every open intent, and none for registry intents. "
        "At most four TOTAL steps including registry intents, maximum dependency depth three. "
        "Use search then open_results when details are needed: one open_results step opens "
        "all result URLs sequentially, never one worker per result. Bind result_urls from "
        "the preceding step's url field. Bindings must reference earlier declared dependencies "
        "and fields in their expected_record_shape, or findings for the full result. "
        "Only search, open_results, extract, navigate operations. Never login, pay, or submit "
        "state changes. Parameters, domain, criteria, goal and schema are controller-owned. "
        "List explicit success conditions. Do not emit caps, context or parameters."
    )
    result = active.parse_json(
        system=prompt,
        user=json.dumps(interpreted.to_dict()),
        output_model=schema,
        max_tokens=PLAN_MAX_TOKENS,
    )
    # A refusal is a status, never content: nothing below reads the response.
    if result.status == "refusal":
        raise ContractError("the model declined to plan this request", code="MODEL_REFUSED")
    if result.status != "ok":
        raise ContractError("the planner response did not complete", code="EXTRACTION_FAILED")
    parsed = result.parsed
    if parsed is None:
        raise ContractError("the model returned no parsed plan", code="EXTRACTION_FAILED")
    try:
        output = schema.model_validate(parsed).model_dump()
    except Exception as exc:
        raise ContractError("the model returned an invalid plan shape", code="EXTRACTION_FAILED") from exc
    tasks: list[Subtask] = []
    for index, intent in enumerate(interpreted.intents):
        if intent.kind == "registry":
            single = copy.deepcopy(interpreted)
            single.intents = [intent]
            st = plan(single, plan_id).subtasks[0]
            st.subtask_id = f"registry-{index + 1}"
            st.intent_index = index
            st.concurrency_group = _concurrency_groups(interpreted.intents)[index]
            tasks.append(st)
    covered: set[int] = set()
    for raw in output["subtasks"]:
        index = raw["intent_index"]
        if not 0 <= index < len(interpreted.intents) or interpreted.intents[index].kind != "open":
            raise ContractError("planner intent_index must identify an accepted open intent")
        intent = interpreted.intents[index]
        covered.add(index)
        bindings = {}
        for binding in raw.pop("inputs_from"):
            name = binding["parameter"]
            if name in intent.parameters or name in bindings:
                raise ContractError("inputs_from cannot overwrite an accepted or repeated parameter")
            bindings[name] = {key: binding[key] for key in ("subtask_id", "field")}
        if not raw["success_conditions"] or not all(raw["success_conditions"]):
            raise ContractError("open steps require success conditions")
        tasks.append(Subtask(
            **raw, inputs_from=bindings, kind="open", site_id=intent.site_id,
            parameters={key: copy.deepcopy(origin.value) for key, origin in intent.parameters.items()},
            output_schema_id="open-records.v1", target_domain=intent.target_domain,
            goal=intent.goal, criteria=copy.deepcopy(intent.criteria),
            expected_record_shape=list(intent.expected_record_shape),
        ))
    if covered != {i for i, intent in enumerate(interpreted.intents) if intent.kind == "open"}:
        raise ContractError("planner omitted an accepted open intent")
    built = Plan(plan_id=plan_id, request_id=interpreted.request_id, subtasks=tasks,
                 created_at=created_at or _utc_now(), planned_by="model")
    validate_plan(built)
    return built
