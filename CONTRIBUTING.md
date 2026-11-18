# Contributing

Thanks for helping improve matador, the live, server-rendered dashboard for
[toro](https://github.com/ilovepixelart/toro) queues. The [docs](docs/index.md)
cover the architecture, the views, the templates and the security model.

## Getting started

Requirements: Python 3.10 or newer, [uv](https://docs.astral.sh/uv/), a checkout
of toro next to this one, and, for the integration and e2e layers, a Redis
server that you own, named by the `REDIS_URL` environment variable.

During development matador depends on toro through the local path `../toro`
(the `[tool.uv.sources]` override in `pyproject.toml`; a released
matador-dashboard depends on `toro-queue` from PyPI instead):

```bash
git clone https://github.com/ilovepixelart/toro
git clone https://github.com/ilovepixelart/matador
cd matador
uv sync                                   # venv + deps + dev group, toro from ../toro
docker run -d --name matador-redis -p 127.0.0.1:6391:6379 redis:7-alpine   # a Redis for the tests
export REDIS_URL=redis://localhost:6391
uv run playwright install chromium        # once, for the e2e layer
```

Tests write under the `matadortest` key prefix, but use a Redis of its own for
them rather than one that holds real data. There is deliberately no default:
port 6379 on a development machine often belongs to another project, so the
suite never assumes it. To try the dashboard by hand (the dev scripts use a
Redis on `localhost:6379`):

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
uv run pytest -m wheel                     # the built wheel, installed (needs network)
```

The suite is marked by folder (`tests/conftest.py` applies the marker):

| Marker | Folder | Needs Redis |
| --- | --- | --- |
| `unit` | `tests/unit` | no |
| `integration` | `tests/integration` | yes |
| `e2e` | `tests/e2e` | yes, plus a Playwright browser |
| `wheel` | `tests/wheel` | yes, plus network access to PyPI |

The e2e layer is excluded from a plain `pytest` run and runs on its own, since
sync Playwright and pytest-asyncio cannot share one process. Integration, e2e
and wheel tests skip when `REDIS_URL` is unset ("set REDIS_URL to a Redis you
own") or when no Redis answers on it, so a green run without `REDIS_URL` has not
run them.

The wheel layer tests what a user installs rather than the source tree. It
builds the wheel with `uv build`, installs it into a fresh virtual environment
outside the repository with its dependencies from PyPI (toro-queue included, not
the sibling checkout), and runs `tests/wheel/scenarios.py` there: the dashboard is
started through the installed `matador` command and every route is called over
HTTP. It runs once with the newest toro-queue and once with the oldest one the
wheel accepts. It also runs the same scenarios against the release
`tests/wheel/allowlist.json` names and fails on any difference that file does not
list. A pull request that changes user-visible behavior adds an entry with its
number; a release moves `baseline` forward and empties the list.

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
