"""In-memory stand-ins for the three boundaries the controller calls out through.

Phase D of docs/hackathon/ARGUS-IMPLEMENTATION.md.  These let the whole run
execute offline, with no browser, no model call and no network:

* :class:`FakeToolbox` - hands out session handles, answers ``run_subtask``
  from a small product catalog, and can be scripted to force failures.
* :class:`StubModerator` - the three moderator callables reduced to rules, so
  the controller's intake, reconciliation and synthesis paths are exercised
  without a model.  It is a placeholder for Thomas's moderator.
* :class:`FakeGhost` - always explores, validates records against the fixture
  ground truth, and compiles a candidate skill.  Modelled on Sting's simulated
  demo (``ghostapi/demo/ghost_demo.py``): the catalog rows and the shape of the
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

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus import registry
from argus.contracts import (
    ERROR_CODES,
    Claim,
    ContractError,
    FinalAnswer,
    InterpretedRequest,
    ModeratorDecision,
    Plan,
    Subtask,
    SubtaskInput,
    TypedError,
    WorkerReport,
)

__all__ = [
    "CATALOG",
    "DEFAULT_SITE_ID",
    "SCRIPTED_OUTCOMES",
    "FakeToolbox",
    "StubModerator",
    "FakeGhost",
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


def _now() -> str:
    """Current UTC time in the fixtures' ``...Z`` format."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _origin(site_id: str) -> str:
    """The configured origin for a site, or the demo catalog's."""
    site = registry.SITES.get(site_id) or registry.SITES[DEFAULT_SITE_ID]
    return site["origin"]


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
        observation = entry.get("observation_id") if isinstance(entry, Mapping) else None
        if isinstance(observation, str) and observation not in refs:
            refs.append(observation)
    return refs


class FakeToolbox:
    """A toolbox that never opens a browser.  Implements :class:`argus.interfaces.Toolbox`.

    Sessions are handed out as ``fake-session-N`` and tracked, so a test can
    assert the controller closed every one of them and closed each only once.
    ``run_subtask`` answers from :data:`CATALOG`, filtered by the subtask's
    ``query`` substring and ``max_price`` (and capped at ``max_results`` when
    the subtask carries one).

    ``script`` maps a ``subtask_id`` to one of :data:`SCRIPTED_OUTCOMES`, or to
    a sequence of them consumed one per call (``None`` for a normal success, and
    success again once the sequence is exhausted) so a retry can be scripted to
    succeed on its second attempt.
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
            raise RuntimeError(f"fake toolbox failed while running {subtask.subtask_id}")
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

    def records_for(self, subtask: Subtask, observation: str | None = None) -> list[dict[str, Any]]:
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

    def _build_report(self, subtask_input: SubtaskInput, forced: str | None) -> WorkerReport:
        subtask = subtask_input.subtask
        data = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
        template = data["actions"][0]
        origin = _origin(subtask.site_id)
        search_url = f"{origin}/catalog"
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        query = params.get("query")

        with self._lock:
            self._observation_n += 1
            observation_n = self._observation_n
        observation = f"observation-{observation_n - 1:03d}.png"

        if forced == "empty":
            # Nothing found and nothing captured: records and evidence are both
            # empty, which is what StubModerator answers with ``verify``.
            observation = None
            records: list[dict[str, Any]] = []
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
            self._action(
                template,
                step=1,
                name="type",
                payload={"text": query},
                url=search_url,
                semantic_target="Search products",
                observation_before=observation,
            ),
        ]

        if forced in _SCRIPTED_FAILURES:
            code, retryable, message = _SCRIPTED_FAILURES[forced]
            outcome = "failed"
            summary = f"The subtask did not finish: {message}."
            findings = None
            actions.append(
                self._action(
                    template,
                    step=2,
                    name="extract",
                    payload={"schema": subtask.output_schema_id},
                    url=search_url,
                    outcome="failed",
                    observation_before=observation,
                )
            )
            typed_failures = [
                TypedError(
                    code=code,
                    message=message,
                    retryable=retryable,
                    step_id="step-002",
                    evidence_refs=[observation] if observation else [],
                )
            ]
            failures = [{"step_id": "step-002", "reason": message}]
        else:
            outcome = "succeeded"
            findings = records
            if records:
                titles = ", ".join(record["title"] for record in records)
                summary = (
                    f"The fake catalog returned {len(records)} product(s) for "
                    f"{query!r}: {titles}."
                )
            else:
                summary = f"The fake catalog returned no products for {query!r}."
            actions.append(
                self._action(
                    template,
                    step=2,
                    name="extract",
                    payload={"schema": subtask.output_schema_id},
                    url=search_url,
                    observation_before=observation,
                )
            )
            actions.append(
                self._action(
                    template,
                    step=3,
                    name="finished",
                    payload={"content": summary},
                    url=search_url,
                    observation_before=observation,
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
            request_id=subtask_input.run_id,
            subtask_id=subtask.subtask_id,
            subtask=self._subtask_text(subtask),
            outcome=outcome,
            summary=summary,
            findings=findings,
            actions=actions,
            evidence={
                "session_id": f"fake-browser-{observation_n}",
                "session_replay_url": None,
                "screenshots": [observation] if observation else [],
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
        detail = ", ".join(f"{name}={value!r}" for name, value in sorted(params.items()))
        return f"{subtask.operation} on {subtask.site_id} with {detail or 'no parameters'}"


class StubModerator:
    """The three moderator callables as rules, standing in for Thomas's moderator.

    Implements :class:`argus.interfaces.Moderator`.  It makes no model call and
    reads nothing but the report it is handed, so its judgements are shallow by
    design: a run driven by this stub proves the controller's plumbing, not that
    an answer is any good.  Every decision uses the vocabulary
    ``contracts.MODERATOR_DECISIONS`` allows for its stage.

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
        """Stage 8: merge the reports' records; name missing subtasks as gaps."""
        subtask_ids = [report.subtask_id for report in reports]
        self.calls.append(("reconcile", tuple(subtask_ids)))

        findings: list[Any] = []
        evidence_refs: list[str] = []
        for report in reports:
            findings.extend(_records_of(report))
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
                f"{', '.join(subtask_ids) or 'none'}; the stub compares nothing, so it "
                "finds no conflicts."
            ),
            evidence_refs=evidence_refs,
            next_action={
                "subtask_ids": subtask_ids,
                "findings": findings,
                "gaps": gaps,
                "conflicts": [],
            },
        )

    def synthesize(
        self,
        interpreted: InterpretedRequest,
        records: list[Any],
        validation: dict[str, Any],
        evidence: list[str],
        failures: list[TypedError],
    ) -> FinalAnswer:
        """Stage 10: one claim per record, each citing that record's observation.

        A record with no observation of its own falls back to the run's first
        evidence reference; a record with neither is named in ``unverified``
        instead of becoming a claim, so no claim is ever emitted without an
        evidence reference.  ``failures`` is repeated unchanged.
        """
        self.calls.append(("synthesize", interpreted.request_id, len(records)))
        fallback = evidence[0] if evidence else None
        claims: list[Claim] = []
        unverified: list[str] = []

        for index, record in enumerate(records):
            label = f"record {index}"
            if isinstance(record, Mapping):
                label = str(record.get("title") or label)
                ref = record.get("source_observation_id") or fallback
                text = (
                    f"{label} costs {record.get('price')} {record.get('currency')} "
                    f"at {record.get('url')}."
                )
            else:
                ref = fallback
                text = f"{label}: {record!r}."
            if not ref:
                unverified.append(f"{label}: no evidence reference, so it is not claimed.")
                continue
            claims.append(Claim(text=text, evidence_refs=[str(ref)]))

        status = validation.get("status") if isinstance(validation, Mapping) else None
        if status != "passed":
            unverified.append(f"Validation status is {status!r}, not 'passed'.")
        for failure in failures:
            unverified.append(f"{failure.code}: {failure.message}")

        if claims:
            text = (
                f"{len(claims)} record(s) for {interpreted.raw_text!r}, each cited to an "
                "observation from this run."
            )
        else:
            text = f"No cited record for {interpreted.raw_text!r}."
        if failures:
            text += f" {len(failures)} subtask failure(s) are reported unchanged."

        return FinalAnswer(
            text=text,
            claims=claims,
            records=list(records),
            failures=list(failures),
            unverified=unverified,
        )


class FakeGhost:
    """Skill matching, validation and compilation without a skill store.

    Implements :class:`argus.interfaces.Ghost`.  ``match`` always explores,
    because the fake holds no qualified skills.  ``validate`` checks the records
    against the fixture's ground truth the way Sting's ``verify`` does - named
    checks, independent of the steps that produced the records - and the
    moderator cannot override a failure.  ``compile`` stages a candidate skill
    in the shape of his ``simulated_discovery`` definition; session handles and
    evidence never enter it.
    """

    def __init__(self, site_id: str = DEFAULT_SITE_ID, origin: str | None = None) -> None:
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
    ) -> dict[str, Any]:
        """Stage 9: ``passed`` only when every record satisfies every named check."""
        self.calls.append(("validate", subtask.subtask_id, len(records or [])))
        origin = self._origin_for(subtask)
        params = subtask.parameters if isinstance(subtask.parameters, Mapping) else {}
        max_price = params.get("max_price")
        rows = [record for record in (records or []) if isinstance(record, Mapping)]

        checks = {
            "records_are_objects": len(rows) == len(records or []),
            "title_present": all(
                isinstance(row.get("title"), str) and row["title"].strip() for row in rows
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
                else all(_is_number(row.get("price")) and row["price"] <= max_price for row in rows)
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
