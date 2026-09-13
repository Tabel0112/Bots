# Controlled catalog

Planning placeholder: no running website or dependencies are installed. Controlled-site ownership remains unassigned; see [team tasks](../docs/ai/TEAM.md) and [current context](../docs/ai/CURRENT.md).

Proposed scope: one read-only product search with query and maximum price. Agree exact currency, output limit and filter semantics before creating the truth set. Keep catalog data deterministic and document expected results for fixed changed-input and empty-result cases.

Plan v1 and v2 with the same semantic data and one deliberate supported control-target change. The old procedure should first fail observably on v2, then a semantically equivalent target can be proposed and the new candidate version qualified. Include one unsupported change state to test honest failure/fallback. No accounts or submissions.

An eventual integration needs URLs, start command, version switch, truth data, fixed inputs and expected outputs. See [evaluation scenarios](../docs/hackathon/EVALUATION.md). The controlled site requires its own learned skill; public-site skill transfer is not assumed.

## Hosted controlled catalog (2026-09-13)

The controlled catalog `Agents/browser_worker/demo/catalog.html` is published
unchanged from the public repository `Tabel0112/Bots-Hosting` at
`https://tabel0112.github.io/Bots-Hosting/catalog.html` (the path is
case-sensitive; the lowercase spelling lands on an unrelated 404 page).

`sites.hosted.json` in this folder is the worker site config for that address:
same selectors, controls, schemas and checks as the bundled `demo-catalog`, with
`start_url`, `allowed_domains` and `allowed_url_patterns` pointing at the host.
Product links on the page are root-relative (`/products/<id>`), so on GitHub Pages
they resolve to `https://tabel0112.github.io/products/<id>`; the patterns allow
that. Use it with `WORKER_SITES_FILE=demo-site/sites.hosted.json` and
`WORKER_BROWSER=steel`.

Status: the config validates with the worker's own `validate_request`. The first
Steel run on 2026-09-13 failed before navigation with HTTP 401 from Steel
("Invalid Steel API key"); the key in the local `.env` must be replaced before the
Steel path can be verified. No two-UI-version site exists yet.
