"""Ghost bridge for the connected runtime (ARGUS-3, package P3-GHOST).

:class:`GhostBridge` implements :class:`argus.interfaces.Ghost` on top of two
things it never owns:

* Sting's registry, read in-process through ``ghostapi.api.storage.WorkflowStore``
  and ``ghostapi.api.service.lookup`` exactly as ``LiveGhostBridge.match`` did.
  The bridge only *reads*: no activity event, no candidate, no run report and
  no qualification ever leaves ARGUS through this module (a test greps the
  source for the registry's write entry points).
* Tianqi's DOM worker, through the toolbox context the DOM toolbox keeps per
  ``(run_id, subtask_id)``: ``toolbox.context_for(run_id, subtask_id)`` returns
  ``None`` or a dict with ``"validation"`` (the worker's dumped ``CheckResult``
  list: ``check_id``, ``passed``, ``detail``, ``evidence_refs``), ``"ghost"``
  (the worker report's ``ghost`` block), ``"limitations"``,
  ``"session_disposition"`` and ``"final_url"``.  Only ``context_for`` is
  required, so any object with that method works (duck typing; the toolbox
  module is not imported here).

Decisions this module encodes (docs/hackathon/ARGUS-3-PLAN.md):

1. **Memory is worker-resolved.**  :meth:`GhostBridge.match` always answers
   ``explore``; the reason carries a lookup *preview* so the run's events show
   what the worker's own ``GhostWorkflow`` is likely to find.  The worker makes
   the real reuse/explore decision and saves the candidate itself.
2. **Validation is the worker's verifier plus ARGUS binding checks.**
   :meth:`GhostBridge.validate` combines the binding checks lifted from
   ``LiveGhostBridge`` (records are objects, records present or an evidenced
   empty state, every record cites an observation of this report, the report
   context names this run and subtask, the final page is on the target domain
   when one is known) with every worker check, prefixed ``worker:``.  No
   fixture catalog is consulted.
9. ARGUS keeps JSON runs; :meth:`GhostBridge.compile` returns a *reference*
   envelope for ``JsonStore.save_skill`` pointing at the candidate the worker
   reported, never a compiled definition.

Run resolution.  ``validate`` is given ``report_context`` by the controller
(``{"run_id", "subtask_id", "evidence", "empty_state"}``) and reads the run id
from it.  ``compile`` receives no context, so the runtime calls
:meth:`GhostBridge.set_run` with the run id before ``Controller.run``; when it
has not, the bridge falls back to ``report.request_id`` (the controller's
request identity, which the DOM toolbox may have keyed the context under).

Known limitation of the preview: the worker's lookup carries a
``compatibility_key`` computed from the site config that ARGUS cannot
reproduce, so a ``0.2`` workflow saved with such a key does not match the
preview request (``service.compatible`` compares the keys).  The preview can
therefore say "no compatible qualified workflow" while the worker reuses one.
That is why it is a preview and never a decision.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from argus.contracts import Subtask, WorkerReport

__all__ = ["ALLOWED_ACTIONS", "GhostBridge", "default_database_path"]

#: The action classes the DOM worker may replay; what its own lookup declares.
ALLOWED_ACTIONS = ["navigate", "fill", "select", "click", "wait", "extract"]

#: What every worker check name is prefixed with in the returned ``checks``.
WORKER_CHECK_PREFIX = "worker:"

#: The ``scope`` string of every validation result this bridge returns.
VALIDATION_SCOPE = "worker verifier + argus binding"

_REASON_PREFIX = "worker-resolved: "


def default_database_path() -> Path:
    """``GHOST_DATABASE_PATH`` or ``ghostapi/ghostapi.sqlite3`` under the repo."""
    configured = os.getenv("GHOST_DATABASE_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "ghostapi" / "ghostapi.sqlite3"


def _check_fields(item: Any) -> tuple[str | None, bool]:
    """``(name, passed)`` of one worker check, dumped dict or model object.

    The worker's ``CheckResult`` has ``check_id`` and ``passed``; ``name`` is
    accepted as an alternative key.  Anything but a literal ``True`` counts as
    not passed, so a malformed check can never pass validation.
    """
    if isinstance(item, Mapping):
        name = item.get("check_id", item.get("name"))
        passed = item.get("passed")
    else:
        name = getattr(item, "check_id", getattr(item, "name", None))
        passed = getattr(item, "passed", None)
    if name is None:
        return None, False
    return str(name), passed is True


def _on_domain(url: Any, domain: str) -> bool:
    host = (urlsplit(url).hostname or "") if isinstance(url, str) else ""
    domain = domain.lower().lstrip(".")
    return bool(host) and (host == domain or host.endswith("." + domain))


class GhostBridge:
    """``interfaces.Ghost`` over the worker's verifier and a read-only registry.

    ``toolbox`` is any object with ``context_for(run_id, subtask_id)``.
    ``database_path`` names the Ghost SQLite file for the lookup preview
    (default :func:`default_database_path`), opened lazily on the first
    ``match``; ``store`` injects an already-open ``WorkflowStore`` instead.
    """

    def __init__(
        self,
        toolbox: Any,
        *,
        database_path: str | os.PathLike[str] | None = None,
        store: Any = None,
    ) -> None:
        if not callable(getattr(toolbox, "context_for", None)):
            raise TypeError(
                "GhostBridge needs a toolbox with context_for(run_id, subtask_id)"
            )
        self.toolbox = toolbox
        self.database_path = (
            Path(database_path)
            if database_path is not None
            else default_database_path()
        )
        self._store = store
        self._store_lock = threading.Lock()
        self._run_id: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"GhostBridge(database_path={str(self.database_path)!r})"

    # -------------------------------------------------------------- run identity

    def set_run(self, run_id: str | None) -> None:
        """Tell the bridge which run is executing (called by the runtime).

        ``validate`` prefers the ``run_id`` in the controller's
        ``report_context``; ``compile`` has no context and uses this value,
        falling back to the report's ``request_id``.  Pass ``None`` to clear.
        """
        self._run_id = str(run_id) if run_id is not None else None

    def _run_candidates(self, *preferred: Any) -> list[str]:
        """Run ids to try, in order, without duplicates or blanks."""
        found: list[str] = []
        for candidate in (*preferred, self._run_id):
            if isinstance(candidate, str) and candidate and candidate not in found:
                found.append(candidate)
        return found

    def _context(self, subtask_id: str, *preferred: Any) -> dict[str, Any] | None:
        """The worker context for this subtask under the first run id that has one."""
        for run_id in self._run_candidates(*preferred):
            context = self.toolbox.context_for(run_id, subtask_id)
            if isinstance(context, Mapping):
                return dict(context)
        return None

    # ------------------------------------------------------------------- store

    def _workflow_store(self) -> Any:
        """The registry store, opened on first use (read-only usage only)."""
        with self._store_lock:
            if self._store is None:
                from ghostapi.api.storage import WorkflowStore

                self._store = WorkflowStore(self.database_path)
            return self._store

    # ------------------------------------------------------------------- match

    def _lookup_request(self, subtask: Subtask) -> Any:
        from ghostapi.api.models import LookupRequest

        body = {
            "task_id": subtask.subtask_id,
            "agent_id": "argus",
            "site_id": subtask.site_id.removeprefix("open:"),
            "operation": subtask.operation,
            "parameters": dict(subtask.parameters or {}),
            "required_outputs": list(subtask.expected_record_shape or []),
            "allowed_actions": list(ALLOWED_ACTIONS),
        }
        try:
            return LookupRequest(schema_version="0.2", **body)
        except Exception:  # noqa: BLE001 - fall back to the older schema
            return LookupRequest(schema_version="0.1", **body)

    def match(self, subtask: Subtask, skills: list[dict[str, Any]]) -> dict[str, Any]:
        """Stage 4: always ``explore`` with a lookup preview in the reason.

        ``skills`` (ARGUS's own JSON references) is accepted for the protocol
        and ignored: the registry, not the reference store, knows what is
        qualified.  A failing preview is reported by exception class name and
        still answers ``explore``; the worker resolves memory either way.
        """
        try:
            from ghostapi.api.service import lookup

            request = self._lookup_request(subtask)
            decision = lookup(self._workflow_store(), request)
        except Exception as exc:  # noqa: BLE001 - preview only, never a decision
            preview = f"lookup preview unavailable ({type(exc).__name__})"
            return {
                "decision": "explore",
                "skill": None,
                "reason": _REASON_PREFIX + preview,
            }
        workflow = decision.get("workflow") if isinstance(decision, dict) else None
        if decision.get("decision") == "reuse" and isinstance(workflow, dict):
            preview = (
                f"qualified workflow {workflow.get('skill_id')} "
                f"v{workflow.get('version')} available"
            )
        else:
            preview = "no compatible qualified workflow"
        return {
            "decision": "explore",
            "skill": None,
            "reason": _REASON_PREFIX + preview,
        }

    # ---------------------------------------------------------------- validate

    def validate(
        self,
        subtask: Subtask,
        records: list[Any],
        evidence: list[str],
        *,
        report_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Stage 9: ARGUS binding checks plus the worker's own check list.

        ``passed`` only when every binding check and every worker check passed;
        ``failed`` when any check is false; ``inconclusive`` when the records
        are empty without an evidenced empty state, or when no worker
        verification is available for this run and subtask.
        """
        context = report_context if isinstance(report_context, Mapping) else None
        empty_state = bool(context.get("empty_state")) if context else False
        cited = {ref for ref in evidence if isinstance(ref, str)}
        objects = [record for record in records if isinstance(record, Mapping)]

        checks: dict[str, bool] = {
            "records_are_objects": len(objects) == len(records),
            "records_present": bool(records) or empty_state,
            "records_cite_observations": all(
                record.get("source_observation_id") in cited for record in objects
            ),
        }
        context_run_id = context.get("run_id") if context else None
        if context is not None:
            checks["report_bound_to_subtask"] = (
                context.get("subtask_id") == subtask.subtask_id
            )
            checks["report_bound_to_run"] = (
                isinstance(context_run_id, str)
                and bool(context_run_id)
                and (self._run_id is None or context_run_id == self._run_id)
            )

        worker = self._context(subtask.subtask_id, context_run_id)
        worker_checks = worker.get("validation") if worker else None
        if worker is not None and subtask.target_domain and worker.get("final_url"):
            checks["final_url_on_target_domain"] = _on_domain(
                worker.get("final_url"), subtask.target_domain
            )
        if isinstance(worker_checks, list) and worker_checks:
            for index, item in enumerate(worker_checks):
                name, passed = _check_fields(item)
                checks[f"{WORKER_CHECK_PREFIX}{name or f'check-{index}'}"] = passed

        failed = [name for name, passed in checks.items() if not passed]
        result: dict[str, Any] = {
            "checks": checks,
            "failed_checks": failed,
            "scope": VALIDATION_SCOPE,
        }
        binding_failed = [name for name in failed if name != "records_present"]
        if binding_failed:
            result["status"] = "failed"
        elif not checks["records_present"]:
            result["status"] = "inconclusive"
            result["reason"] = "no records and no evidenced empty state"
        elif not (isinstance(worker_checks, list) and worker_checks):
            result["status"] = "inconclusive"
            result["reason"] = "worker verification unavailable"
        else:
            result["status"] = "passed"
        return result

    # ----------------------------------------------------------------- compile

    def compile(self, report: WorkerReport, subtask: Subtask) -> dict[str, Any] | None:
        """Stage 11: a reference to the candidate the worker saved, or ``None``.

        The worker's ``ghost`` block records ``mode`` (``exploration``,
        ``reuse`` or ``fallback_visual``), ``candidate`` (the registry's
        ``{"skill_id", "version", "status"}`` answer, or ``None``),
        ``candidate_skipped`` (a reason string when nothing was compilable),
        ``errors``, ``visual_used`` and, on reuse, ``workflow`` and ``run_id``.
        Only a recorded ``candidate`` yields an envelope; reuse, a skipped
        candidate, a registry error or a missing context yield ``None``.
        """
        worker = self._context(subtask.subtask_id, report.request_id)
        ghost = worker.get("ghost") if worker else None
        if not isinstance(ghost, Mapping):
            return None
        candidate = ghost.get("candidate")
        if (
            ghost.get("mode") == "reuse"
            or ghost.get("candidate_skipped")
            or not isinstance(candidate, Mapping)
            or not isinstance(candidate.get("skill_id"), str)
            or not candidate.get("skill_id")
        ):
            return None
        version = candidate.get("version")
        return {
            "skill_id": candidate["skill_id"],
            "version": version if version not in (None, "") else 1,
            "status": "candidate",
            "site_id": subtask.site_id.removeprefix("open:"),
            "operation": subtask.operation,
            "subtask_id": subtask.subtask_id,
            "source_run_ids": [report.request_id],
            "worker": report.worker,
            "mode": ghost.get("mode"),
            "visual_used": bool(ghost.get("visual_used")),
            "reference": {
                "registry": "ghost",
                "database_path": str(self.database_path),
                "ghost": dict(ghost),
            },
        }
