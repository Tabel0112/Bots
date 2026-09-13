"""Deterministic request, URL, value-origin, and action permission checks."""

import json
import re
from fnmatch import fnmatchcase
from urllib.parse import parse_qsl, unquote, urlsplit

from jsonschema import Draft202012Validator

from .config import SiteConfig
from .schemas import (
    ACTIONS,
    Decision,
    Element,
    Observation,
    SubtaskRequest,
    WorkerError,
)
from .schemas import (
    FailureCode as C,
)

SENSITIVE = re.compile(r"password|passwd|secret|token|api[_-]?key|cookie|authorization", re.I)
PROHIBITED = re.compile(
    r"\b(log[ -]?in|sign[ -]?in|purchase|buy|checkout|delete|submit|send|pay)\b", re.I
)
_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.I)
_OPEN_NUMBER_FIELDS = {"price", "salary", "rating"}


def _open_hostname(site_id: str) -> str:
    if not site_id.startswith("open:"):
        raise WorkerError(C.INVALID_INPUT, "open_search requires site_id open:<hostname>.")
    host = site_id.removeprefix("open:").strip().lower().rstrip(".")
    try:
        host.encode("ascii")
        parsed = urlsplit(f"//{host}")
        labels = host.split(".")
        valid = (
            parsed.hostname == host
            and parsed.port is None
            and 0 < len(host) <= 253
            and all(_HOST_LABEL.fullmatch(label) for label in labels)
        )
    except (UnicodeEncodeError, ValueError):
        valid = False
    if not valid:
        raise WorkerError(C.INVALID_INPUT, "open_search site_id contains an invalid hostname.")
    return host


def _open_schema(task: SubtaskRequest, host: str) -> SiteConfig:
    if "query" not in task.parameters or not isinstance(task.parameters["query"], str):
        raise WorkerError(C.INVALID_INPUT, "open_search requires a string query parameter.")
    if not task.parameters["query"].strip():
        raise WorkerError(C.INVALID_INPUT, "open_search query must not be empty.")
    if any(
        isinstance(value, bool) or not isinstance(value, (str, int, float))
        for value in task.parameters.values()
    ):
        raise WorkerError(
            C.INVALID_INPUT, "open_search filters must contain only strings or numbers."
        )
    fields = task.expected_record_shape
    if (
        not fields
        or len(set(fields)) != len(fields)
        or any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", field) for field in fields)
    ):
        raise WorkerError(C.INVALID_INPUT, "open_search requires a unique expected_record_shape.")

    start_url = task.start_url or f"https://{host}/"
    try:
        parsed = urlsplit(start_url)
        _ = parsed.port
    except ValueError:
        raise WorkerError(C.INVALID_INPUT, "open_search start_url is invalid.") from None
    domains = [host, f"www.{host}"]
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in domains:
        raise WorkerError(C.DOMAIN_NOT_ALLOWED, "open_search start_url must use its named host.")
    if task.start_url:
        netloc = parsed.netloc
        paired = f"www.{host}" if parsed.hostname == host else host
        paired_netloc = paired + (f":{parsed.port}" if parsed.port is not None else "")
        patterns = [f"{parsed.scheme}://{netloc}/*", f"{parsed.scheme}://{paired_netloc}/*"]
    else:
        patterns = [f"https://{host}/*", f"https://www.{host}/*"]

    parameter_properties = {
        name: {"type": "string" if isinstance(value, str) else "number"}
        for name, value in task.parameters.items()
    }
    record_properties = {}
    for field in fields:
        if field.casefold() in _OPEN_NUMBER_FIELDS:
            record_properties[field] = {"type": "number"}
        else:
            record_properties[field] = {"type": "string", "minLength": 1}
            if field.casefold() == "url":
                record_properties[field]["format"] = "uri"
    return SiteConfig(
        site_id=task.site_id,
        start_url=start_url,
        allowed_domains=domains,
        allowed_url_patterns=patterns,
        controls=[],
        parameters_schema={
            "type": "object",
            "additionalProperties": False,
            "required": list(task.parameters),
            "properties": parameter_properties,
        },
        output_schema_id="open-records.v1",
        record_schema={
            "type": "object",
            "additionalProperties": False,
            "required": fields,
            "properties": record_properties,
        },
        results_selector=None,
        record_selector=None,
        empty_selector=None,
        parameter_evidence=[],
        max_records=30,
        open_site=True,
    )


def guard_url(url: str, site: SiteConfig, task: SubtaskRequest, resource: bool = False):
    try:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
        ):
            raise ValueError()
        if "\\" in url or any(ord(c) < 32 for c in url) or parts.fragment:
            raise ValueError()
        _ = parts.port
        host = parts.hostname.lower()
        if resource and host in site.resource_domains:
            return
        if host not in site.allowed_domains or host not in task.allowed_domains:
            raise ValueError()
        # Match the whole URL (origin including port + decoded path), not a hostname suffix.
        canonical = f"{parts.scheme}://{parts.netloc}{unquote(parts.path) or '/'}"
        if not any(fnmatchcase(canonical, p) for p in site.allowed_url_patterns):
            raise ValueError()
        if task.allowed_url_patterns and not any(
            fnmatchcase(canonical, p) for p in task.allowed_url_patterns
        ):
            raise ValueError()
        query = parse_qsl(parts.query, keep_blank_values=True)
        # Open sites have no operator-declared query keys: any non-sensitive GET
        # query is allowed on the allowed domain (site searches redirect to URLs
        # such as /w/index.php?search=...). Configured sites keep their allowlist.
        if any(
            (not site.open_site and k not in site.allowed_query_keys) or SENSITIVE.search(k)
            for k, _ in query
        ):
            raise ValueError()
        if any(x in unquote(parts.path).split("/") for x in ("..", ".")):
            raise ValueError()
    except ValueError:
        raise WorkerError(
            C.DOMAIN_NOT_ALLOWED, "URL is outside the configured site/path policy."
        ) from None


def validate_request(raw: SubtaskRequest | dict, sites: dict[str, SiteConfig]):
    try:
        task = SubtaskRequest.model_validate(raw)
    except (ValueError, TypeError):
        raise WorkerError(C.INVALID_INPUT, "Request does not match SubtaskRequest 0.2.") from None
    if task.operation == "open_search":
        site = _open_schema(task, _open_hostname(task.site_id))
    else:
        site = sites.get(task.site_id)
        if not site:
            raise WorkerError(C.INVALID_INPUT, "Unknown site_id.")
    if task.output_schema_id != site.output_schema_id:
        raise WorkerError(C.INVALID_INPUT, "Unknown output schema for this site.")
    if list(Draft202012Validator(site.parameters_schema).iter_errors(task.parameters)):
        raise WorkerError(
            C.INVALID_INPUT, "Parameters do not match the configured operation schema."
        )
    if any(SENSITIVE.search(k) for k in task.parameters):
        raise WorkerError(
            C.INVALID_INPUT, "Sensitive parameters are not supported by this read-only worker."
        )
    if len(json.dumps(task.model_dump(mode="json"))) > 24000:
        raise WorkerError(C.INVALID_INPUT, "Request exceeds the compact worker input limit.")
    if PROHIBITED.search(task.objective):
        raise WorkerError(
            C.UNSUPPORTED_OPERATION,
            "Only read-only search/filter/extraction objectives are supported.",
        )
    if any(d.status != "succeeded" for d in task.prerequisites):
        raise WorkerError(C.PRECONDITION_FAILED, "Prerequisite subtasks must have succeeded.")
    if not set(task.allowed_domains) <= set(site.allowed_domains):
        raise WorkerError(C.DOMAIN_NOT_ALLOWED, "Request domains exceed the configured site.")
    if not set(task.allowed_actions) <= set(ACTIONS):
        raise WorkerError(C.ACTION_REJECTED, "Unsupported action requested.")
    covered = {r.parameter for r in site.parameter_evidence}
    if not site.open_site and not set(task.parameters) <= covered:
        raise WorkerError(
            C.INVALID_INPUT, "Every parameter needs configured applied-result evidence."
        )
    fields = set(site.record_schema.get("properties", {}))
    for condition in [*site.mandatory_conditions, *task.success_conditions]:
        if condition.parameter is not None and condition.parameter not in task.parameters:
            raise WorkerError(C.INVALID_INPUT, "Success check references a missing parameter.")
        value = task.parameters.get(condition.parameter) if condition.parameter else condition.value
        if condition.kind in {"min_records", "max_records"}:
            if type(value) is not int or value < 0 or condition.field is not None:
                raise WorkerError(C.INVALID_INPUT, "Record bounds must be nonnegative integers.")
        elif condition.field not in fields or value is None:
            raise WorkerError(
                C.INVALID_INPUT, "Field check needs a known field and comparison value."
            )
        elif condition.kind == "field_lte" and (type(value) not in {int, float}):
            raise WorkerError(C.INVALID_INPUT, "Numeric field bound must be a number.")
    guard_url(task.start_url or site.start_url, site, task)
    return task, site


def resolve_element(obs: Observation, ref: str | None) -> Element:
    found = [e for e in obs.elements if e.ref == ref]
    if not found:
        raise WorkerError(
            C.TARGET_NOT_FOUND, "Element is absent from the current observation.", True
        )
    if len(found) != 1:
        raise WorkerError(C.TARGET_AMBIGUOUS, "Element reference is not unique.", True)
    return found[0]


def validate_action(decision: Decision, task: SubtaskRequest, site: SiteConfig, obs: Observation):
    kind, a = decision.action_type, decision.arguments
    if kind not in task.allowed_actions:
        raise WorkerError(C.ACTION_REJECTED, "Action is outside this request's allowlist.", True)
    if a.observation_id != obs.observation_id:
        raise WorkerError(C.STALE_OBSERVATION, "Use references from the newest observation.", True)
    if kind in {"fill", "select", "click", "inspect_element"}:
        element = resolve_element(obs, a.target_ref)
        if kind != "inspect_element" and (
            element.disabled
            or (not site.open_site and kind not in element.permitted_actions)
            or (site.open_site and not _open_action_allowed(kind, element))
        ):
            raise WorkerError(
                C.ACTION_REJECTED, "This control is not authorized for that operation.", True
            )
        if kind != "inspect_element":
            matches = [
                e
                for e in obs.elements
                if (e.role, e.name, e.attributes.get("bound_parameter"))
                == (element.role, element.name, element.attributes.get("bound_parameter"))
                and (
                    _open_action_allowed(kind, e) if site.open_site else kind in e.permitted_actions
                )
            ]
            if not element.name or len(matches) > 1:
                raise WorkerError(
                    C.TARGET_AMBIGUOUS,
                    "Target lacks a unique semantic identity; inspect or request visual help.",
                    True,
                )
        if kind == "click" and element.href:
            guard_url(element.href, site, task)
    if kind in {"fill", "select"}:
        origin = a.value_origin
        if origin is None or a.value is None:
            raise WorkerError(C.ACTION_REJECTED, "A value and explicit origin are required.", True)
        values = task.parameters if origin.kind == "parameter" else site.approved_literals
        if origin.key not in values or a.value != str(values[origin.key]):
            raise WorkerError(C.ACTION_REJECTED, "Value does not match its approved origin.", True)
        required = resolve_element(obs, a.target_ref).attributes.get("bound_parameter")
        if required and (origin.kind != "parameter" or origin.key != required):
            raise WorkerError(
                C.ACTION_REJECTED, "The control is bound to a different parameter.", True
            )
    elif a.value is not None or a.value_origin is not None:
        raise WorkerError(C.ACTION_REJECTED, "Only fill/select accept input values.", True)
    if kind == "navigate":
        if a.url is None:
            raise WorkerError(C.ACTION_REJECTED, "Navigation requires a URL.", True)
        guard_url(a.url, site, task)
    elif a.url is not None:
        raise WorkerError(C.ACTION_REJECTED, "Only navigate accepts a URL.", True)
    if kind != "extract_records" and a.records:
        raise WorkerError(C.ACTION_REJECTED, "Only extraction accepts record mappings.", True)


def _open_action_allowed(kind: str, element: Element) -> bool:
    if kind == "fill":
        return element.role in {"textbox", "searchbox", "combobox"}
    if kind == "select":
        return element.role in {"textbox", "searchbox", "combobox"}
    if kind == "click":
        return element.role in {"button", "link", "checkbox"}
    return False
