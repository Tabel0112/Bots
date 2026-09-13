"""In-memory stand-ins for the three boundaries the controller calls out through.

Phase D of docs/hackathon/ARGUS-IMPLEMENTATION.md.  These let the whole run
execute offline, with no browser, no model call and no network:

* :class:`FakeModelClient` - the :mod:`argus.model_client` boundary reduced to a
  scripted list of results, so stage 1 and the open-world planner can be driven
  through every outcome (including a refusal) without an SDK or a key.
* :class:`FakePlannerClient` - the same boundary answering the open-world
  planner from a Plan JSON fixture, so ``python -m argus --plan-fixture`` and
  the tests can replay a scheduling offline.  It is only ever injected
  explicitly; nothing substitutes it for a real model call.
* :class:`FakeToolbox` - hands out session handles, answers ``run_subtask``
  from a small product catalog for registry subtasks and from
  :data:`OPEN_DATASET` for open-world ones, and can be scripted to force
  failures.
* :class:`StubModerator` - the three moderator callables reduced to rules, so
  the controller's intake, reconciliation and synthesis paths are exercised
  without a model.  It is a placeholder for Thomas's moderator.
  Reconciliation merges the accepted reports by record ``url``, so a
  search-then-open_results chain answers with one entry per result rather than
  two.  Synthesis applies the request's explicit criteria and names the ones it
  could not.
* :class:`FakeGhost` - always explores, validates registry records against the
  fixture ground truth and open-world records with the generic checks only, and
  compiles a candidate skill.  Modelled on Sting's simulated demo
  (``ghostapi/demo/ghost_demo.py``): the catalog rows and the shape of the
  compiled definition come from there.  His demo prices in CAD while
  ``argus.registry`` fixes USD, so the prices here are USD.

Nothing in this module is a claim about real behaviour.  The reports it
produces are synthetic: the ``worker`` field says ``fake`` and no evidence file
exists behind the observation IDs.  Every call is appended to ``self.calls`` so
a test can assert what the controller did.

Standard library only.  The worker report is built with
``WorkerReport.from_dict`` on ``argus/examples/worker_report.json`` so all
thirteen of the visual worker's required fields are present and the fake can
never drift out of that format.
"""

from __future__ import annotations

import copy
import json
import threading
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from argus import registry
from argus.contracts import (
    ERROR_CODES,
    AnswerSelection,
    Claim,
    ContractError,
    Criterion,
    InterpretedRequest,
    ModeratorDecision,
    Note,
    Plan,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)
from argus.model_client import MODEL_STATUSES, ModelResult

__all__ = [
    "CATALOG",
    "DEFAULT_SITE_ID",
    "OPEN_DATASET",
    "OPEN_RECORD_SHAPE",
    "SCRIPTED_OUTCOMES",
    "FakeGhost",
    "FakeModelClient",
    "FakePlannerClient",
    "FakeToolbox",
    "StubModerator",
]

#: The site every fake defaults to; its origin comes from the registry.
DEFAULT_SITE_ID = "demo-catalog"

_FIXTURE_PATH = Path(__file__).resolve().parent / "examples" / "worker_report.json"

#: The fixture catalog, five products.  Titles and prices are Sting's demo rows
#: (``ghostapi/demo/ghost_demo.py``) converted to the registry's fixed USD.
CATALOG: tuple[dict[str, Any], ...] = (
    {"index": 0, "title": "Studio headphones", "price": 129.0},
    {"index": 1, "title": "Travel headphones", "price": 79.0},
    {"index": 2, "title": "Reference headphones", "price": 249.0},
    {"index": 3, "title": "Mechanical keyboard", "price": 99.0},
    {"index": 4, "title": "Compact keyboard", "price": 49.0},
)

#: Record fields an open-world subtask gets when it declares no
#: ``expected_record_shape`` of its own.
OPEN_RECORD_SHAPE: tuple[str, ...] = ("title", "company", "url", "salary", "remote")

#: The open-world fixture, keyed by ``target_domain``.  A row's ``url`` is
#: ``https://<domain>/jobs/<index>``; ``keywords`` only feeds the query match
#: and is never emitted.  Seven rows so that the query "software engineering"
#: selects six (the manager row lacks "software") and there is something for
#: the moderator's filter, rank and limit criteria to act on.
OPEN_DATASET: dict[str, tuple[dict[str, Any], ...]] = {
    "jobs.example.com": (
        {
            "index": 1,
            "title": "Senior Software Engineer",
            "company": "Northwind",
            "salary": 185000,
            "remote": True,
            "keywords": "software engineering backend",
        },
        {
            "index": 2,
            "title": "Software Engineer, Platform",
            "company": "Contoso",
            "salary": 150000,
            "remote": False,
            "keywords": "software engineering platform",
        },
        {
            "index": 3,
            "title": "Staff Software Engineer",
            "company": "Fabrikam",
            "salary": 210000,
            "remote": True,
            "keywords": "software engineering staff",
        },
        {
            "index": 4,
            "title": "Software Engineering Intern",
            "company": "Northwind",
            "salary": 48000,
            "remote": False,
            "keywords": "software engineering intern",
        },
        {
            "index": 5,
            "title": "Frontend Software Engineer",
            "company": "Adatum",
            "salary": 132000,
            "remote": True,
            "keywords": "software engineering frontend",
        },
        {
            "index": 6,
            "title": "Site Reliability Engineer",
            "company": "Contoso",
            "salary": 160000,
            "remote": True,
            "keywords": "software engineering reliability",
        },
        {
            "index": 7,
            "title": "Engineering Manager",
            "company": "Adatum",
            "salary": 195000,
            "remote": False,
            "keywords": "engineering management",
        },
    ),
}

#: Outcomes :class:`FakeToolbox` can be scripted to force per subtask.
#:
#: ``target_not_found``  failed with a retryable ``TARGET_NOT_FOUND``
#: ``auth_required``     failed with ``AUTH_REQUIRED``, terminal per ARGUS.md
#: ``budget``            failed with ``BUDGET_EXCEEDED``, metrics over budget
#: ``raise``             raises instead of returning a report
#: ``empty``             succeeded with no records and no screenshot, the
#:                       thin-evidence case that drives the ``verify`` path
SCRIPTED_OUTCOMES = (
    "target_not_found",
    "auth_required",
    "budget",
    "raise",
    "empty",
)

#: ``scripted outcome -> (error code, retryable, message)``.
_SCRIPTED_FAILURES: dict[str, tuple[str, bool, str]] = {
    "target_not_found": (
        "TARGET_NOT_FOUND",
        True,
        "the result list was not found on the catalog page",
    ),
    "auth_required": (
        "AUTH_REQUIRED",
        False,
        "the catalog asked for a sign-in before showing results",
    ),
    "budget": (
        "BUDGET_EXCEEDED",
        False,
        "the subtask used more browser actions than its budget allowed",
    ),
}

#: Fixed clock for action timestamps, so two runs of a test compare equal.
_BASE_TIMESTAMP = 1789237040.0

#: Marks a record that a later subtask superseded while reconciling, so the
#: positions taken during the merge stay valid until the list is filtered.
_SUPERSEDED = object()


def _now() -> str:
    """Current UTC time in the fixtures' ``...Z`` format."""
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _origin(site_id: str) -> str:
    """The configured origin for a site, or the demo catalog's."""
    site = registry.SITES.get(site_id) or registry.SITES[DEFAULT_SITE_ID]
    return site["origin"]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _open_origin(subtask: Subtask) -> str:
    """The origin an open-world subtask browses: its target domain over https."""
    return f"https://{subtask.target_domain or ''}"


def _open_rows(domain: str | None) -> tuple[dict[str, Any], ...]:
    """The dataset rows for a target domain, each with its ``url`` filled in."""
    if not isinstance(domain, str):
        return ()
    rows = OPEN_DATASET.get(domain.strip().lower(), ())
    return tuple(
        dict(row, url=f"https://{domain.strip().lower()}/jobs/{row['index']}")
        for row in rows
    )


def _on_domain(url: Any, domain: str | None) -> bool:
    """True when ``url`` is a string whose host is ``domain`` or a subdomain of it."""
    if not isinstance(url, str) or not isinstance(domain, str) or not domain.strip():
        return False
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return False
    if not host:
        return False
    wanted = domain.strip().lower().removesuffix(".")
    host = host.lower().removesuffix(".")
    return host == wanted or host.endswith(f".{wanted}")


def _records_of(report: WorkerReport) -> list[dict[str, Any]]:
    """The report's findings as a list of records, or empty if it has none."""
    findings = report.findings
    return list(findings) if isinstance(findings, list) else []


def _screenshots_of(report: WorkerReport) -> list[str]:
    """The report's evidence screenshots, tolerating a report without any."""
    evidence = report.evidence if isinstance(report.evidence, Mapping) else {}
    shots = evidence.get("screenshots")
    return [str(ref) for ref in shots] if isinstance(shots, list) else []


def _verifications_of(report: WorkerReport) -> list[str]:
    """Observation IDs of the controller's own read-only verifications.

    ``Controller._verify`` appends one entry per verification to
    ``evidence["verifications"]`` so the next assessment can see what it could
    not see the first time.  Without reading them the stub would answer
    ``verify`` a second time and the controller would trip its verification cap,
    so a thin-evidence report could never be accepted.
    """
    evidence = report.evidence if isinstance(report.evidence, Mapping) else {}
    entries = evidence.get("verifications")
    refs: list[str] = []
    for entry in entries if isinstance(entries, list) else []:
        observation = (
            entry.get("observation_id") if isinstance(entry, Mapping) else None
        )
        if isinstance(observation, str) and observation not in refs:
            refs.append(observation)
    return refs


class FakeModelClient:
    """A scripted :class:`argus.model_client.ModelClient`.  Never calls anything.

    Construct it with the :class:`~argus.model_client.ModelResult` each call
    should return, in order - one result, or a list for a caller that calls
    more than once.  Running out is an error rather than a silent repeat, so a
    test that expects two calls cannot pass while making one.

    Every call is appended to :attr:`calls` as a mapping with ``system``,
    ``user``, ``output_model`` and ``max_tokens``, so a test can assert what the
    caller asked for - the prompt it sent, the schema it demanded and the bound
    it set - without asserting anything about a model's judgement.
    """

    def __init__(self, results: Sequence[ModelResult] | ModelResult) -> None:
        if isinstance(results, ModelResult):
            results = [results]
        scripted = list(results)
        for index, result in enumerate(scripted):
            if not isinstance(result, ModelResult):
                raise ContractError(
                    f"FakeModelClient result {index} is {type(result).__name__}, "
                    f"not a ModelResult"
                )
        self.results: list[ModelResult] = scripted
        self.calls: list[dict[str, Any]] = []

    def parse_json(
        self, system: str, user: str, output_model: type, max_tokens: int
    ) -> ModelResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "output_model": output_model,
                "max_tokens": max_tokens,
            }
        )
        if len(self.calls) > len(self.results):
            raise ContractError(
                f"FakeModelClient was scripted with {len(self.results)} result(s) "
                f"but was called {len(self.calls)} time(s)"
            )
        return self.results[len(self.calls) - 1]


#: The scheduling fields of one planner step, exactly as ``planner._output_model``
#: declares them.  Everything else in a Plan JSON subtask - parameters, target,
#: goal, criteria, record shape, kind, output schema - is controller-owned and
#: is dropped, so the fake can only ever hand the planner what a model could.
_STEP_FIELDS: tuple[str, ...] = (
    "subtask_id",
    "intent_index",
    "operation",
    "depends_on",
    "inputs_from",
    "concurrency_group",
    "success_conditions",
    "preferred_tool",
)

#: ``Subtask`` defaults for the optional scheduling fields, so a Plan JSON that
#: omits them (the way ``Subtask.from_dict`` allows) still yields a full step.
_STEP_DEFAULTS: dict[str, Any] = {
    "depends_on": [],
    "inputs_from": {},
    "success_conditions": [],
    "preferred_tool": "dom",
}


class FakePlannerClient:
    """A :class:`argus.model_client.ModelClient` that replays a Plan JSON's scheduling.

    ``plan_payload`` is a Plan object (``argus/examples/plan_open_chain.json``
    or any ``Plan.to_dict()``).  Each subtask is reduced to the planner's
    ``Step`` schema: the seven scheduling fields only, with ``inputs_from``
    converted from the contract's ``{parameter: {subtask_id, field}}`` mapping
    to the schema's list of ``{parameter, subtask_id, field}`` bindings.  Context
    the fixture carries - parameters, target domain, goal, criteria, record
    shape, caps - never reaches the planner, which copies those from the
    accepted request the same way it would after a real call.

    ``status`` overrides the outcome for refusal and truncation tests: anything
    but ``"ok"`` is returned as that status with no parsed content.  Every call
    is recorded in :attr:`calls` so a test can assert the planner was called
    once, or not at all for a registry-only request.  The caller constructs it
    explicitly; ``planner.plan_open`` never substitutes it for a real client.
    """

    def __init__(self, plan_payload: Mapping[str, Any], status: str = "ok") -> None:
        if status not in MODEL_STATUSES:
            raise ContractError(
                f"unknown model result status {status!r}; expected one of "
                f"{', '.join(MODEL_STATUSES)}"
            )
        if not isinstance(plan_payload, Mapping) or not isinstance(
            plan_payload.get("subtasks"), list
        ):
            raise ContractError(
                "FakePlannerClient needs a Plan object with a subtasks list"
            )
        self.status = status
        self.steps: list[dict[str, Any]] = [
            self._step(index, raw) for index, raw in enumerate(plan_payload["subtasks"])
        ]
        self.calls: list[dict[str, Any]] = []

    @staticmethod
    def _step(index: int, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, Mapping):
            raise ContractError(f"FakePlannerClient subtask {index} is not an object")
        step: dict[str, Any] = {}
        for name in _STEP_FIELDS:
            if name in raw:
                step[name] = copy.deepcopy(raw[name])
            elif name in _STEP_DEFAULTS:
                step[name] = copy.deepcopy(_STEP_DEFAULTS[name])
            else:
                raise ContractError(
                    f"FakePlannerClient subtask {index} lacks scheduling field {name!r}"
                )
        bindings = step["inputs_from"]
        if isinstance(bindings, Mapping):
            step["inputs_from"] = [
                {
                    "parameter": parameter,
                    "subtask_id": source.get("subtask_id")
                    if isinstance(source, Mapping)
                    else None,
                    "field": source.get("field")
                    if isinstance(source, Mapping)
                    else None,
                }
                for parameter, source in bindings.items()
            ]
        elif not isinstance(bindings, list):
            raise ContractError(
                f"FakePlannerClient subtask {index}: inputs_from must be an object or a list"
            )
        return step

    def parse_json(
        self, system: str, user: str, output_model: type, max_tokens: int
    ) -> ModelResult:
        self.calls.append(
            {
                "system": system,
                "user": user,
                "output_model": output_model,
                "max_tokens": max_tokens,
            }
        )
        if self.status != "ok":
            return ModelResult(
                status=self.status,
                raw_text=f"fake planner scripted as {self.status}",
                model="fake-planner",
            )
        return ModelResult(
            status="ok",
            parsed={"subtasks": copy.deepcopy(self.steps)},
            raw_text=None,
            model="fake-planner",
        )


class FakeToolbox:
    """A toolbox that never opens a browser.  Implements :class:`argus.interfaces.Toolbox`.

    Sessions are handed out as ``fake-session-N`` and tracked, so a test can
    assert the controller closed every one of them and closed each only once.
    ``run_subtask`` answers registry subtasks from :data:`CATALOG`, filtered by
    the subtask's ``query`` substring and ``max_price`` (and capped at
    ``max_results`` when the subtask carries one).

    Open-world subtasks (``kind == "open"``) are answered from
    :data:`OPEN_DATASET` for their ``target_domain``: a search keeps the rows
    whose title, company or keywords contain every word of ``query``, and an
    ``open_results`` operation with a ``result_urls`` parameter returns one
    record per URL in that order, each cited to its own observation.  Records
    carry exactly the subtask's ``expected_record_shape`` (or
    :data:`OPEN_RECORD_SHAPE`) plus ``source_observation_id``; every URL stays
    on the target domain.  An unknown domain yields an explicit empty result.

    ``script`` maps a ``subtask_id`` to one of :data:`SCRIPTED_OUTCOMES`, or to
    a sequence of them consumed one per call (``None`` for a normal success, and
    success again once the sequence is exhausted) so a retry can be scripted to
    succeed on its second attempt.

    Every report carries ``request_id`` from ``SubtaskInput.request_id`` and
    ``session_handle`` from ``SubtaskInput.session_handle``, which is what the
    controller's intake requires of a real worker.
    """

    def __init__(self, script: Mapping[str, Any] | None = None) -> None:
        self.script: dict[str, Any] = dict(script or {})
        self._validate_script()
        #: Every call, in order, as ``(method name, *arguments)``.
        self.calls: list[tuple[Any, ...]] = []
        #: ``handle -> True`` while the session is open, ``False`` once closed.
        self.sessions: dict[str, bool] = {}
        self._queues: dict[str, list[Any]] = {}
        self._session_n = 0
        self._observation_n = 0
        self._observe_n = 0
        self._lock = threading.Lock()

    # -- helpers ---------------------------------------------------------

    def _validate_script(self) -> None:
        for subtask_id, forced in self.script.items():
            values = forced if isinstance(forced, (list, tuple)) else [forced]
            for value in values:
                if value is not None and value not in SCRIPTED_OUTCOMES:
                    raise ContractError(
                        f"script[{subtask_id!r}]: unknown outcome {value!r}; "
                        f"expected one of {', '.join(SCRIPTED_OUTCOMES)}"
                    )

    @property
    def open_sessions(self) -> list[str]:
        """Handles that are still open, in the order they were opened."""
        return [handle for handle, is_open in self.sessions.items() if is_open]

    @property
    def closed_sessions(self) -> list[str]:
        """Handles that have been closed."""
        return [handle for handle, is_open in self.sessions.items() if not is_open]

    def calls_named(self, name: str) -> list[tuple[Any, ...]]:
        """Every recorded call to one method."""
        return [call for call in self.calls if call[0] == name]

    def _require_open(self, handle: str) -> None:
        state = self.sessions.get(handle)
        if state is None:
            raise ContractError(f"unknown session handle {handle!r}")
        if not state:
            raise ContractError(f"session {handle!r} is already closed")

    def _next_forced(self, subtask_id: str) -> str | None:
        with self._lock:
            forced = self.script.get(subtask_id)
            if isinstance(forced, (list, tuple)):
                queue = self._queues.setdefault(subtask_id, list(forced))
                return queue.pop(0) if queue else None
            return forced

    # -- Toolbox ---------------------------------------------------------

    def open_session(self, site_id: str) -> str:
        """Return a fresh ``fake-session-N`` handle and record it as open."""
        with self._lock:
            self._session_n += 1
            handle = f"fake-session-{self._session_n}"
            self.sessions[handle] = True
            self.calls.append(("open_session", site_id, handle))
        return handle

    def close_session(self, handle: str) -> None:
        """Mark the session closed; raise on an unknown or already closed handle."""
        with self._lock:
            self.calls.append(("close_session", handle))
            self._require_open(handle)
            self.sessions[handle] = False

    def run_subtask(self, subtask_input: SubtaskInput) -> WorkerReport:
        """Return a synthetic :class:`~argus.contracts.WorkerReport` for one subtask."""
        subtask = subtask_input.subtask
        with self._lock:
            self.calls.append(
                (
                    "run_subtask",
                    subtask.subtask_id,
                    subtask_input.mode,
                    subtask.preferred_tool,
                    subtask_input.session_handle,
                )
            )
        forced = self._next_forced(subtask.subtask_id)
        if forced == "raise":
            raise RuntimeError(
                f"fake toolbox failed while running {subtask.subtask_id}"
            )
        return self._build_report(subtask_input, forced)

    def observe(self, handle: str) -> str:
        """Return a fresh ``observation-fake-N`` ID for the open session."""
        with self._lock:
            self._observe_n += 1
            observation = f"observation-fake-{self._observe_n}"
            self.calls.append(("observe", handle, observation))
            self._require_open(handle)
        return observation

    def dom_interpret(self, handle: str, question: str) -> str:
        """Echo the question back as the DOM's answer."""
        with self._lock:
            self.calls.append(("dom_interpret", handle, question))
            self._require_open(handle)
        return f"dom: {question} -> confirmed on the fake catalog page"

    def vision_interpret(self, handle: str, question: str) -> str:
        """Echo the question back as the screenshot's answer."""
        with self._lock:
            self.calls.append(("vision_interpret", handle, question))
            self._require_open(handle)
        return f"vision: {question} -> confirmed on the fake catalog screenshot"

    # -- report construction ---------------------------------------------

    def records_for(
        self, subtask: Subtask, observation: str | None = None
    ) -> list[dict[str, Any]]:
        """The catalog rows a subtask's parameters select, as records.

        Filtered by ``query`` as a case-insensitive substring of the title and
        by ``max_price`` when given, then capped at ``max_results``.  Record
        keys match ``argus/examples/final_answer.json``.
        """
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        query = str(params.get("query") or "").strip().casefold()
        max_price = params.get("max_price")
        max_results = params.get("max_results")
        origin = _origin(subtask.site_id)
        retrieved_at = _now()

        rows = [row for row in CATALOG if query in row["title"].casefold()]
        if _is_number(max_price):
            rows = [row for row in rows if row["price"] <= max_price]
        if isinstance(max_results, int) and not isinstance(max_results, bool):
            rows = rows[: max(max_results, 0)]
        return [
            {
                "title": row["title"],
                "price": row["price"],
                "currency": "USD",
                "url": f"{origin}/products/{row['index']}",
                "source_observation_id": observation,
                "retrieved_at": retrieved_at,
            }
            for row in rows
        ]

    @staticmethod
    def _shape(
        subtask: Subtask, row: Mapping[str, Any], observation: str | None
    ) -> dict[str, Any]:
        """One open-world record in the subtask's expected shape, cited."""
        shape = subtask.expected_record_shape or list(OPEN_RECORD_SHAPE)
        record: dict[str, Any] = {name: copy.deepcopy(row.get(name)) for name in shape}
        record["source_observation_id"] = observation
        return record

    def open_records_for(
        self, subtask: Subtask, observations: Sequence[str | None]
    ) -> list[dict[str, Any]]:
        """The dataset rows an open-world subtask selects, as shaped records.

        ``observations`` supplies the observation each record cites: for
        ``open_results`` one per URL in order (the last one repeats if fewer
        were given), otherwise the first one for every record.
        """
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        rows = _open_rows(subtask.target_domain)
        observations = list(observations) or [None]

        if subtask.operation == "open_results" and isinstance(
            params.get("result_urls"), list
        ):
            by_url = {row["url"]: row for row in rows}
            records = []
            for index, url in enumerate(params["result_urls"]):
                row = dict(by_url.get(url) or {}, url=url)
                observation = observations[min(index, len(observations) - 1)]
                records.append(self._shape(subtask, row, observation))
            return records

        query = str(params.get("query") or "").casefold().split()
        selected = [
            row
            for row in rows
            if all(
                word
                in f"{row['title']} {row['company']} {row.get('keywords', '')}".casefold()
                for word in query
            )
        ]
        max_results = params.get("max_results")
        if isinstance(max_results, int) and not isinstance(max_results, bool):
            selected = selected[: max(max_results, 0)]
        return [self._shape(subtask, row, observations[0]) for row in selected]

    def _next_observation(self) -> tuple[str, int]:
        with self._lock:
            self._observation_n += 1
            n = self._observation_n
        return f"observation-{n - 1:03d}.png", n

    def _action(
        self,
        template: dict[str, Any],
        *,
        step: int,
        name: str,
        payload: dict[str, Any],
        url: str,
        outcome: str = "succeeded",
        semantic_target: str | None = None,
        observation_before: str | None = None,
        observation_after: str | None = None,
    ) -> dict[str, Any]:
        """One action record in the visual worker's shape, from the fixture's keys."""
        action = dict(template)
        action.update(
            step_id=f"step-{step:03d}",
            action={"name": name, "input": payload},
            semantic_target=semantic_target,
            observation_before=observation_before,
            observation_after=observation_after,
            url=url,
            outcome=outcome,
            timestamp=_BASE_TIMESTAMP + step,
            worker_reasoning=None,
        )
        return action

    def _build_report(
        self, subtask_input: SubtaskInput, forced: str | None
    ) -> WorkerReport:
        subtask = subtask_input.subtask
        data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        template = data["actions"][0]
        is_open = subtask.kind == "open"
        origin = _open_origin(subtask) if is_open else _origin(subtask.site_id)
        search_url = f"{origin}/search" if is_open else f"{origin}/catalog"
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        query = params.get("query")
        result_urls = params.get("result_urls") if is_open else None
        opens_results = subtask.operation == "open_results" and isinstance(
            result_urls, list
        )

        observation, observation_n = self._next_observation()
        # ``open_results`` cites one further observation per opened URL.
        opened: list[str] = (
            [self._next_observation()[0] for _ in result_urls] if opens_results else []
        )

        if forced == "empty":
            # Nothing found and nothing captured: records and evidence are both
            # empty, which is what StubModerator answers with ``verify``.
            observation = None
            opened = []
            records: list[dict[str, Any]] = []
        elif is_open:
            records = self.open_records_for(subtask, opened or [observation])
        else:
            records = self.records_for(subtask, observation)

        actions = [
            self._action(
                template,
                step=0,
                name="open_url",
                payload={"url": search_url},
                url=search_url,
                observation_after=observation,
            ),
        ]
        if opens_results:
            for index, url in enumerate(result_urls):
                actions.append(
                    self._action(
                        template,
                        step=len(actions),
                        name="open_url",
                        payload={"url": url},
                        url=str(url),
                        observation_before=observation
                        if index == 0
                        else opened[index - 1],
                        observation_after=opened[index] if opened else None,
                    )
                )
        else:
            actions.append(
                self._action(
                    template,
                    step=1,
                    name="type",
                    payload={"text": query},
                    url=search_url,
                    semantic_target="Search jobs" if is_open else "Search products",
                    observation_before=observation,
                )
            )
        last_url = str(result_urls[-1]) if opens_results and result_urls else search_url
        last_observation = opened[-1] if opened else observation
        subject = f"site {subtask.target_domain}" if is_open else "catalog"
        noun = "record" if is_open else "product"

        if forced in _SCRIPTED_FAILURES:
            code, retryable, message = _SCRIPTED_FAILURES[forced]
            outcome = "failed"
            summary = f"The subtask did not finish: {message}."
            findings = None
            actions.append(
                self._action(
                    template,
                    step=len(actions),
                    name="extract",
                    payload={"schema": subtask.output_schema_id},
                    url=last_url,
                    outcome="failed",
                    observation_before=last_observation,
                )
            )
            typed_failures = [
                TypedError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    step_id=actions[-1]["step_id"],
                    evidence_refs=[observation] if observation else [],
                )
            ]
            failures = [{"step_id": actions[-1]["step_id"], "reason": message}]
        else:
            outcome = "succeeded"
            findings = records
            if records:
                titles = ", ".join(str(record.get("title")) for record in records)
                summary = (
                    f"The fake {subject} returned {len(records)} {noun}(s) for "
                    f"{query!r}: {titles}."
                )
            else:
                summary = f"The fake {subject} returned no {noun}s for {query!r}."
            actions.append(
                self._action(
                    template,
                    step=len(actions),
                    name="extract",
                    payload={"schema": subtask.output_schema_id},
                    url=last_url,
                    observation_before=last_observation,
                )
            )
            actions.append(
                self._action(
                    template,
                    step=len(actions),
                    name="finished",
                    payload={"content": summary},
                    url=last_url,
                    observation_before=last_observation,
                )
            )
            typed_failures = []
            failures = []

        action_count = len(actions)
        elapsed_ms = 1000 * action_count
        if forced == "budget":
            # Over the lent budget, so the controller's own check also trips.
            action_count = subtask_input.budget.max_actions + 1
            elapsed_ms = int(subtask_input.budget.max_seconds * 1000) + 1

        data.update(
            worker="fake",
            worker_model="argus.fakes.FakeToolbox",
            # The report echoes the request identity the controller dispatched
            # with; a payload from before the field existed falls back to the
            # run ID, which the controller then rejects at intake.
            request_id=(
                subtask_input.request_id
                if subtask_input.request_id is not None
                else subtask_input.run_id
            ),
            subtask_id=subtask.subtask_id,
            subtask=self._subtask_text(subtask),
            outcome=outcome,
            summary=summary,
            findings=findings,
            actions=actions,
            evidence={
                "session_id": f"fake-browser-{observation_n}",
                "session_replay_url": None,
                "screenshots": ([observation] if observation else []) + opened,
                "network_requests": [
                    {
                        "type": "network",
                        "ts": _BASE_TIMESTAMP,
                        "method": "GET",
                        "url": search_url,
                        "status": 200,
                        "resource_type": "document",
                        "post_data": None,
                    }
                ],
                "trace_file": None,
            },
            metrics={
                "elapsed_ms": elapsed_ms,
                "browser_action_count": action_count,
                "model_call_count": 0,
                "token_counts": None,
            },
            failures=failures,
            session_handle=subtask_input.session_handle,
            typed_failures=[failure.to_dict() for failure in typed_failures],
        )
        return WorkerReport.from_dict(data)

    @staticmethod
    def _subtask_text(subtask: Subtask) -> str:
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        detail = ", ".join(
            f"{name}={value!r}" for name, value in sorted(params.items())
        )
        return (
            f"{subtask.operation} on {subtask.site_id} with {detail or 'no parameters'}"
        )


class StubModerator:
    """The three moderator callables as rules, standing in for Thomas's moderator.

    Implements :class:`argus.interfaces.Moderator`.  It makes no model call and
    reads nothing but the report it is handed, so its judgements are shallow by
    design: a run driven by this stub proves the controller's plumbing, not that
    an answer is any good.  Every decision uses the vocabulary
    ``contracts.MODERATOR_DECISIONS`` allows for its stage.

    ``reconcile`` merges the accepted reports' records by ``url``: a later
    subtask's record supersedes an earlier subtask's record for the same URL,
    and records without a URL are carried through untouched.

    ``retry_codes`` is off by default: a failed report is answered with ``fail``
    carrying its first typed failure.  Set it to the codes ARGUS.md lets the
    moderator retry (``TARGET_NOT_FOUND``, ``TARGET_AMBIGUOUS``,
    ``EXTRACTION_FAILED``) to exercise the controller's retry path.  The cap of
    one retry per subtask is the controller's, never the moderator's.
    """

    def __init__(self, retry_codes: Sequence[str] = ()) -> None:
        for code in retry_codes:
            if code not in ERROR_CODES:
                raise ContractError(f"unknown error code {code!r}")
        self.retry_codes = tuple(retry_codes)
        #: Every call, in order, as ``(method name, *identifying arguments)``.
        self.calls: list[tuple[Any, ...]] = []

    def assess_report(
        self,
        subtask: Subtask,
        report: WorkerReport,
        success_conditions: list[str],
    ) -> ModeratorDecision:
        """Stage 7: accept a succeeded report with evidence, else verify or fail.

        Evidence is the report's own screenshots or, on the second look, the
        observation the controller took for a verification it was asked for.
        """
        self.calls.append(("assess_report", subtask.subtask_id, report.outcome))
        screenshots = _screenshots_of(report)
        verifications = _verifications_of(report)
        records = _records_of(report)

        if report.outcome == "succeeded" and (screenshots or verifications):
            return ModeratorDecision(
                stage="assess",
                decision="accept",
                reason=(
                    f"{len(records)} record(s) with a source observation from this run; "
                    f"success conditions checked: {len(success_conditions)}."
                    + (
                        f" Evidence comes from {len(verifications)} controller "
                        "verification(s), not from the worker."
                        if verifications and not screenshots
                        else ""
                    )
                ),
                evidence_refs=screenshots or verifications,
            )

        if report.outcome == "succeeded":
            return ModeratorDecision(
                stage="assess",
                decision="verify",
                reason="The report succeeded but carries no screenshot, so nothing confirms it.",
                next_action={
                    "question": (
                        "Does the page show the search results for "
                        f"{(subtask.parameters or {}).get('query')!r}, or an explicit empty state?"
                    ),
                    "success_conditions": list(success_conditions),
                    "tool": "vision" if subtask.preferred_tool == "dom" else "dom",
                },
            )

        failure = (
            report.typed_failures[0]
            if report.typed_failures
            else TypedError(
                code="EXTRACTION_FAILED",
                message=f"The report outcome is {report.outcome!r} and carries no typed failure.",
                retryable=False,
                step_id=None,
                evidence_refs=screenshots,
            )
        )
        decision = "retry_other_path" if failure.code in self.retry_codes else "fail"
        return ModeratorDecision(
            stage="assess",
            decision=decision,
            reason=f"{failure.code}: {failure.message}",
            evidence_refs=list(failure.evidence_refs) or screenshots,
            next_action={"failure": failure.to_dict()},
        )

    def reconcile(self, plan: Plan, reports: list[WorkerReport]) -> ModeratorDecision:
        """Stage 8: merge the reports' records; name missing subtasks as gaps.

        Records are merged by ``url`` rather than concatenated: see
        :meth:`_merge_records`.  Independent subtasks share no URL, so their
        records are simply appended one report after another.
        """
        subtask_ids = [report.subtask_id for report in reports]
        self.calls.append(("reconcile", tuple(subtask_ids)))

        findings, superseded = self._merge_records(reports)
        evidence_refs: list[str] = []
        for report in reports:
            for ref in _screenshots_of(report):
                if ref not in evidence_refs:
                    evidence_refs.append(ref)

        reported = set(subtask_ids)
        gaps = [
            {
                "subtask_id": subtask.subtask_id,
                "gap": "no accepted report for this subtask",
                "resolution": "report_as_gap",
            }
            for subtask in plan.subtasks
            if subtask.subtask_id not in reported
        ]
        return ModeratorDecision(
            stage="reconcile",
            decision="merged",
            reason=(
                f"Merged {len(findings)} record(s) from subtask(s) "
                f"{', '.join(subtask_ids) or 'none'}"
                + (
                    f"; {superseded} earlier record(s) were superseded by a later "
                    "subtask's record for the same url"
                    if superseded
                    else ""
                )
                + "; the stub compares nothing else, so it finds no conflicts."
            ),
            evidence_refs=evidence_refs,
            next_action={
                "subtask_ids": subtask_ids,
                "findings": findings,
                "gaps": gaps,
                "conflicts": [],
            },
        )

    @staticmethod
    def _merge_records(reports: Sequence[WorkerReport]) -> tuple[list[Any], int]:
        """Merge the reports' records by ``url``; count the superseded ones.

        A dependent chain reports the same thing twice: ``search`` finds a
        result and ``open_results`` opens it and comes back with more of its
        fields.  Concatenating both reports would answer with every result
        duplicated, so a later report's record *supersedes* an earlier record
        carrying the same non-empty ``url``, keeping the later one because it is
        the more detailed of the two.  The survivor takes the later report's
        position, so the merged order follows the last report that mentioned
        each record.  A record with no ``url`` - anything that is not an object,
        or an object without one - is never matched against another and is kept
        exactly where it was.
        """
        merged: list[Any] = []
        position: dict[str, int] = {}
        superseded = 0
        for report in reports:
            for record in _records_of(report):
                url = record.get("url") if isinstance(record, Mapping) else None
                key = url.strip() if isinstance(url, str) and url.strip() else None
                if key is None:
                    merged.append(record)
                    continue
                if key in position:
                    merged[position[key]] = _SUPERSEDED
                    superseded += 1
                position[key] = len(merged)
                merged.append(record)
        return [record for record in merged if record is not _SUPERSEDED], superseded

    def synthesize(
        self,
        interpreted: InterpretedRequest,
        records: list[Any],
        validation: dict[str, Any],
        evidence: list[str],
        failures: list[TypedError],
    ) -> AnswerSelection:
        """Stage 10: select records and fields without producing prose.

        The request's explicit criteria are applied first, in order: ``filter``
        keeps records whose named field is truthy (or equals the criterion's
        ``value`` when its parameter is ``{"field", "value"}``), ``rank`` sorts
        descending on the named field and ``limit`` truncates.  A criterion
        whose field is absent from the records, or that names no field, is not
        applied and is returned as a typed ``criterion_not_applied`` note, so
        the controller can render a caveat without trusting moderator prose.
        The selected ``records`` are the records after the criteria.
        """
        self.calls.append(("synthesize", interpreted.request_id, len(records)))
        claims: list[Claim] = []
        notes: list[Note] = []

        criteria = [
            (f"intent-{intent_index}:criterion-{criterion_index}", criterion)
            for intent_index, intent in enumerate(interpreted.intents)
            for criterion_index, criterion in enumerate(intent.criteria)
        ]
        selected, not_applied = self._apply_criteria(criteria, list(records))
        notes.extend(Note("criterion_not_applied", subject) for subject in not_applied)

        record_indices: list[int] = []
        for original_index, record in selected:
            if isinstance(record, Mapping):
                fields = [name for name in record if name != "source_observation_id"]
                observation = record.get("source_observation_id")
            else:
                fields = []
                observation = None
            if not fields or not isinstance(observation, str):
                continue
            record_indices.append(original_index)
            claims.append(Claim(record_index=len(record_indices) - 1, fields=fields))

        return AnswerSelection(
            record_indices=record_indices, claims=claims, notes=notes
        )

    @staticmethod
    def _apply_criteria(
        criteria: Sequence[tuple[str, Criterion]], records: list[Any]
    ) -> tuple[list[tuple[int, Any]], list[str]]:
        """Filter, rank and limit records; return IDs of criteria not applied."""
        not_applied: list[str] = []
        indexed = list(enumerate(records))

        def rows_carry(field: Any) -> bool:
            return bool(indexed) and all(
                isinstance(record, Mapping) and field in record for _, record in indexed
            )

        def skipped(subject: str) -> None:
            not_applied.append(subject)

        ordered = sorted(
            criteria,
            key=lambda item: {
                "filter": 0,
                "rank": 1,
                "limit": 2,
            }.get(item[1].kind, 3),
        )
        for subject, criterion in ordered:
            parameter = criterion.parameter
            if criterion.kind == "limit":
                if (
                    not isinstance(parameter, int)
                    or isinstance(parameter, bool)
                    or parameter < 0
                ):
                    skipped(subject)
                    continue
                indexed = indexed[:parameter]
                continue

            field, wanted, exact = parameter, None, False
            if isinstance(parameter, Mapping) and "field" in parameter:
                field, wanted, exact = (
                    parameter["field"],
                    parameter.get("value"),
                    "value" in parameter,
                )
            if not isinstance(field, str) or not field.strip():
                skipped(subject)
                continue
            if not rows_carry(field):
                skipped(subject)
                continue
            if criterion.kind == "filter":
                indexed = [
                    (index, record)
                    for index, record in indexed
                    if (record[field] == wanted if exact else bool(record[field]))
                ]
            elif criterion.kind == "rank":
                indexed = sorted(
                    indexed, key=lambda item: _rank_key(item[1][field]), reverse=True
                )
        return indexed, not_applied


def _rank_key(value: Any) -> tuple[int, Any]:
    """An ordering that never raises: numbers above everything else, then text."""
    if _is_number(value):
        return (1, value)
    return (0, str(value))


class FakeGhost:
    """Skill matching, validation and compilation without a skill store.

    Implements :class:`argus.interfaces.Ghost`.  ``match`` always explores,
    because the fake holds no qualified skills.  ``validate`` checks registry
    records against the fixture's ground truth the way Sting's ``verify`` does
    - named checks, independent of the steps that produced the records - and
    the moderator cannot override a failure.  Open-world subtasks get the
    generic checks only (every record cites an observation of this report,
    results present or an explicit empty state, the query evidenced by an
    observation, every URL on the target domain), read from the controller's
    ``report_context`` keyword; completeness stays unverified.  ``compile``
    stages a candidate skill in the shape of his ``simulated_discovery``
    definition; session handles and evidence never enter it.
    """

    def __init__(
        self, site_id: str = DEFAULT_SITE_ID, origin: str | None = None
    ) -> None:
        self.site_id = site_id
        self.origin = origin or _origin(site_id)
        #: Every call, in order, as ``(method name, *identifying arguments)``.
        self.calls: list[tuple[Any, ...]] = []

    def _origin_for(self, subtask: Subtask) -> str:
        if subtask.site_id == self.site_id or subtask.site_id not in registry.SITES:
            return self.origin
        return _origin(subtask.site_id)

    def match(self, subtask: Subtask, skills: list[dict[str, Any]]) -> dict[str, Any]:
        """Stage 4: always ``explore``; the fake has no qualified skill to reuse."""
        self.calls.append(("match", subtask.subtask_id, len(skills or [])))
        return {
            "decision": "explore",
            "reason": "no qualified skills in fake",
            "skill": None,
        }

    def validate(
        self,
        subtask: Subtask,
        records: list[Any],
        evidence: list[str],
        *,
        report_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stage 9: ``passed`` only when every record satisfies every named check.

        ``report_context`` is what the controller passes for open-world
        subtasks only (``run_id``, ``subtask_id``, the report's ``evidence``
        without any session handle, ``empty_state``); registry calls keep the
        three-argument form and it stays ``None``.
        """
        self.calls.append(("validate", subtask.subtask_id, len(records or [])))
        if subtask.kind == "open":
            return self._validate_open(subtask, records, evidence, report_context)
        origin = self._origin_for(subtask)
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        max_price = params.get("max_price")
        rows = [record for record in (records or []) if isinstance(record, Mapping)]

        checks = {
            "records_are_objects": len(rows) == len(records or []),
            "title_present": all(
                isinstance(row.get("title"), str) and row["title"].strip()
                for row in rows
            ),
            "price_present": all(_is_number(row.get("price")) for row in rows),
            "currency_usd": all(row.get("currency") == "USD" for row in rows),
            "url_on_site": all(
                isinstance(row.get("url"), str) and row["url"].startswith(f"{origin}/")
                for row in rows
            ),
            "price_within_max": (
                True
                if not _is_number(max_price)
                else all(
                    _is_number(row.get("price")) and row["price"] <= max_price
                    for row in rows
                )
            ),
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        return {
            "status": "passed" if not failed else "failed",
            "checks": checks,
            "failed_checks": failed,
            "record_count": len(records or []),
            "evidence_refs": list(evidence or []),
            "scope": "synthetic fixture only; no live website was visited",
        }

    @staticmethod
    def _validate_open(
        subtask: Subtask,
        records: list[Any],
        evidence: list[str],
        report_context: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The generic open-world checks; nothing operation-specific."""
        context = report_context if isinstance(report_context, Mapping) else {}
        known: set[str] = {str(ref) for ref in (evidence or [])}
        report_evidence = context.get("evidence")
        if isinstance(report_evidence, Mapping):
            shots = report_evidence.get("screenshots")
            for shot in shots if isinstance(shots, list) else []:
                known.add(str(shot))
            verifications = report_evidence.get("verifications")
            for entry in verifications if isinstance(verifications, list) else []:
                observation = (
                    entry.get("observation_id") if isinstance(entry, Mapping) else None
                )
                if isinstance(observation, str):
                    known.add(observation)
        rows = [record for record in (records or []) if isinstance(record, Mapping)]
        empty_state = context.get("empty_state") is True

        checks = {
            "records_are_objects": len(rows) == len(records or []),
            "records_cite_observations": all(
                isinstance(row.get("source_observation_id"), str)
                and row["source_observation_id"] in known
                for row in rows
            ),
            "results_present_or_empty_state": bool(rows) or empty_state,
            # The fake accepts any observation of this report as evidence that
            # the query or filter took effect; a real Ghost reads the page.
            "query_visibly_applied": bool(known),
            "urls_on_target_domain": all(
                _on_domain(row["url"], subtask.target_domain)
                for row in rows
                if "url" in row
            ),
        }
        failed = sorted(name for name, passed in checks.items() if not passed)
        return {
            "status": "passed" if not failed else "failed",
            "checks": checks,
            "failed_checks": failed,
            "record_count": len(records or []),
            "evidence_refs": sorted(known),
            "unverified": [
                (
                    "completeness: generic checks cannot tell whether every matching "
                    "record was collected"
                )
            ],
            "scope": "generic open-world checks on synthetic records; no live website was visited",
        }

    def compile(self, report: WorkerReport, subtask: Subtask) -> dict[str, Any] | None:
        """Stage 11: a candidate skill from the report's actions, or ``None``."""
        actions = report.actions if isinstance(report.actions, list) else []
        self.calls.append(("compile", subtask.subtask_id, len(actions)))
        if not actions:
            return None

        steps = []
        for action in actions:
            if not isinstance(action, Mapping):
                continue
            body = action.get("action")
            name = body.get("name") if isinstance(body, Mapping) else None
            steps.append(
                {
                    "action": name,
                    "target": action.get("semantic_target"),
                    "outcome": action.get("outcome"),
                }
            )

        spec = registry.OPERATIONS.get(subtask.operation, {})
        param_types = {
            name: rule["type"] for name, rule in (spec.get("parameters") or {}).items()
        }
        return {
            "skill_id": f"{subtask.site_id}.{subtask.operation.replace('_', '-')}",
            "version": 1,
            "status": "candidate",
            "site_id": subtask.site_id,
            "operation": subtask.operation,
            "description": (
                f"Candidate compiled by FakeGhost from one {subtask.operation} report."
            ),
            "created_at": _now(),
            "definition": {
                "inputs": param_types,
                "output_schema_id": subtask.output_schema_id,
                "validator_id": f"{subtask.site_id}.v1",
                "action_class": spec.get("action_class", "read_only"),
                "preconditions": ["the fake catalog fixture is available"],
                "steps": steps,
                "source": "argus.fakes.FakeGhost, compiled from a synthetic report",
            },
            "source_subtask_ids": [subtask.subtask_id],
            "qualification": [],
        }
