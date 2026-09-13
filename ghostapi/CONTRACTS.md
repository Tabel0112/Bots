# Ghost API workflow contract — versions 0.1 and 0.2

The implemented worker connection uses **0.2** on the existing `/v1` routes. See the
[integration contract and examples](INTEGRATION.md). It adds execution context,
compatibility keys, validation checks, embedded artifacts and idempotency keys to
candidates/runs; qualification references stored fresh runs. `0.1` fixture clients
remain supported, and `0.2` lookup never reuses an older fixture workflow. The workflow
examples below retain their historical `0.1` meaning.

Status: provisional contract. The FastAPI service implements an initial direct
subagent boundary; the local catalog demo remains a separate simplified fixture.

This is the authoritative location for the workflow schema and Ghost function interface extracted from the [shared contract](../docs/hackathon/CONTRACTS.md). Request, browser, result, error, and frontend contracts remain there. Field names and contract version are unchanged by this file move.

## Skill schema — B supplies, C stores

```json
{
  "schema_version": "0.1",
  "skill_id": "demo-catalog.search-products",
  "version": 1,
  "status": "candidate",
  "site_id": "demo-catalog",
  "operation": "search_products",
  "inputs": {
    "query": {"type": "string", "required": true},
    "max_price": {"type": "number", "minimum": 0, "required": true}
  },
  "preconditions": ["catalog search form is present"],
  "steps": [
    {
      "step_id": "set-query",
      "action": "fill",
      "target": {"role": "textbox", "label": "Search products"},
      "value": {"parameter": "query"},
      "expected_state": {"field_equals_parameter": "query"}
    }
  ],
  "output_schema_id": "product-list.v1",
  "validator_id": "catalog-search.v1",
  "source_run_ids": ["run-example"],
  "qualification": {"test_run_ids": [], "last_validated_at": null}
}
```

This abbreviated example illustrates the schema; its single step is not a complete runnable skill. Real compilation must include price binding, submission, result readiness, extraction, and checks. Values distinguish literals from named parameter references. Stable semantic targets resolve against the current page; old page element indexes are invalid across sessions.

Store `candidate → qualified → quarantined` as distinct states. One successful trace creates a candidate. Proposed demo qualification: replay in fresh sessions with two distinct changed input sets and one empty-result case where a real empty state can be verified; all required checks pass. If that coverage cannot be obtained, keep candidate status and report the missing coverage. These few trials demonstrate supported examples, not general reliability.

Repair creates a new candidate version with references to the old version, failure, and changed steps. Retain old version history. Quarantine the failing version for the affected scope; do not overwrite it or automatically promote an untested repair. C stores versions and run records durably for the demo; no concurrent promotion is needed.

## Agent-facing FastAPI interface

Every Steel-powered subagent calls Ghost directly. Task assignment, Steel sessions,
browser actions, and research remain outside Ghost.

- `POST /v1/workflows/lookup`: find a compatible qualified version and bind current
  values, or return an explicit `explore` decision.
- `POST /v1/workflows/candidates`: compile a successful normalized action trace into
  an immutable candidate version.
- `POST /v1/workflows/{skill_id}/versions/{version}/runs`: record a subagent's fresh
  reuse result, evidence, metrics, and failure data.
- `POST /v1/workflows/{skill_id}/versions/{version}/qualification`: submit separate
  replay reports; initial promotion requires three distinct passed input sets and an
  evidenced empty-result report.
- `GET /v1/workflows` and
  `GET /v1/workflows/{skill_id}/versions/{version}`: inspect the registry.
- `GET /v1/activity`: activity consumed by the workflow graph served at `/graph`.

OpenAPI at `/docs` is the executable field-level reference. Version 0.1 requests
reject unknown fields. Candidates are never returned by lookup. The first compiler
supports explicit `input_parameter` origins; broader trace inference and independent
operation-specific validation remain unfinished.

## Ghost domain interface

- `match(request, skills) -> MatchDecision`: compatible qualified skill or `explore`, with reason.
- `compile(trace, request, checks) -> CandidateSkill`: parameterize supported values; reject missing evidence or unsupported action shapes.
- `bind(skill, parameters) -> BoundProcedure`: strict required/type/scope checks; no silent unknown fields.
- `validate(request, items, evidence, checks) -> ValidationReport`.
- `qualify(candidate, replay_reports) -> QualificationReport`.
- `propose_repair(skill, failure, observation) -> RepairProposal`: uses A's target proposal if needed; cannot broaden task authority or edit the validator to pass.

The FastAPI application exposes these operations without starting a browser or task
controller. Subagents execute steps and return normalized evidence.
