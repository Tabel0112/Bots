"""Composition of the connected ARGUS runtime (ARGUS-3, package 5).

``build_connected_controller`` wires the real components to the controller:

- toolbox: :class:`argus.adapters.dom_toolbox.DomToolbox` over Tianqi's DOM worker,
  which itself runs inside Sting's ``GhostWorkflow`` when ``GHOST_API_URL`` is set
  (lookup, replay or explore, visual continuation, validation, candidate save);
- ghost: :class:`argus.adapters.ghost_bridge.GhostBridge` (lookup preview, binding
  checks over the worker's verification, candidate reference; never writes);
- moderator: :class:`moderator.moderator.Moderator` over the ARGUS model client;
- interpreter, gate and planner: the real ones (``argus.interpreter``,
  ``argus.gate``, ``argus.planner``), so raw request text goes to ``Controller.run``.

``connected_environment_problems`` lists what is missing before the runtime starts.
It is deliberately strict: a connected runtime that cannot reach its dependencies
must refuse to start rather than fall back to fixtures.
"""

from __future__ import annotations

import copy
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from argus import planner
from argus.contracts import MAX_OPEN_SUBTASKS, InterpretedRequest, Plan, Subtask
from argus.controller import Controller
from argus.store import JsonStore

#: Environment variables every connected run needs, whatever the browser backend.
REQUIRED_ENV = ("OPENAI_API_KEY", "ARGUS_MODEL", "GHOST_API_URL")

RUNTIME_MODES = ("controlled", "connected", "scrape")


def connected_interpret(
    text: str, request_id: str, *, client=None
) -> InterpretedRequest:
    """Interpret text, then suggest policy-checked sites for unresolved open intents."""
    from argus.interpreter import interpret
    from argus.model_client import OpenAICompatibleClient
    from argus.site_suggestion import suggest_sites

    active = client or OpenAICompatibleClient(model=os.environ["ARGUS_MODEL"])
    interpreted = interpret(text, request_id, client=active)
    return suggest_sites(interpreted, active)


def _expected_fields(intent) -> list[str]:
    fields = list(intent.expected_record_shape) or ["title", "url"]
    for criterion in intent.criteria:
        field = criterion.parameter
        if (
            isinstance(field, str)
            and field
            and field not in fields
            and field.replace("_", "a").isalnum()
        ):
            fields.append(field)
    return fields


def _open_parameters(intent) -> dict:
    parameters = {
        name: copy.deepcopy(origin.value)
        for name, origin in intent.parameters.items()
        if name != "site_choice"
        and not isinstance(origin.value, bool)
        and isinstance(origin.value, (str, int, float))
    }
    if not isinstance(parameters.get("query"), str) or not parameters["query"].strip():
        # The search terms are what the user asked FOR, i.e. the filter criteria
        # ("condos", "for sale", "in Toronto"), never the goal sentence: typing the
        # instruction into a site's search box returned articles about search
        # engines on the first live open-world run (2026-09-13).
        filters = [
            criterion.text.strip()
            for criterion in intent.criteria
            if criterion.kind == "filter" and isinstance(criterion.text, str)
        ]
        parameters["query"] = " ".join(filters) if filters else intent.goal
    return parameters


def open_plan(interpreted: InterpretedRequest, plan_id: str) -> Plan:
    """Plan each accepted open intent as one independent DOM ``open_search``."""
    if not any(intent.kind == "open" for intent in interpreted.intents):
        return planner.plan(interpreted, plan_id)
    if len(interpreted.intents) > MAX_OPEN_SUBTASKS:
        from argus.contracts import ContractError

        raise ContractError(
            f"open plan exceeds the {MAX_OPEN_SUBTASKS}-subtask cap",
            code="PLAN_TOO_LARGE",
        )

    registry_indices = [
        index
        for index, intent in enumerate(interpreted.intents)
        if intent.kind == "registry"
    ]
    registry_subtasks: dict[int, Subtask] = {}
    if registry_indices:
        registry_request = copy.deepcopy(interpreted)
        registry_request.intents = [
            copy.deepcopy(interpreted.intents[index]) for index in registry_indices
        ]
        registry_plan = planner.plan(registry_request, plan_id)
        for original_index, subtask in zip(
            registry_indices, registry_plan.subtasks, strict=True
        ):
            subtask.intent_index = original_index
            registry_subtasks[original_index] = subtask

    subtasks: list[Subtask] = []
    for index, intent in enumerate(interpreted.intents):
        if intent.kind == "registry":
            subtasks.append(registry_subtasks[index])
            continue
        domain = intent.target_domain or ""
        subtasks.append(
            Subtask(
                subtask_id=f"open-{index + 1}",
                intent_index=index,
                site_id=f"open:{domain}",
                operation="open_search",
                parameters=_open_parameters(intent),
                concurrency_group=f"open-{index + 1}",
                output_schema_id="open-records.v1",
                depends_on=[],
                success_conditions=[],
                preferred_tool="dom",
                kind="open",
                target_domain=intent.target_domain,
                goal=intent.goal,
                criteria=copy.deepcopy(intent.criteria),
                expected_record_shape=_expected_fields(intent),
            )
        )
    built = Plan(
        plan_id=plan_id,
        request_id=interpreted.request_id,
        subtasks=subtasks,
        created_at=datetime.now(UTC)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        planned_by="deterministic",
    )
    planner.validate_plan(built)
    return built


def normalise_runtime(value: str | None) -> str:
    """Map the ``ARGUS_RUNTIME`` value to one of :data:`RUNTIME_MODES`.

    ``live`` is accepted as the historical name of the deprecated three-site
    scraper and reported as ``scrape``.
    """
    mode = (value or "connected").strip().lower()
    if mode == "live":
        return "scrape"
    if mode not in RUNTIME_MODES:
        raise ValueError(
            f"ARGUS_RUNTIME={value!r} is not one of {', '.join(RUNTIME_MODES)}"
        )
    return mode


def ghost_api_healthy(base_url: str, timeout: float = 3.0) -> bool:
    """True when the Ghost API answers ``GET /health`` with HTTP 200."""
    url = base_url.rstrip("/") + "/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def connected_environment_problems(
    *, check_ghost: bool = True, sites_loader=None
) -> list[str]:
    """Return human-readable problems that must be fixed before ``connected`` runs.

    Empty list means the runtime may start. Nothing here opens a browser or calls a
    model; it only checks configuration and the Ghost health endpoint.
    """
    problems = [f"{name} is not set" for name in REQUIRED_ENV if not os.getenv(name)]
    browser = (os.getenv("WORKER_BROWSER") or "steel").lower()
    if browser == "steel" and not os.getenv("STEEL_API_KEY"):
        problems.append("STEEL_API_KEY is not set (WORKER_BROWSER=steel)")
    if browser == "local" and not os.getenv("WORKER_BROWSER_EXECUTABLE"):
        problems.append("WORKER_BROWSER_EXECUTABLE is not set (WORKER_BROWSER=local)")
    try:
        if sites_loader is None:
            from Agents.browser_worker.config import load_sites as sites_loader
        sites = sites_loader()
        if not sites:
            problems.append("no configured worker sites (WORKER_SITES_FILE)")
    except Exception as exc:  # noqa: BLE001 - reported as a startup problem, never raised
        problems.append(f"worker site config unavailable ({type(exc).__name__})")
    try:
        moderator_choice()
    except ValueError as exc:
        problems.append(str(exc))
    if moderator_choice_safe() == "module":
        try:
            from moderator.moderator import Moderator  # noqa: F401 - availability check
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            problems.append(
                f"moderator module unavailable ({type(exc).__name__}); set ARGUS_MODERATOR=stub to run with the deterministic stub"
            )
    ghost_url = os.getenv("GHOST_API_URL")
    if check_ghost and ghost_url and not ghost_api_healthy(ghost_url):
        problems.append(f"Ghost API at {ghost_url} did not answer /health")
    return problems


def moderator_choice() -> str:
    """``ARGUS_MODERATOR``: ``module`` (Thomas's adapted module, default) or ``stub``.

    ``stub`` is the deterministic :class:`argus.fakes.StubModerator`; it exists so
    the connected pipeline can be exercised before the module lands and is
    reported by ``/api/health`` so a stub run is never mistaken for the real one.
    """
    choice = (os.getenv("ARGUS_MODERATOR") or "module").strip().lower()
    if choice not in ("module", "stub"):
        raise ValueError(f"ARGUS_MODERATOR={choice!r} is not 'module' or 'stub'")
    return choice


def moderator_choice_safe() -> str:
    try:
        return moderator_choice()
    except ValueError:
        return "invalid"


def build_moderator(model_client=None):
    if moderator_choice() == "stub":
        from argus.fakes import StubModerator

        return StubModerator()
    from moderator.moderator import Moderator

    client = model_client
    if client is None:
        from argus.model_client import OpenAICompatibleClient

        client = OpenAICompatibleClient(model=os.environ["ARGUS_MODEL"])
    # Decision 4 (ARGUS-3): sessions are worker-owned and the DOM toolbox cannot
    # observe or interpret a page, so the moderator must not ask the controller to
    # verify; it defers to the independent validation stage instead.
    return Moderator(client, verification_available=False)


def build_connected_controller(
    store_root: str | Path,
    *,
    model_client=None,
    toolbox=None,
    ghost=None,
    moderator=None,
    max_concurrency: int = 2,
) -> Controller:
    """Compose the connected controller. Keyword arguments exist for tests."""
    if toolbox is None:
        from argus.adapters.dom_toolbox import DomToolbox

        toolbox = DomToolbox()
    if ghost is None:
        from argus.adapters.ghost_bridge import GhostBridge

        ghost = GhostBridge(toolbox)
    if moderator is None:
        moderator = build_moderator(model_client)
    return Controller(
        toolbox,
        moderator,
        ghost,
        JsonStore(store_root),
        interpret=lambda text, request_id: connected_interpret(
            text, request_id, client=model_client
        ),
        plan=open_plan,
        max_concurrency=max_concurrency,
    )
