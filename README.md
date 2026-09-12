# ARGUS + Ghost API

Project for the Battle of the Schools Web Agents hackathon. ARGUS plans tasks, coordinates subagents and verifies their results. Ghost API turns successful browser interactions into reusable, parameterized capabilities. Browser subagents will use Steel.

**Current phase: planning and research.** This shared checkpoint contains project documentation and AI handoff instructions. A provisional synthetic backend was built locally and is not included in this documentation commit; see [CURRENT.md](docs/ai/CURRENT.md) for its limitations and historical validation.

## Start here

- [AI/project context](docs/ai/README.md): short reading guide for teammates and coding assistants.
- [Team tasks and project tracking](docs/ai/TEAM.md): Sting — Ghost API; Thomas — visual interpretation; Tianqi — HTML/code interpretation; Abel — ARGUS.
- [Architecture](docs/ai/ARCHITECTURE.md): request → subtasks → browser workers → moderator → final result, plus Ghost's lifecycle.
- [Ghost API module](ghostapi/README.md): workflow structure, development guide, interactive flowchart demo, tests, and Codecov setup.
- [Decisions](docs/ai/DECISIONS.md): confirmed choices and unresolved questions.
- [Research](docs/hackathon/RESEARCH.md): Steel, canvas, vision and the proposed feasibility experiment.
- [Provisional contract](docs/hackathon/CONTRACTS.md) and [evaluation checklist](docs/hackathon/EVALUATION.md): component examples and the evidence the MVP must produce.

## Coding-agent handoffs

Root [CLAUDE.md](CLAUDE.md) imports [AGENTS.md](AGENTS.md), which directs agents to the same project context. Start the coding tool from this repository and give it a scoped task. Other AI tools can start with `docs/ai/README.md`.

Each coding task should leave current module usage instructions, a concise change/test record in TEAM, and an explicit next handoff. Keep project-wide progress in CURRENT and decisions in DECISIONS. Teammates receive changes to these files when they pull the shared repository; updates are not synchronized between computers until published through Git.

## Scope

One read-only search/filter/extraction workflow, one public website and one controlled website with two UI versions. Demonstrate actual exploration, trace compilation, fresh qualification, changed-input reuse, independent validation and bounded repair with honest failure handling.

The [frontend](frontend/README.md), [controlled site](demo-site/README.md) and [demo materials](docs/demo/README.md) directories currently hold planning placeholders. These areas still need owners. Public-site choice, agent framework/model and several shared interface decisions remain open. There is no integrated live application or installed project-wide dependency set in this checkpoint. The Ghost API module includes a standard-library fixture demo for its local workflow lifecycle and interactive viewer.
