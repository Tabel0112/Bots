# ARGUS + Ghost API — team working pack

Updated: 2026-09-12. Planning baseline: 24 productive hours, four people.

ARGUS is the deliberation, orchestration, and verification brain. Ghost API is the procedural-memory/tooling layer that compiles successful browser interactions into reusable, parameterized, self-validating capabilities.

These documents describe intended implementation. They do not claim the code exists or that checks have passed. Initial status for every workstream is **not started / not yet reported**.

## Start here

1. Read the [shared plan](PLAN.md) together and assign names to A–D.
2. Person C owns the [interface contract](CONTRACTS.md); agree on version 0.1 before independent coding.
3. Each person opens their work log and updates its status, next action, and branch.
4. Use the [evaluation and demo checklist](EVALUATION.md) as the shared definition of completion.

| Person | Work log | Main responsibility |
| --- | --- | --- |
| A — browser engineer | [Person A](people/A-browser.md) | Explore websites, capture actions, replay steps, manage browser sessions |
| B — Ghost engineer | [Person B](people/B-ghost.md) | Compile skills, match requests, bind inputs, validate, repair |
| C — integration lead | [Person C](people/C-argus.md) | Shared contracts, ARGUS controller, API, storage, integration |
| D — interface and demo builder | [Person D](people/D-experience.md) | Dashboard, controlled demo site, manual acceptance checks, pitch/video |

## How to keep this useful

- Each person edits their own log after a meaningful checkpoint and before handing work off; a short update roughly every two hours is enough.
- Keep the current-status table accurate; append important handoffs and decisions below it.
- Explain actual code in the implementation section once it exists. Link files and name entry points so a teammate can debug your component.
- Record checks actually run, their result, and the tested revision. Use “not run” where appropriate.
- A blocker must name the missing input, its owner, and what can continue meanwhile.
- C owns shared plan/contract changes. D owns the evaluation checklist. Other teammates propose changes through their own logs or a short team discussion.
- Do not copy personal AI-tool preferences into team or repository instructions.

## Placement and publication

This pack can live at `docs/hackathon/` in `Tabel0112/Bots`; relative links will keep working. It is currently a local deliverable because the earlier checkout was absent and restoring it failed authentication. No remote publication is implied by these files. The Git integration step remains pending.

The earlier full project specification remains the broader architecture reference. This pack narrows the implementation for the hackathon; it does not replace that long-term vision.
