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
(`sidebar_oob.html`, `tab_counts_oob.html`) that let one response update
several regions ([Live updates](live-updates.md)).

## Macros

`macros.html` holds the pieces used everywhere:

| Macro | Renders |
|---|---|
| `icon(name)` | An inline-SVG Heroicon (outline set, self-hosted - no icon font, no CDN). |
| `job_row(name, j, state)` | One job row: a native `<details>` accordion with checkbox, id chip, data preview, progress bar, attempts, and the state-appropriate action buttons. The body lazy-loads via `hx-get` on first open. |
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
| `dur` | `850ms`, `45.2s`, `1h 23m` |
| `uptime` | `3h 45m` from a started-at timestamp |
| `comma` / `compact` | `1,234` / `12.3K`, `5.2M` |
| `schedule` | `every 5s` or `cron */5 * * * *` |
| `pretty` | Pygments-highlighted JSON, truncated at 20k chars ([Security](security.md)) |

## Styling: Tailwind, standalone

The CSS is built by the **standalone Tailwind CLI** - no Node, no npm:

```bash
./tailwindcss -i styles/input.css -o matador/static/app.css --watch
```

`styles/input.css` defines the design tokens as CSS custom properties - panel,
line, ink, and the status colors (`--info`, `--success`, `--warning`,
`--danger`, `--accent`) - with a light and a dark set; dark mode is
`darkMode: 'class'`, toggled by a small behavior and remembered in
localStorage. Component classes (`.btn*`, `.card`, `.chip`, `.input`, `.th`,
`.td`, `.pg*`) live in `@layer components`, and the status utilities built from
tokens are safelisted since they're composed in macros, out of the content
scanner's sight.

The built `app.css` is committed to `matador/static/` (a pip install needs no
build step) and served with a cache-busting `?v=` derived from the file's
mtime, so a redeploy can't pin a stale stylesheet.
