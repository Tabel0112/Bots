# ARGUS phase 1b handoff — audit and corrected prompts

Written 2026-09-12 after Codex ran out of usage mid-phase. Branch `feat/argus-controller-base` at `7641f87` plus uncommitted changes. Everything below was verified against the working tree, not taken from reports.

## Audit: what is actually in the tree

Verified with `python3 -m unittest discover -s argus/tests -v` (261 tests, OK) and `git diff --check` (clean).

**Done and sound (phase 0b):**
- `contracts.py` at `0.3-argus-draft`: `Criterion`, open fields on `Intent` and `Subtask`, `inputs_from` with declared-dependency check, `Plan.planned_by` and `caps` with the 4/3 ceilings, three new error codes. Old fixtures load through defaults.
- `registry.DOMAIN_POLICY` and `domain_allowed()` with a thorough offline test module. Note: `example.com` and `jobs.example.com` are allowed; only the bare `.example` TLD is blocked, so the fixtures are consistent with the policy.
- Controller rejects `max_concurrency` above 4 and non-integers, with tests.
- Five new fixtures. `plan_open_chain.json` passes `validate_plan` today.
- Docs (README, TEAM, CURRENT, DECISIONS, ARGUS.md, plan) describe this honestly as "0b built, E/F/2b pending".

**Started but incomplete (the traps):**
- `interpreter.py`: the prompt text and the Pydantic `IntentOut` have the open fields, but `_intent()` (around line 427) still builds `Intent(site_id, operation, parameters, confidence)` only. Every open field the model returns is discarded, so every interpretation is `kind="registry"`.
- `planner.plan_open()` exists and dispatches by intent kind, but has **zero tests**. Its Pydantic `Step` schema takes `inputs_from` as a **list of `{parameter, subtask_id, field}` bindings**, while `plan_open_chain.json` stores `inputs_from` as a **dict keyed by parameter** and also carries context fields the strict schema forbids. Any fake client that replays the fixture verbatim will fail `model_validate`.
- `__main__.py --plan-fixture` lazily imports `argus.fakes.FakePlannerClient`, which **does not exist**. The flag currently crashes with `ImportError`.
- `interfaces.Ghost.validate` gained an optional `report_context` keyword, but the controller never passes it and `FakeGhost.validate` does not accept it. Dead on both ends.

**Not started:**
- `gate.py`: no S-rules. Open intents fall through the registry rules and are rejected by G2/G3.
- `fakes.py`: no open dataset in `FakeToolbox`, no generic validation in `FakeGhost`, no criteria-aware synthesis in `StubModerator`, no `FakePlannerClient`.
- `controller.py`: no `inputs_from` data transfer; a dependent subtask never receives the upstream findings.
- `test_planner.py`, `test_end_to_end.py`: untouched.
- `interpreted_request_open_salary.json` has a salary rank criterion but **no "remote only" filter criterion**, so it cannot drive the "highest salary, remote only" end-to-end case as written.

## What was wrong with the previous handoff prompt

1. It bundled E, F, 2b and docs into one run. That is the shape that ran out of usage once already. Below it is split into three sequential prompts with a checkpoint after each; each names exact files.
2. It did not state the concrete breakages above, so the next agent would rediscover them. They are now in each prompt.
3. It did not mention the fixture-versus-schema mismatch for `inputs_from`, which is the one detail most likely to burn an hour.
4. It asked for `report_context` to be finished "or removed cleanly" without saying the controller and fake are both unwired. Decision below: finish it, because generic validation needs run and evidence metadata.
5. The salary fixture gap above was not mentioned.

## Decision that changes the prompts: OpenAI-compatible model access

Abel confirmed on 2026-09-12 that the team connects models through OpenAI-style APIs, not the Anthropic SDK. `argus/interpreter.py` and `planner.plan_open` currently call `client.messages.parse` (Anthropic shape) and lazily import `anthropic`. Prompt E0 below replaces that with one model-client boundary before E and F build on it. E and F must use the model client, never the SDKs directly.

## Prompt E0 — model client boundary (Opus 5, high)

```text
You are replacing the model-call boundary of the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Branch feat/argus-controller-base at 7641f87 plus uncommitted work; do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-1B.md (whole), docs/ai/DECISIONS.md (the "Model access for ARGUS" entry), then argus/interpreter.py, argus/planner.py, argus/fakes.py, argus/tests/test_interpreter.py and argus/tests/test_planner.py.

Create argus/model_client.py. Edit only: argus/interpreter.py, argus/planner.py, argus/fakes.py, argus/tests/test_interpreter.py, argus/tests/test_planner.py, plus a new argus/tests/test_model_client.py.

model_client.py:
- ModelResult dataclass: status ("ok" | "refusal" | "truncated" | "invalid"), parsed (dict or None), raw_text (str or None), model (str).
- ModelClient Protocol: parse_json(system: str, user: str, output_model: type, max_tokens: int) -> ModelResult, where output_model is a pydantic BaseModel subclass whose JSON schema is enforced by the backend.
- OpenAICompatibleClient(model=None, base_url=None, api_key=None): lazily imports the openai package inside the constructor; reads OPENAI_API_KEY and OPENAI_BASE_URL from the environment when not given (the SDK does this natively; do not duplicate parsing), and ARGUS_MODEL for the model name with no hardcoded default other than raising a clear ContractError(code="PRECONDITION_FAILED") if none is set. parse_json calls the SDK's structured-output parse method (chat.completions.parse with response_format=output_model on current openai-python; if that name differs in the installed version, read the installed package source and adapt, and say so in the report). Map: a refusal on the message -> status "refusal"; finish_reason "length" -> "truncated"; a parse or validation error -> "invalid"; otherwise "ok" with parsed = the validated model's model_dump(). Never read message content before checking refusal.
- No network in tests. Test OpenAICompatibleClient by injecting a fake SDK object through a private constructor argument; if the openai package is not installed locally, the constructor path that imports it is tested for the PRECONDITION_FAILED message only.

fakes.py: add FakeModelClient(results: list[ModelResult] | ModelResult) implementing ModelClient, returning results in order and recording every call's system, user and output_model in .calls. Remove any Anthropic-shaped fake client helpers in the tests once the callers are migrated.

interpreter.py: replace client.messages.parse, _default_client, _resolve_model and stop_reason handling with a ModelClient. Prompt E already landed: keep its tier-two parsing, _hostname, _malformed_domain, criterion span verification and the registry early-return path exactly as they are, and migrate its new tests (the open_intent/criterion fakes) to FakeModelClient. interpret(text, request_id, client=None, model=None): client None -> OpenAICompatibleClient(model=model). Map status refusal -> ContractError MODEL_REFUSED, truncated or invalid -> EXTRACTION_FAILED. Keep every existing behavior (span verification, ambiguity rules, prompt content) and adjust the existing tests to FakeModelClient; the set of behaviors covered by tests must not shrink.

planner.py: plan_open takes a ModelClient the same way and stops importing private names from interpreter.

README is not yours; note for 2b that environment names are now OPENAI_API_KEY, OPENAI_BASE_URL, ARGUS_MODEL.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report: files changed with one line each, the installed openai version if present or "not installed", exact test count and time, anything unverified, git status --short, and confirm nothing was staged or committed.
```

## Prompt E — interpreter tier two and safety gate

```text
You are completing the open-world interpreter and gate of the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Branch feat/argus-controller-base at 7641f87 plus uncommitted work; do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/.

Read AGENTS.md, then docs/hackathon/ARGUS-HANDOFF-1B.md (this file, whole), the "Open-world navigation" section of docs/hackathon/ARGUS.md, then argus/contracts.py, argus/registry.py, argus/interpreter.py, argus/gate.py and their tests. contracts.py and registry.py are read-only for you.

Edit only: argus/interpreter.py, argus/gate.py, argus/tests/test_interpreter.py, argus/tests/test_gate.py, and the fixture argus/examples/interpreted_request_open_salary.json.

Known state: the Pydantic IntentOut already has kind, target_domain, goal, criteria and expected_record_shape, and the system prompt already describes tier two, but _intent() discards all of them. Start there.

Interpreter:
- Parse kind, target_domain, goal, criteria and expected_record_shape into the Intent dataclass. Registry intents keep today's behavior byte for byte (existing tests must not change).
- Criteria: convert CriterionOut (span_start/span_end) into Criterion(text, kind, parameter, span, confidence). Verify each criterion span against raw_text with the same rule as parameters; on mismatch set span None, confidence 0.0 and add an ambiguity.
- target_domain: accept only what the model returns; if it is not None, run registry.domain_allowed for a syntax check and, if the hostname is malformed, set it to None and add an ambiguity. Never derive a domain in code from a site name. Extracting a hostname from an explicit URL in the text is acceptable in code (urllib.parse) and must be tested.
- Use the ModelClient from Prompt E0; keep refusal and truncation mapping and the max_tokens call shape unchanged; existing E0 tests must still pass.
- Tests (fake client, offline): open intent with explicit URL yielding target_domain; open intent with no site yielding target_domain None plus an ambiguity; "best 10 jobs" yielding a rank criterion below 0.6 and a limit criterion 10; "highest salary, remote only" yielding a rank criterion with parameter "salary" and a filter criterion with parameter "remote", each with its own verified span; a registry request still preferred over open; criterion span mismatch downgraded.

Gate (argus/gate.py):
- Keep G0 to G7 for kind "registry" unchanged.
- For kind "open", apply in order and stop at the first hit: S1 target_domain None -> clarify, question asks which site to use; S2 registry.domain_allowed false -> reject, reason includes the policy reason and error code DOMAIN_NOT_ALLOWED; S3 goal None or blank -> clarify; S4 any criterion of kind rank with confidence below 0.6 -> clarify with exactly this question text: "What should 'best' mean? For example, highest salary, remote-only roles, or closest match to your experience." (substitute the criterion's text for 'best'); S5 the goal or raw_text requests performing a login, payment, purchase, checkout, or a form submission or post that changes state -> reject ACTION_CLASS_NOT_ALLOWED, but reading or searching documentation about those topics stays allowed; implement as a small verb-plus-object matcher and document it as a preflight first cut, not an action classifier; otherwise accept with rule_id "S0".
- Tests: every S-rule, both open gate fixtures, a "how do I log in to X" documentation request accepted, a "log in to X and download my invoices" request rejected, and the interpreted_request_open.json fixture gating to clarify on S4 with the example question.

Fixture: add a filter criterion {"text": "remote only", "kind": "filter", "parameter": "remote", "span": <verified against raw_text>, "confidence": 0.97} to interpreted_request_open_salary.json and update raw_text to "Find the highest salary 10 software engineering jobs, remote only, on jobs.example.com" so every span verifies. Update expected_record_shape to include "remote".

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report: root causes found, files changed with one line each, exact test count and time observed, anything unverified, git status --short, and confirm nothing was staged or committed.
```

## Prompt F — planner client, controller data flow, fakes

```text
You are completing model-planned open-world execution for the ARGUS controller in the repository at /Users/baiyangchen/Developer/Coding/Bots. Branch feat/argus-controller-base at 7641f87 plus uncommitted work including the finished Prompt E; do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/.

Read AGENTS.md, docs/hackathon/ARGUS-HANDOFF-1B.md (whole), the "Open-world navigation" section of docs/hackathon/ARGUS.md, then argus/contracts.py, argus/interfaces.py, argus/planner.py, argus/controller.py, argus/fakes.py and their tests. contracts.py is read-only. interfaces.py may change only as stated below.

Edit only: argus/planner.py, argus/controller.py, argus/fakes.py, argus/interfaces.py, argus/tests/test_planner.py, argus/tests/test_controller.py, argus/tests/test_fakes.py.

Known state and traps:
- planner.plan_open exists and is untested. After E0 it takes a ModelClient. Its Pydantic Step schema expects inputs_from as a list of {parameter, subtask_id, field} bindings and forbids extra keys, while argus/examples/plan_open_chain.json stores inputs_from as a dict keyed by parameter and carries context fields. A fake client must return only scheduling fields in the schema's shape.
- argus/__main__.py imports argus.fakes.FakePlannerClient, which does not exist yet.
- interfaces.Ghost.validate has an optional report_context keyword that nothing passes and FakeGhost does not accept.
- Controller has no inputs_from data transfer.

Planner:
- Add tests for plan_open with an injected fake client: a search-then-open_results chain with result_urls bound to the search step's url; refusal -> MODEL_REFUSED; stop_reason other than end_turn -> EXTRACTION_FAILED; a cycle rejected; five steps rejected PLAN_TOO_LARGE; depth four rejected; intent_index pointing at a registry intent rejected; a binding that would overwrite an accepted parameter rejected; a mixed request producing one plan with the registry subtask planned deterministically and the client called exactly once; a registry-only request never touching the client (assert the fake's call count is zero).
- Do not weaken any existing check. Confirm in the report that model output cannot change target_domain, goal, criteria, expected_record_shape, parameters or caps.

FakePlannerClient (argus/fakes.py):
- FakePlannerClient(plan_payload) implements the E0 ModelClient protocol: parse_json returns ModelResult(status="ok", parsed={"subtasks": [...]}) containing only the Step schema fields, converting each fixture subtask's inputs_from dict into the binding list and dropping context fields, and records calls in .calls. Optional status override for refusal and truncation tests. It must be explicitly constructed by the caller; never substitute it when client is None.

Controller (argus/controller.py):
- Before building SubtaskInput for a subtask with inputs_from, resolve each binding from the named dependency's accepted report: field "findings" gives the whole findings object; for a findings list, any other field collects that top-level field from every record in order (empty list stays empty); for a findings mapping, that field's value. A missing field or a dependency without an accepted report fails the subtask with PRECONDITION_FAILED and cancels its dependents. Resolved values go into subtask.parameters for the worker; no other transformation.
- Pass report_context to ghost.validate as a keyword only for kind "open" subtasks: {"run_id", "subtask_id", "evidence": the report's evidence dict with any session handle removed, "empty_state": bool derived from the report}. Registry calls keep the three-argument form. Keep the interfaces.py docstring accurate to this.
- Tests: the two-step chain with url list transfer; missing field fails and cancels dependents; findings mapping transfer; open validate receives context with no session handle; the chain respects max_concurrency.

FakeToolbox:
- For kind "open" subtasks, return findings from a deterministic dataset keyed by target_domain (add jobs.example.com with at least six job records having title, company, url on that domain, salary as a number and remote as a boolean). For operation "open_results" with a result_urls parameter, return one record per URL in order, on the same domain, each with its own observation reference. Findings follow the subtask's expected_record_shape; every record carries source_observation_id from this run. Script outcomes and self.calls behave as today.

FakeGhost:
- For kind "open" subtasks apply generic checks only: every record cites an observation that appears in report_context["evidence"] or the report's screenshots; records non-empty unless report_context["empty_state"] is true; the query or filter is evidenced by at least one observation (accept any observation reference for the fake); every url is on subtask.target_domain. Registry validation unchanged. Accept the report_context keyword with default None.

StubModerator:
- synthesize applies explicit criteria when the records carry the field: filter criteria drop records where the field is falsy or does not match; rank criteria sort descending on the field; limit criteria truncate. Criteria whose field is absent from the records are listed in FinalAnswer.unverified with a sentence saying why. Claims still cite the record's observation.

Run python3 -m unittest discover -s argus/tests -v and git diff --check. Report: root causes found, files changed with one line each, exact test count and time, anything unverified, git status --short, and confirm nothing was staged or committed.
```

## Prompt 2b — CLI, end-to-end tests, docs

```text
You are integrating open-world navigation into the ARGUS controller base in the repository at /Users/baiyangchen/Developer/Coding/Bots. Branch feat/argus-controller-base at 7641f87 plus uncommitted work including finished Prompts E, E0 and F; do not stage, commit, push, pip install or use the network. Preserve untracked backend/, scripts/ and tests/. The suite currently has 397 tests and must not end with fewer.

Read AGENTS.md, docs/ai/TEAM.md, docs/ai/CURRENT.md, docs/hackathon/ARGUS-HANDOFF-1B.md (whole), docs/hackathon/ARGUS.md, docs/hackathon/ARGUS-IMPLEMENTATION.md, then every file under argus/.

Edit: argus/__main__.py, argus/fakes.py (reconcile only, see below), argus/tests/test_end_to_end.py, argus/tests/test_fakes.py (reconcile tests only), argus/README.md, docs/ai/TEAM.md (ARGUS-1 row and one new update block in the compact format), docs/ai/CURRENT.md ("Latest work and next useful step" only), docs/hackathon/ARGUS.md and docs/hackathon/ARGUS-IMPLEMENTATION.md (only the sections describing phase 1b status). Fix other integration mismatches in the module that is wrong; if a contract change is unavoidable, stop and report the smallest change instead of making it.

Known defect to fix first: StubModerator.reconcile concatenates every accepted report's findings, so in the search-then-open_results chain each job appears twice in the final answer. Fix reconcile so that when a later subtask's records share a url with an earlier subtask's records, the later (more detailed) record supersedes the earlier one; records without a url are kept as they are; order follows the later report. Add tests for supersession, for no-url records, and for two independent subtasks whose records do not overlap. Do not touch synthesize.

CLI: --plan-fixture FILE works only with --interpreted FILE, loads a Plan JSON, wraps it in argus.fakes.FakePlannerClient and injects it through Controller(plan=...). Document it in --help and the README as explicit offline planner injection that never replaces a real model call. Replace the stale module docstring in argus/__main__.py that names ANTHROPIC_API_KEY: the environment names are OPENAI_API_KEY, OPENAI_BASE_URL and ARGUS_MODEL. Exit codes unchanged.

End-to-end tests (fakes only, offline), in argus/tests/test_end_to_end.py:
a) interpreted_request_open.json -> needs_input; the questions contain the S4 example text; no session opened; store has run.json and events.jsonl.
b) interpreted_request_open_salary.json plus plan_open_chain.json through FakePlannerClient -> succeeded; two dependent subtasks ran in order; the second received result_urls as an ordered list; generic validation passed; the answer's records are unique by url, sorted by salary descending, remote-only, at most ten, each claim citing an observation; store has one report per subtask.
c) a copy of the open fixture with target_domain "127.0.0.1" -> a failed result whose error code is DOMAIN_NOT_ALLOWED; and with "intranet.corp" -> the same.
d) a five-step plan fixture -> failed with PLAN_TOO_LARGE before any session opens (assert the fake toolbox opened nothing).
e) the registry fixture path produces the same result as before (status, claims and validation match the existing end-to-end expectations).

Run: python3 -m unittest discover -s argus/tests -v; git diff --check; python3 -m argus --fake --interpreted argus/examples/interpreted_request_open.json --store /tmp/argus-open-a (expect needs_input, exit 1); python3 -m argus --fake --interpreted argus/examples/interpreted_request_open_salary.json --plan-fixture argus/examples/plan_open_chain.json --store /tmp/argus-open-b (expect succeeded, exit 0, records unique by url).

Docs: describe actual behavior only. README covers the two interpreter tiers, the S-rules, the caps, the domain policy, generic versus registry validation, the model client and its three environment names, that openai 2.14.0 is installed locally but no live call has been made, and the --plan-fixture flag. Label as pending live toolbox integration: DNS, redirect, rebinding and request enforcement; shared four-slot VLM arbitration across runs; real Steel sessions; Thomas's moderator; Sting's Ghost receiver verification including the report_context keyword on Ghost.validate which Sting has not seen. TEAM and CURRENT record only checks actually run with real output. Move the ARGUS-1 row to Ready to connect only if every command above passed.

Report: root causes found, files changed with one line each, every command with its exact observed output summary, anything unverified, git status --short, and confirm nothing was staged or committed.
```

## Order and checkpoints

Actual order: E ran first (2026-09-12, accepted after review: 298 tests OK, open fixture gates to clarify S4 with the approved question, all salary-fixture spans verify). E0 landed next (accepted after review: 332 tests OK, no Anthropic import or call shape left in interpreter.py or planner.py, openai 2.14.0 installed locally, FakeModelClient satisfies the protocol). F landed (accepted after review: 397 tests OK; the salary chain runs to succeeded with both subtasks accepted, but each job appears twice because StubModerator.reconcile concatenates both reports; 2b fixes that). Then 2b. Note for 2b: argus/__main__.py line 21 docstring still names ANTHROPIC_API_KEY. After each: `python3 -m unittest discover -s argus/tests -v` and `git diff --check`. Abel commits after 2b. If the tool supports a single long session, the three prompts may be given back to back, but each must finish and report before the next starts.
