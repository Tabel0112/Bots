"""Trusted deployment configuration. Requests may narrow, never widen, site authority."""

import json
import os
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from pydantic import Field

from .schemas import Model, SuccessCondition


class ControlRule(Model):
    selector: str
    actions: list[Literal["fill", "select", "click"]]
    parameter: str | None = None


class ParameterEvidence(Model):
    parameter: str
    selector: str
    attribute: Literal["text", "value"] = "text"


class SiteConfig(Model):
    site_id: str
    start_url: str
    allowed_domains: list[str]
    allowed_url_patterns: list[str]
    allowed_query_keys: list[str] = Field(default_factory=list)
    resource_domains: list[str] = Field(default_factory=list)
    controls: list[ControlRule]
    parameters_schema: dict[str, Any]
    output_schema_id: str
    record_schema: dict[str, Any]
    approved_literals: dict[str, str] = Field(default_factory=dict)
    results_selector: str | None
    record_selector: str | None
    empty_selector: str | None
    loading_selector: str = "[aria-busy='true']"
    error_selector: str = "[role='alert']"
    auth_selector: str = "input[type='password']"
    parameter_evidence: list[ParameterEvidence]
    mandatory_conditions: list[SuccessCondition] = Field(default_factory=list)
    max_records: int = Field(default=30, ge=1, le=100)
    # Optional operator-owned row recipe for independent validation after visual work.
    extraction_fields: list[dict[str, Any]] = Field(default_factory=list)
    open_site: bool = False


class Settings(Model):
    browser: Literal["steel", "local"] = "steel"
    model: Literal["gpt-5.4", "gpt-5.4-2026-03-05"] = "gpt-5.4"
    reasoning_effort: Literal["medium", "high"] = "medium"
    max_output_tokens: int = Field(default=4096, ge=512, le=16384)
    model_timeout_seconds: float = Field(default=45, gt=0, le=120)
    browser_timeout_seconds: float = Field(default=10, gt=0, le=60)
    browser_executable: str | None = None

    @classmethod
    def from_env(cls):
        return cls(
            browser=os.getenv("WORKER_BROWSER", "steel"),
            model=os.getenv("OPENAI_MODEL", "gpt-5.4"),
            reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "medium"),
            max_output_tokens=int(os.getenv("OPENAI_MAX_OUTPUT_TOKENS", "4096")),
            model_timeout_seconds=float(os.getenv("MODEL_TIMEOUT_SECONDS", "45")),
            browser_timeout_seconds=float(os.getenv("BROWSER_TIMEOUT_SECONDS", "10")),
            browser_executable=os.getenv("WORKER_BROWSER_EXECUTABLE") or None,
        )


def load_sites(path: str | Path | None = None) -> dict[str, SiteConfig]:
    source = path or os.getenv("WORKER_SITES_FILE") or Path(__file__).with_name("sites.json")
    sites = [SiteConfig.model_validate(s) for s in json.loads(Path(source).read_text("utf-8"))]
    for site in sites:
        Draft202012Validator.check_schema(site.parameters_schema)
        Draft202012Validator.check_schema(site.record_schema)
    if len({s.site_id for s in sites}) != len(sites):
        raise ValueError("Duplicate configured site_id")
    return {s.site_id: s for s in sites}
