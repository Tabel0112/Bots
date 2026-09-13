# ARGUS connection audit — can the controller connect to teammates' code?

Read-only audit on 2026-09-12 of `workers/visual/` (Thomas), `browser_worker/` (Tianqi) and `ghostapi/` (Sting) against `argus/interfaces.py` and `argus/contracts.py`. Nothing was changed in teammates' packages. File and line references were verified in the working tree at the time; recheck before relying on them.

## Verdict

| Connection | Callable today | Input gap | Output gap | Session gap | Blocker |
| --- | --- | --- | --- | --- | --- |
| Toolbox via visual worker | Partly. `UITarsSubagent().run(prompt, start_url)` is sync and importable | Takes a prose string and a start URL only; no receiver for budget seconds; ARGUS never supplies a start URL | All 13 report fields present by name, but `findings` is always `None` on the UI-TARS backend, `metrics` is absent on the Claude backend, screenshot refs differ from the committed example, no typed failures | **Hard.** Creates and releases its own Steel session; cannot accept a handle | Session injection needs a code change by Thomas. No `observe`, `dom_interpret` or `vision_interpret` exist |
| Toolbox via DOM worker | Yes. `await Worker().run(request)` | Large but mechanical: objective, allowlists, typed success conditions, operation name `search_extract` vs `search_products`; `mode: reuse` has no receiver; open-world subtasks impossible (closed operation literal, configured sites only) | Field renames (`records`, `action_trace`, `evidence_refs`, `metrics.actions`), 12 extra fields the ARGUS loader rejects, 8 failure codes to remap, `needs_visual` and `inconclusive` outcomes need moderator policy | **Soft.** Accepts `ownership: argus` plus an opaque `session_ref` and retains the session. Steel mode only, one page, caller serialises | None structural |
| Moderator | **No implementation exists** anywhere | | | | Must be written against `interfaces.Moderator`; only the stub exists |
| Ghost | Partly. `verify()` is sync and importable | `verify(params_dict, items, evidence_dict)` vs `validate(Subtask, records, list[str], report_context=)`; `evidence` must be a dict with `applied_inputs` and `empty_state` or it raises; no `match`, no `compile` | `verify` returns a compatible `{status, checks}` mapping but never `inconclusive`; the candidate definition lacks `skill_id` and `status`, which the store requires | n/a | **CAD vs USD**, and `run_task` self-promotes skills |

## Two things adapters cannot fix

1. **Visual worker session ownership.** `SteelBrowser.__init__` has no session parameter; `start()` always calls `sessions.create` and `stop()` always releases. ARGUS opens the session first, so the worker would open a second one. Thomas needs to add a session id parameter and gate release on ownership, mirroring the DOM worker's `ownership` field.
2. **Currency.** Sting's replay stamps every row `CAD` and his verifier requires `CAD`; ARGUS's registry and Tianqi's site both fix `USD`. Connected as-is, every cross-component validation fails. Converting in an adapter would fabricate prices. One owner changes.

## Safety findings

- `ghost_demo.run_task` calls `qualify`, which sets `status='qualified'` in the same transaction as the task result. An adapter must call `verify` and `replay` only, never `run_task` or `qualify`; ARGUS stage 11 stores candidates only.
- Ghost's `verify` derives ground truth from a hard-coded in-memory catalog, so it can only validate fixture rows, not anything a real worker returns. Its own `scope` string says "synthetic fixture only".
- The DOM worker's report carries its own `validation` field. It must never be forwarded as Ghost's validation.
- The visual worker's report embeds the live Steel session id and replay URL in `evidence`; ARGUS strips only `session_handle`. The adapter must strip these too.
- No worker enforces an action class. Nothing stops the VLM from clicking a login or submit control. `ACTION_CLASS_NOT_ALLOWED` has no live enforcer yet.

## Smallest adapter per connection

- **Visual toolbox** (after Thomas's session change): hold one `SteelBrowser` per handle; render the prompt from `goal`, `parameters` and `success_conditions`; call `run`; build the report with `build_worker_report`; normalise screenshot paths to observation ids; map the four prose abort strings to typed codes (max steps → `BUDGET_EXCEEDED`, refusal → `MODEL_REFUSED`, session lost → `PRECONDITION_FAILED`). `observe` = screenshot plus id allocator; `vision_interpret` = one VLM call with the question and screenshot (new, small).
- **DOM toolbox** (no change on Tianqi's side): build `SubtaskRequest` from `SubtaskInput` (objective from goal or operation plus parameters; `search_products` → `search_extract`; allowlists from `registry.SITES` and `DOMAIN_POLICY`; ceil the budget; `session={"ownership": "argus", "session_ref": handle, "close_on_finish": False}`; drop free-text success conditions and record it as a limitation); `asyncio.run(Worker().run(req))`; rebuild `WorkerReport` field by field, including `browser_action_count` from `metrics.actions` and typed failures via a code table (`BUDGET_EXHAUSTED` → `BUDGET_EXCEEDED`, `ACTION_REJECTED` → `ACTION_CLASS_NOT_ALLOWED`, `MODEL_TIMEOUT` → `EXTRACTION_FAILED`). ARGUS's `open_session` must create a real Steel session, about ten lines lifted from `adapter.py`.
- **One shared toolbox**: feasible in Steel mode only. ARGUS opens the session and hands the same id to both workers. This is what makes `needs_visual` continuous: the DOM worker returns `needs_visual` with url, observation and question; the moderator returns `retry_other_path`; the visual worker resumes on the same page.
- **Ghost adapter**: `match` runs the demo's SQL (rename `exploration` → `explore`, wrap the row's definition as `skill`); `validate` rebuilds the evidence dict as `{"applied_inputs": subtask.parameters, "empty_state": report_context["empty_state"]}`, calls `verify`, and rewrites status to `inconclusive` when records are absent without an empty state; `compile` wraps `simulated_discovery` output in the envelope from `ghostapi/CONTRACTS.md` with `skill_id`, `version`, `status: candidate`, `site_id`, `operation`. Never call `run_task` or `qualify`.

## Questions for the owners

**Thomas.** Will `SteelBrowser` accept an external session id and skip release when it does not own it? Why does the UI-TARS backend hard-code `data: None`; is structured extraction planned? Are the `observation-000.png` refs in the committed example the intended naming? Should the Claude backend emit `metrics`? Which prose aborts map to which codes? Is he writing the moderator, and against `contracts.MODERATOR_DECISIONS`?

**Tianqi.** Can `operation` accept `search_products` or a name map? What should happen to free-text success conditions? Should `metrics` gain `browser_action_count`? For a continuing `needs_visual` handoff, will the page stay at the handoff URL with `close_on_finish: false`? Can the one-page rule coexist with the visual worker on the same session?

**Sting.** CAD or USD, and does the fixture ground truth change too? Will `validate` accept a `Subtask`, a list of evidence refs and the `report_context` keyword? When will `verify` emit `inconclusive`? Will `compile` return the envelope with `skill_id` and `status`? Does he agree promotion stays out of the per-run path?
