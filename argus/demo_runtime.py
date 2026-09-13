"""Runnable, deterministic ARGUS demonstrations for Mission Control.

These scenarios exercise the production controller, contracts, scheduling,
validation, moderator boundary, persistence, and event stream. Browser findings
are controlled demo data so the application remains runnable without credentials.
"""

from __future__ import annotations

import copy
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from argus.contracts import (
    AnswerSelection,
    Claim,
    Criterion,
    InterpretedRequest,
    Note,
    ParameterOrigin,
    Plan,
    Subtask,
)
from argus.controller import Controller
from argus.fakes import FakeGhost, FakeToolbox, StubModerator
from argus.store import JsonStore

DEFAULT_REQUESTS = {
    "shopping": "Find headphones at most 150 USD and keyboards at most 100 USD. Show the cheapest matching product in each category, with its price and source.",
    "travel": "Plan a three-day Toronto trip with architecture, food, and waterfront stops. Keep the plan practical and show the source for every stop.",
    "jobs": "Find remote software engineering jobs and show the three highest salaries with company and source.",
}

SCENARIO_TERMS = {
    "shopping": ("buy", "shop", "price", "product", "headphone", "keyboard"),
    "travel": ("trip", "travel", "itinerary", "visit", "vacation", "toronto"),
    "jobs": ("job", "role", "career", "salary", "engineer", "remote"),
}

TRAVEL_ROWS = (
    {"title": "St. Lawrence Market", "day": 1, "time": "Morning", "kind": "food", "rating": 4.6, "area": "Old Town", "url": "https://travel.example.com/places/st-lawrence-market"},
    {"title": "Distillery District", "day": 1, "time": "Afternoon", "kind": "architecture", "rating": 4.6, "area": "Distillery", "url": "https://travel.example.com/places/distillery"},
    {"title": "Art Gallery of Ontario", "day": 2, "time": "Morning", "kind": "art", "rating": 4.7, "area": "Grange Park", "url": "https://travel.example.com/places/ago"},
    {"title": "Kensington Market", "day": 2, "time": "Afternoon", "kind": "food", "rating": 4.5, "area": "Kensington", "url": "https://travel.example.com/places/kensington"},
    {"title": "Toronto Islands", "day": 3, "time": "Morning", "kind": "waterfront", "rating": 4.7, "area": "Harbour", "url": "https://travel.example.com/places/toronto-islands"},
    {"title": "Harbourfront Centre", "day": 3, "time": "Afternoon", "kind": "waterfront", "rating": 4.5, "area": "Harbourfront", "url": "https://travel.example.com/places/harbourfront"},
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _origin(value: Any, text: str, start: int = 0) -> dict[str, Any]:
    return ParameterOrigin(value=value, source="structured", confidence=1.0, span=None).to_dict()


class DemoToolbox(FakeToolbox):
    """Controlled domain workers with visible execution latency."""

    def run_subtask(self, subtask_input):
        time.sleep(0.35)
        return super().run_subtask(subtask_input)

    def records_for(self, subtask, observation=None):
        records = super().records_for(subtask, observation)
        query = str((subtask.parameters or {}).get("query", "")).lower()
        category = "headphones" if "headphone" in query else "keyboards"
        for record in records:
            record["category"] = category
        return records

    def open_records_for(self, subtask, observations: Sequence[str | None]):
        if subtask.target_domain != "travel.example.com":
            return super().open_records_for(subtask, observations)
        observations = list(observations) or [None]
        params = subtask.parameters or {}
        if subtask.operation == "open_results" and isinstance(params.get("result_urls"), list):
            wanted = set(params["result_urls"])
            rows = [row for row in TRAVEL_ROWS if row["url"] in wanted]
        else:
            rows = list(TRAVEL_ROWS)
        return [
            {
                **{field: copy.deepcopy(row.get(field)) for field in (subtask.expected_record_shape or row)},
                "source_observation_id": observations[min(index, len(observations) - 1)],
            }
            for index, row in enumerate(rows)
        ]


class DemoModerator(StubModerator):
    """Structured demo selection; the controller still renders every line."""

    def synthesize(self, interpreted, records, validation, evidence, failures):
        self.calls.append(("synthesize", interpreted.request_id, len(records)))
        scenario = interpreted.model.rsplit("/", 1)[-1]
        indexed = list(enumerate(records))
        if scenario == "shopping":
            chosen = []
            for category in ("headphones", "keyboards"):
                matches = [(i, r) for i, r in indexed if isinstance(r, Mapping) and r.get("category") == category]
                if matches:
                    chosen.append(min(matches, key=lambda item: item[1].get("price", float("inf"))))
        elif scenario == "travel":
            days = int(interpreted.intents[0].parameters.get("days", ParameterOrigin(3, "structured", 1.0)).value)
            chosen = [item for item in indexed if item[1].get("day", 1) <= days]
        else:
            return super().synthesize(interpreted, records, validation, evidence, failures)
        record_indices = [index for index, _ in chosen]
        claims = []
        for selected_index, (_, record) in enumerate(chosen):
            fields = [name for name in record if name not in {"source_observation_id", "retrieved_at"}]
            claims.append(Claim(selected_index, fields))
        return AnswerSelection(record_indices, claims, [])


def infer_scenario(text: str) -> str:
    """Classify the three demonstrated domains from user language."""
    lowered = text.casefold()
    scores = {
        scenario: sum(term in lowered for term in terms)
        for scenario, terms in SCENARIO_TERMS.items()
    }
    winner = max(scores, key=scores.get)
    if scores[winner] == 0:
        raise ValueError(
            "ARGUS needs a shopping, travel-planning, or job-search request in this demo."
        )
    return winner


def _shopping_parameters(text: str) -> list[dict[str, Any]]:
    lowered = text.casefold()
    categories = [name for name in ("headphones", "keyboards") if name[:-1] in lowered]
    if not categories:
        raise ValueError("The shopping demo currently supports headphones and keyboards.")
    defaults = {"headphones": 150, "keyboards": 100}
    parsed = []
    for category in categories:
        singular = category[:-1]
        match = re.search(
            rf"{singular}s?.{{0,40}}?(?:under|below|at most|max(?:imum)?(?: price)?(?: of)?)\s*\$?([0-9]+)",
            lowered,
        )
        parsed.append({"query": singular if category == "keyboards" else category, "max_price": int(match.group(1)) if match else defaults[category]})
    return parsed


def interpreted_request(
    scenario: str | None,
    request_id: str,
    text: str | None = None,
    *,
    live: bool = False,
) -> InterpretedRequest:
    if scenario is None:
        if not text or not text.strip():
            raise ValueError("Request text is required.")
        scenario = infer_scenario(text)
    text = (text or DEFAULT_REQUESTS[scenario]).strip()
    if scenario == "shopping":
        intents = _shopping_parameters(text)
        built = []
        for params in intents:
            built.append({
                "site_id": "open:staples.com" if live else "demo-catalog",
                "operation": "search" if live else "search_products",
                "parameters": {key: _origin(value, text) for key, value in params.items()},
                "confidence": 1.0,
                **({"kind": "open", "target_domain": "staples.com", "goal": text,
                    "expected_record_shape": ["title", "price", "currency", "category", "url"]} if live else {}),
            })
        payload = {"request_id": request_id, "raw_text": text, "intents": built, "model": f"argus-{'live' if live else 'demo'}/shopping", "interpreted_at": _now()}
    else:
        if scenario == "travel":
            days_match = re.search(r"\b(?:a\s+)?(one|two|three|1|2|3)[ -]?day\b", text.casefold())
            day_values = {"one": 1, "two": 2, "three": 3, "1": 1, "2": 2, "3": 3}
            days = day_values.get(days_match.group(1), 3) if days_match else 3
            interests = [term for term in ("architecture", "food", "waterfront", "art") if term in text.casefold()]
            domain = "en.wikivoyage.org" if live else "travel.example.com"
            query = "Toronto " + " ".join(interests or ["highlights"])
            shape = ["title", "day", "time", "kind", "rating", "area", "url"]
            criteria = []
        else:
            domain = "remotive.com" if live else "jobs.example.com"
            query = "software engineering"
            shape = ["title", "company", "url", "salary", "remote"]
            limit_match = re.search(r"\b(?:top|first|show)\s+(\d+)\b", text.casefold())
            limit = max(1, min(6, int(limit_match.group(1)))) if limit_match else 3
            criteria = []
            if "remote" in text.casefold():
                criteria.append(Criterion("remote", "filter", {"field": "remote", "value": True}, None, 1.0))
            if any(term in text.casefold() for term in ("highest", "salary", "pay")):
                criteria.append(Criterion("highest salaries", "rank", "salary", None, 1.0))
            criteria.append(Criterion(str(limit), "limit", limit, None, 1.0))
        parameters = {"query": _origin(query, text)}
        if scenario == "travel":
            parameters["days"] = _origin(days, text)
        payload = {
            "request_id": request_id, "raw_text": text,
            "intents": [{
                "site_id": f"open:{domain}", "operation": "navigate",
                "parameters": parameters, "confidence": 1.0,
                "kind": "open", "target_domain": domain, "goal": text,
                "criteria": [criterion.to_dict() for criterion in criteria], "expected_record_shape": shape,
            }],
            "model": f"argus-{'live' if live else 'demo'}/{scenario}", "interpreted_at": _now(),
        }
    return InterpretedRequest.from_dict(payload)


def demo_plan(interpreted: InterpretedRequest, plan_id: str) -> Plan:
    scenario = interpreted.model.rsplit("/", 1)[-1]
    if scenario == "shopping":
        subtasks = []
        for index, intent in enumerate(interpreted.intents):
            params = {name: origin.value for name, origin in intent.parameters.items()}
            params.update({"max_results": 5, "currency": "USD"})
            subtasks.append(Subtask(
                f"shopping-{index + 1}", index, intent.site_id, intent.operation,
                params, f"shopping-{index + 1}", "product-list.v1",
                success_conditions=["records match query", "price is within limit", "source observation exists"],
                kind=intent.kind, target_domain=intent.target_domain, goal=intent.goal,
                expected_record_shape=intent.expected_record_shape,
            ))
        planned_by = "deterministic"
    else:
        intent = interpreted.intents[0]
        domain = intent.target_domain or ""
        prefix = "travel" if scenario == "travel" else "jobs"
        subtasks = [
            Subtask(
                f"{prefix}-research", 0, intent.site_id, "search",
                {"query": intent.parameters["query"].value, "max_results": 8},
                f"{prefix}-research", f"{prefix}-results.v1",
                success_conditions=["results cite current observations", "query is visibly applied"],
                kind="open", target_domain=domain, goal=intent.goal,
                criteria=intent.criteria, expected_record_shape=intent.expected_record_shape,
            ),
            Subtask(
                f"{prefix}-details", 0, intent.site_id, "open_results", {},
                f"{prefix}-details", f"{prefix}-details.v1",
                depends_on=[f"{prefix}-research"],
                success_conditions=["details come from result URLs", "each result cites a fresh observation"],
                inputs_from={"result_urls": {"subtask_id": f"{prefix}-research", "field": "url"}},
                kind="open", target_domain=domain, goal=intent.goal,
                criteria=intent.criteria, expected_record_shape=intent.expected_record_shape,
            ),
        ]
        planned_by = "model"
    return Plan(plan_id, interpreted.request_id, subtasks, _now(), planned_by)


def build_demo_controller(store_root: str | Path) -> Controller:
    return Controller(
        DemoToolbox(), DemoModerator(), FakeGhost(), JsonStore(store_root),
        max_concurrency=4, plan=demo_plan,
    )
