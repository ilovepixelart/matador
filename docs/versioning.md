# Versioning

## What is public

**`matador.create_app` and its keyword options, and nothing else.**

```python
from matador import create_app     # the whole contract
```

Every module under `matador.` is internal: the service layer, the templates, the
Jinja filters, the route names. They change in patch releases, and a host app that
reaches into one is holding a private object. What the dashboard shows comes from
toro's public API, so anything you want programmatically is there
([toro's versioning](https://github.com/ilovepixelart/toro/blob/main/docs/versioning.md)).

The HTML is not an API either. The markup, the ids and the htmx wiring are how the
pages work today; scraping them is scraping a UI.

## Semver

| Change | Version |
|---|---|
| A new option with a default, a new page | minor |
| An option removed, a default changed, a page removed | major |
| A fix, a template change, a dependency bump | patch |

A default that changes is a major change even when the new default is safer: 0.11.0
turning the same-origin guard on is exactly that, and it is why the upgrading notes
exist.

## toro

matador is a dashboard for toro and tracks its data model through the dependency:
the floor in `pyproject.toml` is the oldest toro whose keys this version reads
correctly. Run them a minor apart if you must, but the floor is what is tested.

The other direction is checked at runtime. Workers are upgraded before dashboards,
so a queue can be written by a toro newer than the one matador reads it with: each
queue carries the data-model version that wrote it, and opening a queue stamped
newer than `toro.DATA_MODEL_VERSION` gives a 409 naming both numbers instead of a
page rendered from a shape this version does not know. Opening a queue never stamps
one: matador wrote none of the data.
