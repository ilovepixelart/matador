# Dev scripts

Local helpers for running and populating the dashboard against a Redis on
`localhost:6379`. Not part of the published package.

| Script | What it does |
|---|---|
| [`seed.py`](seed.py) | Populates several queues with realistic, per-queue demo data — a different count in every state (enough to page through), with a handful of jobs processed for real so they carry progress, logs, and stack traces. |
| [`run.py`](run.py) | Serves the dashboard standalone over those queues. |

```bash
uv run python scripts/seed.py             # optional: populate demo data
uv run uvicorn scripts.run:app --reload   # http://localhost:8000
```
