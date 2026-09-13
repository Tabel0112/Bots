"""Structured moderator implementation for the ARGUS controller.

The controller owns execution, sessions, budgets, state, events, and final answer
rendering. This module only judges worker reports, reconciles records, selects
validated fields, and observes progress. Model calls use the injected
``ModelClient`` boundary; passing ``None`` keeps every decision deterministic.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from argus.contracts import (
    MODERATOR_DECISIONS,
    AnswerSelection,
    Claim,
    ContractError,
    Criterion,
    Event,
    InterpretedRequest,
    ModeratorDecision,
    Note,
    Plan,
    Subtask,
    TypedError,
    WorkerReport,
)
from argus.model_client import ModelClient

__all__ = ["Moderator", "load_env"]

_RETRY_CODES = frozenset({"TARGET_NOT_FOUND", "TARGET_AMBIGUOUS", "EXTRACTION_FAILED"})
_INTERNAL_FIELDS = frozenset({"source_observation_id"})


class AssessOutput(BaseModel):
    """The only model output accepted while assessing a worker report."""

    model_config = ConfigDict(extra="forbid", strict=True)

    decision: Literal["accept", "verify", "fail"]
    reason: str = Field(min_length=1)
    unmet_conditions: list[str]
    verification_question: str | None


class ConflictTag(BaseModel):
    """One model tag for a controller-owned conflict description."""

    model_config = ConfigDict(extra="forbid", strict=True)

    conflict_index: int = Field(ge=0)
    resolution: Literal[
        "resolve_from_evidence",
        "verify",
        "report_as_gap",
    ]


class ReconcileOutput(BaseModel):
    """The complete set of conflict tags from one reconciliation call."""

    model_config = ConfigDict(extra="forbid", strict=True)

    conflicts: list[ConflictTag]


class SynthesisRecord(BaseModel):
    """One selected original record and the fields safe to publish from it."""

    model_config = ConfigDict(extra="forbid", strict=True)

    record_index: int = Field(ge=0)
    fields: list[str] = Field(min_length=1)


class SynthesisOutput(BaseModel):
    """A model-proposed reordering and field choice, never answer prose."""

    model_config = ConfigDict(extra="forbid", strict=True)

    records: list[SynthesisRecord]


def _records_of(report: WorkerReport) -> list[Any]:
    findings = report.findings
    if isinstance(findings, list):
        return list(findings)
    if isinstance(findings, Mapping) and isinstance(findings.get("records"), list):
        return list(findings["records"])
    return []


def _screenshots_of(report: WorkerReport) -> list[str]:
    evidence = report.evidence if isinstance(report.evidence, Mapping) else {}
    screenshots = evidence.get("screenshots")
    return (
        [str(value) for value in screenshots] if isinstance(screenshots, list) else []
    )


def _verifications_of(report: WorkerReport) -> list[str]:
    evidence = report.evidence if isinstance(report.evidence, Mapping) else {}
    verifications = evidence.get("verifications")
    refs: list[str] = []
    for entry in verifications if isinstance(verifications, list) else []:
        ref = entry.get("observation_id") if isinstance(entry, Mapping) else None
        if isinstance(ref, str) and ref and ref not in refs:
            refs.append(ref)
    return refs


def _safe_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)


def _valid_report(report: Any) -> bool:
    """Re-check a possibly mutated dataclass at the moderator boundary."""
    if not isinstance(report, WorkerReport):
        return False
    try:
        checked = WorkerReport.from_dict(report.to_dict())
    except (ContractError, TypeError, ValueError):
        return False
    return (
        isinstance(checked.schema_version, str)
        and bool(checked.schema_version.strip())
        and checked.outcome in {"succeeded", "failed"}
        and isinstance(checked.evidence, dict)
        and isinstance(checked.metrics, dict)
        and isinstance(checked.typed_failures, list)
    )


def _rank_key(value: Any) -> tuple[int, Any]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (1, value)
    return (0, str(value))


def _record_label(record: Any) -> str:
    if isinstance(record, Mapping):
        for key in ("url", "title", "name", "id"):
            value = record.get(key)
            if isinstance(value, (str, int, float)) and not isinstance(value, bool):
                return f"{key}={value}"
    return f"record={_safe_json(record)}"


def load_env() -> None:
    """Deprecated compatibility shim; the ARGUS runtime owns environment loading."""


class Moderator:
    """ARGUS moderator and optional progress observer."""

    def __init__(
        self,
        client: ModelClient | None = None,
        *,
        max_tokens: int = 1200,
        stall_seconds: float = 120.0,
    ) -> None:
        if (
            not isinstance(max_tokens, int)
            or isinstance(max_tokens, bool)
            or max_tokens < 1
        ):
            raise ValueError("max_tokens must be a positive integer")
        if (
            not isinstance(stall_seconds, (int, float))
            or isinstance(stall_seconds, bool)
            or stall_seconds < 0
        ):
            raise ValueError("stall_seconds must be a non-negative number")
        self.client = client
        self.max_tokens = max_tokens
        self.stall_seconds = float(stall_seconds)
        self.calls: list[tuple[Any, ...]] = []
        self._assessment_counts: dict[tuple[str, str], int] = {}
        self._last_activity: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def assess_report(
        self,
        subtask: Subtask,
        report: WorkerReport,
        success_conditions: list[str],
    ) -> ModeratorDecision:
        """Apply deterministic safety rules, then optionally judge evidence."""
        self.calls.append(
            ("assess_report", subtask.subtask_id, getattr(report, "outcome", None))
        )
        if not _valid_report(report):
            return self._decision(
                "assess",
                "fail",
                "The worker report does not satisfy the ARGUS report schema.",
                next_action={
                    "failure": TypedError(
                        "EXTRACTION_FAILED",
                        "worker report failed moderator schema validation",
                        False,
                        subtask.subtask_id,
                    ).to_dict()
                },
            )

        assessment_key = (report.request_id, subtask.subtask_id)
        with self._lock:
            attempt = self._assessment_counts.get(assessment_key, 0)
            self._assessment_counts[assessment_key] = attempt + 1

        screenshots = _screenshots_of(report)
        verifications = _verifications_of(report)
        evidence_refs = screenshots or verifications
        records = _records_of(report)

        if report.outcome == "failed":
            failure = (
                report.typed_failures[0]
                if report.typed_failures
                else TypedError(
                    "EXTRACTION_FAILED",
                    "failed worker report carried no typed failure",
                    False,
                    subtask.subtask_id,
                    screenshots,
                )
            )
            decision = (
                "retry_other_path"
                if attempt == 0 and failure.code in _RETRY_CODES
                else "fail"
            )
            return self._decision(
                "assess",
                decision,
                f"{failure.code}: {failure.message}",
                list(failure.evidence_refs) or screenshots,
                {"failure": failure.to_dict()},
            )

        if not evidence_refs:
            query = (subtask.parameters or {}).get("query")
            return self._decision(
                "assess",
                "verify",
                "The succeeded report has no screenshot or controller verification.",
                next_action={
                    "question": (
                        "Does the page show results for "
                        f"{query!r}, or an explicit empty state?"
                    ),
                    "success_conditions": list(success_conditions),
                },
            )

        if self.client is None:
            return self._decision(
                "assess",
                "accept",
                "The succeeded report carries run evidence.",
                evidence_refs,
            )

        prompt = {
            "success_conditions": list(success_conditions),
            "records": records,
            "evidence_refs": evidence_refs,
            "metrics": report.metrics,
        }
        result = self._call_model(
            "Judge whether the evidenced worker report meets every success condition. "
            "Return only the requested structured assessment. Do not write an answer.",
            _safe_json(prompt),
            AssessOutput,
        )
        if result is None or result.status != "ok" or result.parsed is None:
            return self._assessment_fallback(result, evidence_refs)
        try:
            output = AssessOutput.model_validate(result.parsed)
        except (TypeError, ValueError):
            return self._assessment_fallback(None, evidence_refs)

        if (
            output.decision == "accept"
            and (output.unmet_conditions or output.verification_question is not None)
        ) or (output.decision != "verify" and output.verification_question is not None):
            return self._assessment_fallback(None, evidence_refs)

        next_action: dict[str, Any] | None = None
        if output.decision == "verify":
            question = output.verification_question
            if not isinstance(question, str) or not question.strip():
                return self._assessment_fallback(None, evidence_refs)
            next_action = {
                "question": question,
                "unmet_conditions": list(output.unmet_conditions),
            }
        elif output.unmet_conditions:
            next_action = {"unmet_conditions": list(output.unmet_conditions)}
        return self._decision(
            "assess",
            output.decision,
            output.reason,
            evidence_refs,
            next_action,
        )

    def reconcile(self, plan: Plan, reports: list[WorkerReport]) -> ModeratorDecision:
        """Merge by URL and let a model tag only genuine field conflicts."""
        subtask_ids = [report.subtask_id for report in reports]
        self.calls.append(("reconcile", tuple(subtask_ids)))
        findings, dropped, conflicts = self._merge_records(reports)
        evidence_refs: list[str] = []
        for report in reports:
            for ref in _screenshots_of(report):
                if ref not in evidence_refs:
                    evidence_refs.append(ref)

        resolutions = ["report_as_gap"] * len(conflicts)
        model_valid = True
        if conflicts and self.client is not None:
            result = self._call_model(
                "Tag every supplied conflict exactly once. You may only choose "
                "resolve_from_evidence, verify, or report_as_gap.",
                _safe_json({"conflicts": conflicts, "evidence_refs": evidence_refs}),
                ReconcileOutput,
            )
            model_valid = False
            if (
                result is not None
                and result.status == "ok"
                and result.parsed is not None
            ):
                try:
                    output = ReconcileOutput.model_validate(result.parsed)
                    tags = {
                        tag.conflict_index: tag.resolution for tag in output.conflicts
                    }
                    expected = set(range(len(conflicts)))
                    if set(tags) == expected and len(tags) == len(output.conflicts):
                        resolutions = [tags[index] for index in range(len(conflicts))]
                        model_valid = True
                except (TypeError, ValueError):
                    pass

        conflict_entries: list[dict[str, Any]] = []
        for index, conflict in enumerate(conflicts):
            entry = dict(conflict)
            entry["resolution"] = resolutions[index] if model_valid else "report_as_gap"
            conflict_entries.append(entry)

        conflict_by_dropped = {
            entry["dropped_key"]: entry["resolution"] for entry in conflict_entries
        }
        conflict_details = {
            entry["dropped_key"]: {
                "url": entry["url"],
                "fields": list(entry["fields"]),
            }
            for entry in conflict_entries
        }
        gaps: list[dict[str, Any]] = []
        for item in dropped:
            resolution = conflict_by_dropped.get(
                item["dropped_key"], "resolve_from_evidence"
            )
            gap = {
                "subtask_id": item["subtask_id"],
                "record": item["label"],
                "gap": "record superseded during URL merge",
                "resolution": resolution,
            }
            if item["dropped_key"] in conflict_details:
                gap["conflict"] = conflict_details[item["dropped_key"]]
            gaps.append(gap)
        reported = set(subtask_ids)
        gaps.extend(
            {
                "subtask_id": subtask.subtask_id,
                "gap": "no accepted report for this subtask",
                "resolution": "report_as_gap",
            }
            for subtask in plan.subtasks
            if subtask.subtask_id not in reported
        )

        return self._decision(
            "reconcile",
            "merged",
            f"Merged {len(findings)} record(s) from {len(reports)} report(s).",
            evidence_refs,
            {
                "subtask_ids": subtask_ids,
                "findings": findings,
                "gaps": gaps,
                "conflicts": conflict_entries,
            },
        )

    def synthesize(
        self,
        interpreted: InterpretedRequest,
        records: list[Any],
        validation: dict[str, Any],
        evidence: list[str],
        failures: list[TypedError],
    ) -> AnswerSelection:
        """Select original records and their fields; never return answer prose."""
        del validation, evidence, failures
        self.calls.append(("synthesize", interpreted.request_id, len(records)))
        criteria = [
            (f"intent-{intent_index}:criterion-{criterion_index}", criterion)
            for intent_index, intent in enumerate(interpreted.intents)
            for criterion_index, criterion in enumerate(intent.criteria)
        ]
        indexed = list(enumerate(records))
        if self._asks_for_cheapest(interpreted, indexed):
            indexed = self._cheapest_per_category(indexed)
        selected, not_applied = self._apply_criteria(criteria, indexed)
        deterministic = self._selection(selected, not_applied)
        if self.client is None or not deterministic.record_indices:
            return deterministic

        result = self._call_model(
            "Reorder the supplied selected records if useful and choose only fields "
            "that exist on each record. Include every supplied record exactly once. "
            "Return structured selection only.",
            _safe_json(
                {
                    "selected_records": [
                        {"record_index": index, "record": record}
                        for index, record in selected
                    ]
                }
            ),
            SynthesisOutput,
        )
        fallback_subject = self._fallback_subject(result)
        if result is not None and result.status == "ok" and result.parsed is not None:
            try:
                output = SynthesisOutput.model_validate(result.parsed)
                candidate = self._model_selection(output, selected, deterministic.notes)
                if candidate is not None:
                    return candidate
                fallback_subject = "selection_invalid"
            except (TypeError, ValueError):
                fallback_subject = "selection_invalid"
        return AnswerSelection(
            list(deterministic.record_indices),
            list(deterministic.claims),
            [
                *deterministic.notes,
                Note("synthesis_fallback", fallback_subject),
            ],
        )

    def observe_progress(
        self, snapshot: dict[str, Any], event: Event
    ) -> ModeratorDecision:
        """Flag stalls and request stops for an evidenced budget overrun."""
        self.calls.append(("observe_progress", event.type))
        data = event.data if isinstance(event.data, Mapping) else {}
        subtask_id = data.get("subtask_id")
        if not isinstance(subtask_id, str) or not subtask_id:
            return self._decision("observe", "continue", "No subtask is in scope.")

        now = time.monotonic()
        detail = self._snapshot_subtask(snapshot, subtask_id)
        elapsed = self._number(
            data.get("elapsed_seconds"),
            detail.get("elapsed_seconds"),
            snapshot.get("elapsed_seconds"),
        )
        detail_budget = detail.get("budget")
        budget = self._number(
            data.get("max_seconds"),
            data.get("budget_seconds"),
            detail.get("max_seconds"),
            detail_budget.get("max_seconds")
            if isinstance(detail_budget, Mapping)
            else None,
        )
        if elapsed is not None and budget is not None and elapsed > budget:
            return self._decision(
                "observe",
                "stop_subtask",
                "The subtask exceeded its elapsed-time budget.",
                next_action={"subtask_id": subtask_id},
            )

        explicit_idle = self._number(
            data.get("seconds_since_last_action"),
            detail.get("seconds_since_last_action"),
            detail.get("idle_seconds"),
        )
        activity_key = (event.run_id, subtask_id)
        with self._lock:
            previous = self._last_activity.get(activity_key)
            if self._is_activity(event) or previous is None:
                self._last_activity[activity_key] = now
        idle = (
            explicit_idle
            if explicit_idle is not None
            else (now - previous if previous is not None else 0.0)
        )
        if idle > self.stall_seconds:
            return self._decision(
                "observe",
                "flag",
                "The subtask has produced no new browser action within the stall window.",
                next_action={
                    "subtask_id": subtask_id,
                    "question": (
                        "Verify that the page is responsive and the target remains visible."
                    ),
                },
            )
        return self._decision("observe", "continue", "Progress remains within bounds.")

    @staticmethod
    def _decision(
        stage: str,
        decision: str,
        reason: str,
        evidence_refs: list[str] | None = None,
        next_action: dict[str, Any] | None = None,
    ) -> ModeratorDecision:
        if decision not in MODERATOR_DECISIONS[stage]:
            raise ContractError(f"invalid moderator decision {stage}/{decision}")
        return ModeratorDecision(
            stage,
            decision,
            reason,
            list(evidence_refs or []),
            next_action,
        )

    def _call_model(self, system: str, user: str, output_model: type[BaseModel]):
        if self.client is None:
            return None
        try:
            return self.client.parse_json(system, user, output_model, self.max_tokens)
        except Exception:  # noqa: BLE001 - provider failures degrade structurally
            return None

    def _assessment_fallback(self, result: Any, refs: list[str]) -> ModeratorDecision:
        status = getattr(result, "status", None)
        reason = {
            "refusal": (
                "The moderator model refused; independent verification is required."
            ),
            "truncated": (
                "The moderator model response was truncated; verification is required."
            ),
            "invalid": (
                "The moderator model output was invalid; verification is required."
            ),
        }.get(
            status,
            "The moderator model was unavailable; verification is required.",
        )
        return self._decision(
            "assess",
            "verify",
            reason,
            refs,
            {"question": "Independently verify the report's success conditions."},
        )

    @staticmethod
    def _merge_records(
        reports: Sequence[WorkerReport],
    ) -> tuple[list[Any], list[dict[str, Any]], list[dict[str, Any]]]:
        merged: list[Any] = []
        owners: list[str] = []
        positions: dict[str, int] = {}
        dropped: list[dict[str, Any]] = []
        conflicts: list[dict[str, Any]] = []
        marker = object()
        dropped_number = 0
        for report in reports:
            for record in _records_of(report):
                url = record.get("url") if isinstance(record, Mapping) else None
                key = url.strip() if isinstance(url, str) and url.strip() else None
                if key is None or key not in positions:
                    if key is not None:
                        positions[key] = len(merged)
                    merged.append(record)
                    owners.append(report.subtask_id)
                    continue
                old_position = positions[key]
                old_record = merged[old_position]
                old_owner = owners[old_position]
                dropped_key = f"dropped-{dropped_number}"
                dropped_number += 1
                dropped.append(
                    {
                        "dropped_key": dropped_key,
                        "subtask_id": old_owner,
                        "label": _record_label(old_record),
                    }
                )
                differing = []
                if isinstance(old_record, Mapping) and isinstance(record, Mapping):
                    differing = sorted(
                        field
                        for field in set(old_record) | set(record)
                        if field != "source_observation_id"
                        and old_record.get(field) != record.get(field)
                    )
                elif old_record != record:
                    differing = ["record"]
                if differing:
                    conflicts.append(
                        {
                            "url": key,
                            "fields": differing,
                            "earlier_subtask_id": old_owner,
                            "later_subtask_id": report.subtask_id,
                            "dropped_key": dropped_key,
                        }
                    )
                merged[old_position] = marker
                positions[key] = len(merged)
                merged.append(record)
                owners.append(report.subtask_id)
        return (
            [record for record in merged if record is not marker],
            dropped,
            conflicts,
        )

    @staticmethod
    def _asks_for_cheapest(
        interpreted: InterpretedRequest, indexed: list[tuple[int, Any]]
    ) -> bool:
        if "cheapest" not in interpreted.raw_text.casefold():
            return False
        return bool(indexed) and all(
            isinstance(record, Mapping)
            and isinstance(record.get("category"), str)
            and isinstance(record.get("price"), (int, float))
            and not isinstance(record.get("price"), bool)
            for _, record in indexed
        )

    @staticmethod
    def _cheapest_per_category(
        indexed: list[tuple[int, Any]],
    ) -> list[tuple[int, Any]]:
        categories: list[str] = []
        for _, record in indexed:
            category = record["category"]
            if category not in categories:
                categories.append(category)
        return [
            min(
                (item for item in indexed if item[1]["category"] == category),
                key=lambda item: item[1]["price"],
            )
            for category in categories
        ]

    @staticmethod
    def _apply_criteria(
        criteria: Sequence[tuple[str, Criterion]],
        indexed: list[tuple[int, Any]],
    ) -> tuple[list[tuple[int, Any]], list[str]]:
        not_applied: list[str] = []

        def rows_carry(field: Any) -> bool:
            return bool(indexed) and all(
                isinstance(record, Mapping) and field in record for _, record in indexed
            )

        ordered = sorted(
            criteria,
            key=lambda item: {"filter": 0, "rank": 1, "limit": 2}.get(item[1].kind, 3),
        )
        for subject, criterion in ordered:
            parameter = criterion.parameter
            if criterion.kind == "limit":
                if (
                    not isinstance(parameter, int)
                    or isinstance(parameter, bool)
                    or parameter < 0
                ):
                    not_applied.append(subject)
                else:
                    indexed = indexed[:parameter]
                continue
            field, wanted, exact = parameter, None, False
            if isinstance(parameter, Mapping) and "field" in parameter:
                field = parameter["field"]
                wanted = parameter.get("value")
                exact = "value" in parameter
            if not isinstance(field, str) or not field.strip() or not rows_carry(field):
                not_applied.append(subject)
                continue
            if criterion.kind == "filter":
                indexed = [
                    (index, record)
                    for index, record in indexed
                    if (record[field] == wanted if exact else bool(record[field]))
                ]
            elif criterion.kind == "rank":
                indexed = sorted(
                    indexed,
                    key=lambda item: _rank_key(item[1][field]),
                    reverse=True,
                )
        return indexed, not_applied

    @staticmethod
    def _selection(
        selected: list[tuple[int, Any]], not_applied: list[str]
    ) -> AnswerSelection:
        indices: list[int] = []
        claims: list[Claim] = []
        for original_index, record in selected:
            if not isinstance(record, Mapping):
                continue
            fields = [name for name in record if name not in _INTERNAL_FIELDS]
            if not fields or not isinstance(record.get("source_observation_id"), str):
                continue
            indices.append(original_index)
            claims.append(Claim(len(indices) - 1, fields))
        return AnswerSelection(
            indices,
            claims,
            [Note("criterion_not_applied", subject) for subject in not_applied],
        )

    @staticmethod
    def _model_selection(
        output: SynthesisOutput,
        selected: list[tuple[int, Any]],
        notes: list[Note],
    ) -> AnswerSelection | None:
        allowed = {index: record for index, record in selected}
        proposed = [item.record_index for item in output.records]
        if len(proposed) != len(set(proposed)) or set(proposed) != set(allowed):
            return None
        claims: list[Claim] = []
        for selected_index, item in enumerate(output.records):
            record = allowed[item.record_index]
            if not isinstance(record, Mapping):
                return None
            if len(item.fields) != len(set(item.fields)) or any(
                field in _INTERNAL_FIELDS or field not in record
                for field in item.fields
            ):
                return None
            claims.append(Claim(selected_index, list(item.fields)))
        return AnswerSelection(proposed, claims, list(notes))

    @staticmethod
    def _fallback_subject(result: Any) -> str:
        return {
            "refusal": "model_refused",
            "truncated": "model_truncated",
            "invalid": "model_invalid",
        }.get(getattr(result, "status", None), "selection_invalid")

    @staticmethod
    def _snapshot_subtask(
        snapshot: Mapping[str, Any], subtask_id: str
    ) -> Mapping[str, Any]:
        subtasks = snapshot.get("subtasks")
        value = subtasks.get(subtask_id) if isinstance(subtasks, Mapping) else None
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _number(*values: Any) -> float | None:
        for value in values:
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return float(value)
        return None

    @staticmethod
    def _is_activity(event: Event) -> bool:
        return event.type in {
            "subtask_started",
            "action_observed",
            "worker_action_recorded",
            "subtask_report_received",
        }
