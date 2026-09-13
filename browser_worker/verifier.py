"""Observed-value extraction and independent acceptance checks. No model verdicts."""

import re
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator, FormatChecker

from .config import SiteConfig
from .policy import guard_url, resolve_element
from .schemas import (
    Action,
    CheckResult,
    ExtractedRecord,
    Observation,
    SubtaskReport,
    SubtaskRequest,
    WorkerError,
)
from .schemas import (
    FailureCode as C,
)


def within(ref: str, container: str, obs: Observation) -> bool:
    by_ref = {e.ref: e for e in obs.elements}
    seen = set()
    while ref and ref not in seen:
        if ref == container:
            return True
        seen.add(ref)
        ref = by_ref[ref].parent_ref if ref in by_ref else None
    return False


def observed_value(source, schema: dict, obs: Observation, task: SubtaskRequest, site: SiteConfig):
    if source.element_ref is None:
        return None
    element = resolve_element(obs, source.element_ref)
    if source.attribute == "text" and element.text_truncated:
        raise WorkerError(
            C.EXTRACTION_FAILED, "Source text is truncated; use a smaller field element.", True
        )
    raw = getattr(element, source.attribute)
    if raw is None or raw == "":
        return None
    types = schema.get("type", "string")
    types = [types] if isinstance(types, str) else types
    if source.attribute == "href":
        guard_url(raw, site, task)
    if "number" in types or "integer" in types:
        # Deliberately reject currency/locale ambiguity. Configure a numeric-only source.
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", raw.strip()):
            return None
        try:
            number = Decimal(raw.strip())
            if not number.is_finite():
                return None
            if "integer" in types:
                return int(number) if number == number.to_integral_value() else None
            return float(number)
        except (InvalidOperation, OverflowError):
            return None
    if "boolean" in types:
        return {"true": True, "false": False}.get(raw.strip().lower())
    if schema.get("format") == "uri":
        guard_url(raw, site, task)
    return raw.strip()


def extract(action: Action, obs: Observation, task: SubtaskRequest, site: SiteConfig):
    if action.observation_id != obs.observation_id:
        raise WorkerError(C.STALE_OBSERVATION, "Extraction needs the current observation.", True)
    if len(action.records) > site.max_records:
        raise WorkerError(
            C.EXTRACTION_FAILED, "Too many records for this site's bounded extraction.", True
        )
    fields = site.record_schema["properties"]
    records = []
    containers = set()
    for mapping in action.records:
        if mapping.container_ref not in obs.signals.get("record_refs", []):
            raise WorkerError(
                C.EXTRACTION_FAILED, "Source is not a configured result container.", True
            )
        if mapping.container_ref in containers:
            raise WorkerError(
                C.EXTRACTION_FAILED,
                "Duplicate container mapping; each row is extracted once.",
                True,
            )
        containers.add(mapping.container_ref)
        names = [f.field for f in mapping.fields]
        if len(set(names)) != len(names) or set(names) != set(fields):
            raise WorkerError(
                C.EXTRACTION_FAILED, "Map each schema field once, including missing fields.", True
            )
        data = {}
        for source in mapping.fields:
            if source.element_ref and not within(source.element_ref, mapping.container_ref, obs):
                raise WorkerError(
                    C.EXTRACTION_FAILED, "Field is outside its source result container.", True
                )
            data[source.field] = observed_value(source, fields[source.field], obs, task, site)
        if list(
            Draft202012Validator(site.record_schema, format_checker=FormatChecker()).iter_errors(
                data
            )
        ):
            raise WorkerError(
                C.EXTRACTION_FAILED, "Observed fields do not satisfy the output schema.", True
            )
        records.append(
            ExtractedRecord(
                data=data,
                source_observation_id=obs.observation_id,
                container_ref=mapping.container_ref,
                field_sources=mapping.fields,
                retrieved_at=obs.timestamp,
            )
        )
    return records


def expected_change(decision, before: Observation, after: Observation) -> bool:
    a = decision.arguments
    match a.expected_change:
        case "changed":
            return before.content_hash != after.content_hash
        case "unchanged":
            return before.content_hash == after.content_hash
        case "results_ready":
            return bool(after.signals.get("results_ready")) and not after.signals.get("loading")
        case "visible" | "value_equals":
            target = next((e for e in before.elements if e.ref == a.target_ref), None)
            if target is None:
                return False
            matches = [e for e in after.elements if (e.role, e.name) == (target.role, target.name)]
            return len(matches) == 1 and (
                a.expected_change == "visible" or matches[0].value == a.value
            )
    return False


def verify(
    task: SubtaskRequest,
    site: SiteConfig,
    report: SubtaskReport,
    obs: Observation,
    extracted_hash: str | None,
) -> list[CheckResult]:
    checks = []

    def check(name, passed, detail):
        checks.append(
            CheckResult(
                check_id=name,
                passed=bool(passed),
                detail=detail,
                evidence_refs=[obs.observation_id],
            )
        )

    refs = {o.observation_id: o for o in report.observations}
    records = report.records
    check(
        "fresh_extraction",
        extracted_hash == obs.content_hash,
        "Extraction must match the final observed page content.",
    )
    check(
        "current_run",
        all(
            o.execution_id == report.execution_id and o.run_id == task.run_id
            for o in report.observations
        ),
        "All evidence must belong to this execution.",
    )
    schema_ok = provenance_ok = True
    validator = Draft202012Validator(site.record_schema, format_checker=FormatChecker())
    for record in records:
        schema_ok &= not bool(list(validator.iter_errors(record.data)))
        source = refs.get(record.source_observation_id)
        provenance_ok &= bool(
            source
            and source.content_hash == obs.content_hash
            and record.retrieved_at == source.timestamp
        )
        if source:
            try:
                remapped = {
                    f.field: observed_value(
                        f, site.record_schema["properties"][f.field], source, task, site
                    )
                    for f in record.field_sources
                }
                provenance_ok &= remapped == record.data and all(
                    f.element_ref is None or within(f.element_ref, record.container_ref, source)
                    for f in record.field_sources
                )
            except (WorkerError, KeyError):
                provenance_ok = False
    check("output_schema", schema_ok, "Each record must satisfy the configured JSON schema.")
    check(
        "field_provenance", provenance_ok, "Every field must equal its current-run observed source."
    )
    # Count coverage prevents success by extracting only convenient matching rows.
    check(
        "visible_record_coverage",
        len(records) == obs.signals.get("record_count")
        and len(records) <= site.max_records
        and len({r.container_ref for r in records}) == len(records),
        "All visible configured records must be covered exactly once.",
    )
    ready = (
        obs.signals.get("results_ready")
        and not obs.signals.get("loading")
        and not obs.signals.get("error")
    )
    check(
        "results_or_empty",
        ready and (bool(records) or obs.signals.get("empty")),
        "The page must show settled results or an explicit configured empty state.",
    )
    check(
        "complete_observation", not obs.truncated, "Truncated observations cannot prove coverage."
    )
    applied = obs.signals.get("applied_parameters", {})
    for name, value in task.parameters.items():
        check(
            "applied:" + name,
            applied.get(name) == str(value),
            "The configured applied-result marker must equal the requested parameter.",
        )
    for i, condition in enumerate([*site.mandatory_conditions, *task.success_conditions]):
        value = task.parameters[condition.parameter] if condition.parameter else condition.value
        values = [r.data.get(condition.field) for r in records]
        match condition.kind:
            case "min_records":
                passed = len(records) >= value
            case "max_records":
                passed = len(records) <= value
            case "field_equals":
                passed = all(v == value for v in values)
            case "field_lte":
                passed = all(type(v) in {int, float} and v <= value for v in values)
            case "field_contains":
                passed = all(
                    isinstance(v, str) and str(value).casefold() in v.casefold() for v in values
                )
        check(f"condition:{i}:{condition.kind}", passed, "Configured/requested result condition.")
    links_ok = True
    try:
        guard_url(obs.url, site, task)
        for r in records:
            for field, value in r.data.items():
                if isinstance(value, str) and (
                    site.record_schema["properties"][field].get("format") == "uri"
                    or urlsplit(value).scheme in {"http", "https"}
                ):
                    guard_url(value, site, task)
    except (WorkerError, ValueError):
        links_ok = False
    check(
        "approved_links", links_ok, "Final URL and result links must remain within site authority."
    )
    return checks
