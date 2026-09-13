# Agents

This package contains the repository's browser agents under one namespace:

- `Agents.browser_worker` — DOM-first HTML worker with GPT/Steel support, bounded actions,
  independent verification, Ghost memory, and optional visual handoff.
- `Agents.visual.browser_subagent` — screenshot-driven UI-TARS/Steel worker used for
  visual continuation and standalone visual experiments.

The namespace migration is breaking: use `Agents.*` imports and module commands. The
Ghost API remains a separate service package.

## Entry points

```sh
python -m Agents.browser_worker.ghost_cli --demo
python -m Agents.browser_worker.demo.steel_sanity --use-environment
python -m Agents.visual.browser_subagent "describe the page" --url https://example.com
```

See [the DOM worker README](browser_worker/README.md) and [the visual worker README](visual/README.md)
for setup, environment variables, input/output examples, tests, and limitations.
