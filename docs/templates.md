# Templates

Server-rendered Jinja, organized by what each file is *for* in a hypermedia app:

```
matador/templates/
├── layouts/base.html      the HTML skeleton: assets, SSE connection, htmx config
├── pages/                 full documents that extend the layout (index, error)
├── partials/              the HTMX swap fragments (one per live region)
└── macros.html            reusable components and the icon set
```

The split mirrors the [one route, two responses](architecture.md) rule: `pages/`
is what a direct visit renders, `partials/` is what an HTMX swap renders, and a
route picks between them with `wants_fragment`.

## Partials are the live regions

Each partial corresponds to a swappable region, so the names read as a map of
the UI: `queue.html` (the panel), `jobs.html` (the table + pager),
`job_detail.html` (the accordion body), `job_page.html`, `search_results.html`,
`workers.html` / `workers_list.html`, `schedulers.html`, `sidebar.html`,
`redis.html`, `toast.html` - plus the out-of-band wrappers
(`sidebar_oob.html`, `tab_counts_oob.html`, `job_state_oob.html`,
`announce_oob.html`) that let one response update several regions ([Live updates](live-updates.md)).

## Macros

`macros.html` holds the pieces used everywhere:

| Macro | Renders |
|---|---|
| `icon(name)` | An inline-SVG Heroicon (outline set, self-hosted - no icon font, no CDN). |
| `job_row(name, j, state, page, bulk)` | One job row: a native `<details>` accordion with checkbox (bulk states only), id chip, flow glyphs (branch + child count on parents, turn-arrow on children), data preview, progress bar (active jobs), attempts, and the state-appropriate action buttons. The body loads via `hx-get` each time the row is opened. |
| `flow_tree(queue, nodes, current_id, cls)` | A flow tree (recursive): emits its own `<ul cls>` over a list of sibling nodes, each an `<li>` with id chip, status pill, name, duration, failure reason and a per-node retry button on failed children; the current job's row is lifted. Recurses on each node's children. |
| `job_chip(queue, jid)` | A clickable job-id chip linking to the standalone job page. |
| `state_token(s)` / `state_color(s)` | Map a job state to a semantic token (`info`/`success`/`danger`/`warning`/`muted`) and its badge classes. |
| `empty_state(icon, message, ...)` | The centered "nothing here" block for empty lists. |
| `pglink(p, label, ...)` | A pagination link with `hx-push-url`. |

## Filters

Formatting lives in Jinja filters registered by the app, so templates never do
math:

| Filter | Example |
|---|---|
| `clock` / `clockms` | `12:34:56` / `12:34:56.789` (local time) |
| `dur` | `850ms`, `45.2s`, `83m 20s` |
| `uptime` | `3h 45m` from a started-at timestamp |
| `comma` / `compact` | `1,234` / `12K`, `5.2M` (below 10,000 unchanged) |
| `schedule` | `every 5s` or `cron */5 * * * *` |
| `pretty` | Pygments-highlighted JSON, truncated at 20k chars ([Security](security.md)) |

## Styling: Tailwind, standalone

The CSS is built by the **standalone Tailwind CLI** - no Node, no npm:

```bash
./tailwindcss -i styles/input.css -o matador/static/app.css --watch
```

`styles/input.css` defines the design tokens as CSS custom properties - panel,
line, ink, and the status colors (`--info`, `--success`, `--warning`,
`--danger`, `--accent`) - with a light and a dark set; dark mode is a
class-strategy `@custom-variant dark`, toggled by a small behavior and remembered
in localStorage (Tailwind v4, configured in CSS; there is no `tailwind.config.js`). Component classes (`.btn*`, `.card`, `.chip`, `.input`, `.th`,
`.td`, `.pg*`) live in `@layer components`, and the status utilities built from
tokens are pinned with `@source inline(...)`, since they're composed in macros,
out of the content scanner's sight.

The built `app.css` is committed to `matador/static/` (a pip install needs no
build step) and served with a cache-busting `?v=`: the newest mtime of any file under `static/`,
shared by every stylesheet and script tag (the favicon link carries none), so a redeploy
can't pin a stale stylesheet or script.
