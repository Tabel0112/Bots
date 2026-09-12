# Controlled catalog

Planning placeholder: no running website or dependencies are installed. Controlled-site ownership remains unassigned; see [team tasks](../docs/ai/TEAM.md) and [current context](../docs/ai/CURRENT.md).

Proposed scope: one read-only product search with query and maximum price. Agree exact currency, output limit and filter semantics before creating the truth set. Keep catalog data deterministic and document expected results for fixed changed-input and empty-result cases.

Plan v1 and v2 with the same semantic data and one deliberate supported control-target change. The old procedure should first fail observably on v2, then a semantically equivalent target can be proposed and the new candidate version qualified. Include one unsupported change state to test honest failure/fallback. No accounts or submissions.

An eventual integration needs URLs, start command, version switch, truth data, fixed inputs and expected outputs. See [evaluation scenarios](../docs/hackathon/EVALUATION.md). The controlled site requires its own learned skill; public-site skill transfer is not assumed.
