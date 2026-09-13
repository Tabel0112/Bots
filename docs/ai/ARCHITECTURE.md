# Intended architecture

Status: intended product architecture. The DOM browser-worker slice is now implemented under [browser_worker/](../../browser_worker/README.md), with locally tested browser behavior; other components and connections are not established by that implementation. Decision status is maintained in [DECISIONS.md](DECISIONS.md).

## Product purpose

ARGUS is the deliberation, orchestration and verification brain. Ghost API is procedural memory: it converts successful browser interactions into reusable procedures with explicit inputs, checks, qualification evidence and versions. Learning here means recording, compiling and testing a procedure; model training is not required.

## User-described flow

```text
User task
  -> ARGUS interprets it and creates one overall request
  -> ARGUS breaks work into subtasks with dependencies
  -> subagents execute and report findings, evidence and failures
       -> browser subagents use Steel
       -> Ghost supplies compatible qualified procedures when available
  -> moderator monitors progress and collects worker reports
  -> moderator combines findings, reasons over gaps/conflicts,
     and coordinates independent validation
  -> final response returns to the user
```

Monitoring can occur while workers run; synthesis uses their observed outcomes. Independent subtasks may eventually run concurrently, while dependent subtasks need ordering. This does not imply that every request needs multiple simultaneous model workers.

The user explicitly described a moderator agent. Its responsibilities are part of the intended flow; whether it is a separately invoked model inside ARGUS or a role in ARGUS's controller is still open. One component should own overall run state, budgets and final publication so supervision cannot create competing controllers.

## Component boundaries

| Component | Responsibility |
| --- | --- |
| ARGUS planning | Interpret the task, define subtasks and dependencies, select bounded work |
| Browser subagents + Steel | Observe current pages, perform actions, extract structured records, record action/value origins and return evidence |
| Moderator | Monitor progress, reconcile reports, identify gaps/conflicts, coordinate verification and synthesize the user result |
| Ghost | Match compatible capabilities, compile traces, bind inputs strictly, validate, qualify, version, quarantine and accept repairs |
| Dashboard | Show the interpreted request, actual execution path, evidence, skill lifecycle and observed measurements |

These are software responsibilities. Current personnel assignments and cross-component handoffs are recorded in [TEAM.md](TEAM.md).

## Browser observation and vision

Steel is the selected browser infrastructure. A connected browser worker decides actions and interprets observations. Canvas may require screenshots and a vision-capable worker when page structure does not expose the needed content. The planner can remain primarily textual; the moderator needs access to visual interpretation only when its evidence requires it. One browser worker can support both observation methods. Sources and the proposed experiment live in [RESEARCH.md](../hackathon/RESEARCH.md).

## Ghost learning and reuse

1. For an unfamiliar request, explore through the browser worker and capture an actual trace.
2. Independently validate the task result. A successful trace may then produce a candidate skill.
3. Qualify with separate fresh-session replay cases, changed inputs and an evidenced empty-result case. A single successful exploration is insufficient.
4. On a compatible later request, strictly bind new values, execute the procedure against the current page, extract fresh results and validate again.
5. Detect supported UI failures, quarantine the affected version/scope, and attempt bounded repair. A repaired procedure becomes a new candidate version and needs requalification; preserve previous versions.
6. For unsupported changes, fail or use the explicitly permitted bounded fallback and explain what happened.

Task success, candidate creation and skill qualification are separate outcomes. A compiler failure must not erase a validated task result. Visual replay may still need model decisions, and old click coordinates alone are not a durable target description. No efficiency claim is established until total execution and qualification costs are measured.
