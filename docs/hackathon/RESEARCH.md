# ARGUS + Ghost API — research notes

Updated: 2026-09-12. Status: planning and research; browser feasibility experiments have not been run.

This note holds source-backed Steel/vision findings and the proposed feasibility experiment. The product flow lives in [ARCHITECTURE.md](../ai/ARCHITECTURE.md), current choices in [DECISIONS.md](../ai/DECISIONS.md), and provisional message shapes in [CONTRACTS.md](CONTRACTS.md). Research findings do not silently change the executable contract.

## Steel's role

**Team direction:** browser subagents will use Steel.

Steel provides managed browser sessions and tools for obtaining page content and interacting with pages. Its single-page scrape endpoint returns formats such as HTML and Markdown. Multi-step search/filter/click workflows use browser sessions. These facilities reduce the browser infrastructure the team must build. [Steel browser tools](https://docs.steel.dev/overview/browser-tools/overview), [Steel sessions](https://docs.steel.dev/overview/sessions-api/overview).

Our system still owns deciding which actions to perform, extracting the required structured records, recording parameter origins, collecting evidence, and checking whether the result meets the request. For example, obtaining a catalog page does not by itself establish that the requested price filter took effect or that extracted prices are correct.

## Canvas and visual interaction

A canvas can render controls, text and graphics as pixels without exposing the drawn objects as ordinary semantic HTML. HTML or accessibility-based extraction may therefore miss meaningful content. Some sites supply accessible backing markup or alternative content, so the presence of canvas alone does not establish that vision is required. [MDN canvas reference](https://developer.mozilla.org/en-US/docs/Web/HTML/Reference/Elements/canvas).

Steel documents a computer-use integration in which a model receives screenshots, chooses actions, and sends those actions back to Steel. Steel supplies the browser observation and action mechanisms; the connected model supplies visual interpretation. [Steel computer-use integration](https://docs.steel.dev/integrations/claude-computer-use).

```text
Steel screenshot
  -> vision-capable browser subagent interprets the current screen
  -> subagent chooses a click, typing or scrolling action
  -> Steel executes the action
  -> fresh screenshot / page observation
  -> verify the expected effect or report failure
```

This establishes a documented integration route, not verified reliability on our chosen website. Screenshot availability does not guarantee accurate reading, target selection, or task completion.

## Which agents need vision?

| Component | Planned requirement |
| --- | --- |
| Browser subagent | Needs vision capability and browser-action tools to support workflows whose necessary content or controls are available only visually |
| ARGUS planner | Can primarily operate on the textual task, operation definitions and worker reports |
| Moderator | Can aggregate structured reports; needs vision capability or access to a vision-capable worker when verification requires interpreting screenshot evidence |
| Ghost | Stores procedures and qualification evidence; a visual procedure can invoke the browser worker's visual interaction capability during replay |

A browser subagent can support both page-structure and screenshot-based interaction. We do not need a separate vision-only agent simply to support both observation methods. No model or agent framework has been selected by this discussion.

## Implications for Ghost

**Planning recommendation:** use page structure and semantic targets where adequate, with vision as a bounded fallback when necessary.

- Record the intended action, semantic target, parameter source, before/after evidence, and expected outcome.
- For a visual step, locate the target against the current screen during replay. Historical coordinates can describe an observed action but should not be treated as a durable locator on their own.
- A visual replay may still require model reasoning. Do not promise that every learned procedure becomes model-free or report savings without measurements.
- Keep result validation independent from procedure generation. An apparently successful click is not sufficient evidence of a correct extracted result.
- If visual targets remain ambiguous or a change is unsupported, stop or use the agreed bounded fallback and report the limitation.

The current executable contract emphasizes semantic targets. Research whether visual target descriptions, screenshot references and coordinate context require an explicit contract extension before implementing visual skill compilation. No such extension is approved or implemented by this note.

## Small feasibility experiment — visual interpretation

Relevant workstreams: Thomas's VLM work and Tianqi's HTML/code interpretation. Coordinate the evidence format with Sting and Abel; see [team tasks](../ai/TEAM.md). The experiment remains proposed and has not been run.

Research one representative canvas control through the selected Steel integration. The experiment should establish:

1. Whether the relevant content/control already has usable page structure or accessible markup.
2. Whether a screenshot exposes it clearly enough for the selected vision-capable browser worker.
3. Whether the worker can perform one scoped interaction and verify its effect from a fresh observation.
4. Whether the integration exposes enough action and observation detail to record a useful Ghost trace.
5. Whether a repeated attempt can locate the target again rather than depending on the first attempt's coordinates.
6. Whether failure, finite budgets and browser-session cleanup can be handled explicitly.

Deliver a short finding with the tested page, library/model versions, exact scenario, observed outcomes, safe evidence, limitations and recommendation. **Status: not run.**

General canvas support is not currently required for the first read-only search/filter/extraction MVP. Keep the main demo bounded to one public site and one controlled site with two UI versions. Use this experiment to decide whether vision is necessary for the selected workflow.

## Open research questions

| Question | Relevant component |
| --- | --- |
| Which browser-agent framework and vision-capable model integrate cleanly with Steel and expose action traces? | Browser integration |
| Does the selected public workflow actually need vision? | Browser execution |
| What screenshot/action evidence is sufficient for visual replay and independent validation? | Browser + Ghost + verification |
| What contract additions, if any, are required for visual targets? | Shared contracts |
| How will the dashboard distinguish page-structure execution, visual fallback, and unavailable measurements? | Dashboard + events |

No browser experiments, model calls, dependency installations or live-site acceptance checks were performed for this research note. Source documentation was reviewed during the planning discussion; implementation details and compatibility must be checked when choosing the actual integration.
