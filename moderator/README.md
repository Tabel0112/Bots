# ARGUS moderator

`moderator` implements the structured judgement boundary used by the ARGUS
controller. It assesses worker reports, reconciles accepted records, selects
validated record fields for the controller to render, and optionally observes
progress. It does not dispatch workers, own sessions or budgets, write run state,
or produce user-facing prose.

## Entry point

Construct `moderator.Moderator` and inject it into the controller:

```python
from argus.controller import Controller
from moderator import Moderator

controller = Controller(toolbox, Moderator(None), ghost, store)
```

`Moderator(None)` is fully deterministic and makes no model calls. To enable
bounded structured judgements, pass an `argus.model_client.ModelClient`, normally
an `OpenAICompatibleClient(model=os.environ["ARGUS_MODEL"])`. The constructor also
accepts `max_tokens=1200` and `stall_seconds=120.0`.

## Environment

- `ARGUS_MODEL`: model selected when the connected runtime constructs an
  `OpenAICompatibleClient`; there is no default.
- `OPENAI_API_KEY`: required by the OpenAI-compatible client only when a live
  client is passed.

The deterministic path reads neither variable. `MODERATOR_MODEL` is no longer
supported.

## Inputs and outputs

An evidenced successful report is accepted deterministically:

```json
{
  "stage": "assess",
  "decision": "accept",
  "reason": "The succeeded report carries run evidence.",
  "evidence_refs": ["observation-000.png"],
  "next_action": null
}
```

A successful report without a screenshot or controller verification requests a
read-only verification:

```json
{
  "stage": "assess",
  "decision": "verify",
  "reason": "The succeeded report has no screenshot or controller verification.",
  "evidence_refs": [],
  "next_action": {
    "question": "Does the page show results for 'headphones', or an explicit empty state?",
    "success_conditions": ["results present or explicit empty state"]
  }
}
```

Synthesis returns selection data only. `record_indices` refer to the original
validated list; claim indices refer to the selected order:

```json
{
  "record_indices": [1, 0],
  "claims": [
    {"record_index": 0, "fields": ["title", "price", "url"]},
    {"record_index": 1, "fields": ["title", "price", "url"]}
  ],
  "notes": []
}
```

Model refusals, transport errors, truncation, and invalid assessment output never
degrade to acceptance. Invalid synthesis output returns the deterministic
selection with a typed `synthesis_fallback` note. Raw provider error, refusal, and
unparsed response text is never copied into a decision, event, or answer.

## Tests

From the repository root, using an environment with the project dependencies:

```bash
python -m unittest discover -s argus/tests -p 'test_*.py'
python -m ruff check argus moderator
python -m ruff format --check moderator argus/tests/test_moderator_module.py
```

Dependencies are Python 3.11+, Pydantic 2, and the local `argus` package. The
`openai` package is needed only by `OpenAICompatibleClient`; unit tests use
`FakeModelClient` and make no network calls.

## Known limitations

- Live moderator model calls have not been tested; offline tests cover the same
  structured client boundary.
- Progress snapshots currently expose limited per-subtask timing. The observer
  handles the current events and richer `elapsed_seconds`, `max_seconds`, and
  `seconds_since_last_action` fields when supplied.
- Reconciliation deterministically keeps the later record for a shared URL. A
  model may tag a conflict but may not invent or alter a record.

The old standalone harness was deleted. It spawned visual workers, tailed their
traces, killed subprocesses, emitted events, and synthesized prose—all work now
owned by the ARGUS controller and toolbox. `python -m moderator --harness` reports
that removal instead of entering an obsolete product path.
