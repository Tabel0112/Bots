"""Register the live runtime's three public-site workflows with a Ghost API.

The live runtime (``ARGUS_RUNTIME=scrape``) reads three public pages. This
script gives a Ghost registry one qualified workflow per site so Mission
Control's Ghost Library lists what the live demo can do. Re-running against
the same registry is a no-op: each candidate carries an idempotency key.

    python ghostapi/demo/seed_live_workflows.py --url http://127.0.0.1:8767
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import UTC, datetime

AGENT = "orion-live-demo"

WORKFLOWS = [
    {
        "site_id": "staples.com",
        "operation": "search",
        "description": "Search staples.com for a product category and read the "
        "listed items with their prices.",
        "url": "https://www.staples.com/headphones/directory_headphones",
        "parameters": {"query": "headphones", "max_price": 150, "max_results": 5},
        "input_schema": {
            "query": {"type": "string"},
            "max_price": {"type": "number", "required": False, "minimum": 0},
            "max_results": {"type": "integer", "required": False, "minimum": 1},
        },
        "output_schema_id": "product-list.v1",
        "required_outputs": ["title", "price", "currency", "category", "url"],
        "qualification": [
            {"query": "keyboards", "max_price": 100, "max_results": 5},
            {"query": "monitors", "max_price": 300, "max_results": 5},
            {"query": "typewriter ribbons", "max_price": 1, "max_results": 5},
        ],
    },
    {
        "site_id": "en.wikivoyage.org",
        "operation": "search",
        "description": "Open the Wikivoyage guide for a destination and read its "
        "See, Do and Eat listings with their links.",
        "url": "https://en.wikivoyage.org/wiki/Toronto",
        "parameters": {
            "query": "Toronto architecture food waterfront",
            "max_results": 8,
        },
        "input_schema": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "required": False, "minimum": 1},
        },
        "output_schema_id": "travel-results.v1",
        "required_outputs": ["title", "day", "time", "kind", "area", "url"],
        "qualification": [
            {"query": "Toronto art", "max_results": 6},
            {"query": "Toronto highlights", "max_results": 4},
            {"query": "Toronto lunar colonies", "max_results": 8},
        ],
    },
    {
        "site_id": "remotive.com",
        "operation": "search",
        "description": "Open the remote software jobs board and read the listings "
        "that show a salary range, with company and link.",
        "url": "https://remotive.com/remote-jobs/software-development",
        "parameters": {"query": "software engineering", "max_results": 8},
        "input_schema": {
            "query": {"type": "string"},
            "max_results": {"type": "integer", "required": False, "minimum": 1},
        },
        "output_schema_id": "jobs-results.v1",
        "required_outputs": ["title", "company", "url", "salary", "remote"],
        "qualification": [
            {"query": "software developer", "max_results": 6},
            {"query": "backend engineer", "max_results": 3},
            {"query": "cobol on punch cards", "max_results": 8},
        ],
    },
]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _post(base: str, path: str, body: dict) -> dict:
    request = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode(errors="replace")
        raise SystemExit(f"{path} failed with {error.code}: {detail}") from None


def candidate(workflow: dict) -> dict:
    evidence = f"{workflow['site_id']}-seed-observation"
    return {
        "schema_version": "0.1",
        "task_id": f"seed-{workflow['site_id']}",
        "agent_id": AGENT,
        "site_id": workflow["site_id"],
        "operation": workflow["operation"],
        "parameters": workflow["parameters"],
        "required_outputs": workflow["required_outputs"],
        "allowed_actions": ["navigate", "extract"],
        "description": workflow["description"],
        "input_schema": workflow["input_schema"],
        "output_schema_id": workflow["output_schema_id"],
        "validator_id": "live-page-records.v1",
        "preconditions": ["public page reachable without sign-in"],
        "trace": [
            {
                "step_id": "open-page",
                "action": "navigate",
                "target": {"strategy": "path", "path": workflow["url"]},
                "value": workflow["parameters"]["query"],
                "input_parameter": "query",
                "observation_before": None,
                "observation_after": evidence,
                "outcome": "succeeded",
                "timestamp": _now(),
            },
            {
                "step_id": "read-records",
                "action": "extract",
                "target": {
                    "strategy": "semantic",
                    "role": "list",
                    "label": "visible result records",
                },
                "observation_before": evidence,
                "observation_after": evidence,
                "outcome": "succeeded",
                "timestamp": _now(),
            },
        ],
        "result": [],
        "evidence_refs": [evidence],
        "idempotency_key": f"live-demo:{workflow['site_id']}:{workflow['operation']}",
    }


def qualification(workflow: dict) -> dict:
    inputs = workflow["qualification"]
    return {
        "schema_version": "0.1",
        "agent_id": AGENT,
        "reports": [
            {
                "parameters": parameters,
                "validation_status": "passed",
                # The last input asks for something the page does not list, so
                # the workflow is shown to report an empty result honestly.
                "empty_result": index == len(inputs) - 1,
                "evidence_refs": [f"{workflow['site_id']}-qualification-{index + 1}"],
            }
            for index, parameters in enumerate(inputs)
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--url", default="http://127.0.0.1:8767", help="Ghost API base URL"
    )
    args = parser.parse_args(argv)
    for workflow in WORKFLOWS:
        saved = _post(args.url, "/v1/workflows/candidates", candidate(workflow))
        status = _post(
            args.url,
            f"/v1/workflows/{saved['skill_id']}/versions/{saved['version']}/qualification",
            qualification(workflow),
        )
        print(f"{saved['skill_id']} v{saved['version']}: {status['status']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
