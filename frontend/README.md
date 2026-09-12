# Dashboard

Planning placeholder: no frontend dependencies or UI implementation are installed. Dashboard ownership remains unassigned; see [team tasks](../docs/ai/TEAM.md) and [current context](../docs/ai/CURRENT.md).

Relevant references: [provisional contract 0.1 and JSON examples](../docs/hackathon/CONTRACTS.md), and [project context](../docs/ai/README.md). The generated fixtures and fake API described in CURRENT belong to an unpublished local experiment.

Proposed experience: a request form, ordered event timeline, interpreted request, results/evidence, validation summary, metrics, and skill/version/status panel. Keep the API client separate from presentation. Show sample versus live mode explicitly. Poll run status on an event-stream disconnect; deduplicate by run ID/sequence and render one terminal state. Show unavailable metrics honestly and defer the Stop button until cancellation exists end to end.

Frontend tooling and moderator/subtask display still need design decisions. Acceptance and presentation references live in [docs/demo](../docs/demo).
