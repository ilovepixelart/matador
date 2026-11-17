# Contributing

Thanks for helping improve matador, the live, server-rendered dashboard for
[toro](https://github.com/ilovepixelart/toro) queues. The [docs](docs/index.md)
cover the architecture, the views, the templates and the security model.

## Getting started

Requirements: Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), a checkout
of toro next to this one, and, for the integration and e2e layers, a Redis
server reachable on `localhost:6379`.

During development matador depends on toro through the local path `../toro`
(the `[tool.uv.sources]` override in `pyproject.toml`; a released
matador-dashboard depends on `toro-queue` from PyPI instead):

```bash
git clone https://github.com/ilovepixelart/toro
git clone https://github.com/ilovepixelart/matador
cd matador
uv sync                                   # venv + deps + dev group, toro from ../toro
docker run -d --name matador-redis -p 6379:6379 redis:7-alpine   # a Redis for the tests
uv run playwright install chromium        # once, for the e2e layer
```

Tests write under the `matadortest` key prefix, but use a Redis of its own for
them rather than one that holds real data. To try the dashboard by hand:

```bash
uv run python scripts/seed.py             # optional: populate demo data
uv run uvicorn scripts.run:app --reload   # http://localhost:8000
```

## Checks

The toolchain is Astral's throughout: uv for the environment, ruff for lint and
format, ty for types. CI runs these on every pull request, on every supported
Python:

```bash
uv run ruff check .                        # lint (strict)
uv run ruff format --check .               # format (drop --check to apply)
uv run ty check                            # type check
uv run pytest                              # unit + integration (the default)
uv run pytest -m e2e                       # Playwright browser tests, run separately
```

The suite is marked by folder (`tests/conftest.py` applies the marker):

| Marker | Folder | Needs Redis |
| --- | --- | --- |
| `unit` | `tests/unit` | no |
| `integration` | `tests/integration` | yes |
| `e2e` | `tests/e2e` | yes, plus a Playwright browser |

The e2e layer is excluded from a plain `pytest` run and runs on its own, since
sync Playwright and pytest-asyncio cannot share one process. Integration and
e2e tests skip when no Redis answers on `localhost:6379`, so a green run
without Redis has not run them.

## Styling

The CSS is built with the standalone
[Tailwind CLI](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.3)
(no Node or npm). Download the binary for your platform as `./tailwindcss`
(it is git-ignored) and rebuild after changing templates or `styles/`:

```bash
./tailwindcss -i styles/input.css -o matador/static/app.css --watch
```

Commit the rebuilt `matador/static/app.css`: CI rebuilds it and fails when the
committed file is stale.

## Making a change

- Open an issue first for anything beyond a small fix, so the approach can be
  agreed before the work.
- Keep each pull request to one focused change, with tests that fail without it.
- The public API is `create_app` and its keyword options
  ([docs/versioning.md](docs/versioning.md)). A change that breaks it needs a
  major release; say so in the pull request, and add the entry to
  [docs/upgrading.md](docs/upgrading.md).
- A change that needs a matching toro change goes in a toro branch with the
  same name: CI checks out that branch as the sibling dependency when it exists.

## Reporting bugs and security issues

Open an issue for a bug or a feature idea. For anything security-sensitive, do
not open a public issue: report it privately through
[GitHub Security Advisories](https://github.com/ilovepixelart/matador/security/advisories/new),
as described in [SECURITY.md](SECURITY.md).

By contributing you agree to the [Code of Conduct](CODE_OF_CONDUCT.md) and that
your work is licensed under the project's [MIT license](LICENSE).
