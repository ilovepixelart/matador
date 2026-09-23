"""Integration: the dashboard reads a shape it understands, or it says so.

toro stamps each queue with the data-model version that wrote it and refuses a queue
whose model is newer than the library understands. That check lives on the write
path, which is most of toro and almost none of matador: a dashboard mostly reads. So
a host running a dashboard a major version behind its workers renders whatever it
finds, field by field, and shows blanks where the shape moved. A wrong page is worse
than no page, because nobody doubts it.
"""

from __future__ import annotations

import pytest
from toro import DATA_MODEL_VERSION

from matador import create_app

from .conftest import PREFIX, QUEUE

pytestmark = pytest.mark.asyncio


class _Params(dict):
    """Fill a route template: the queue is the real one, anything else is a stand-in."""

    def __missing__(self, key: str) -> str:
        return QUEUE if key == "name" else "x"


def _queue_routes(app) -> list[str]:
    """Every GET route scoped to one queue, derived from the app itself, so a page
    added later is covered without being listed here."""
    out: list[str] = []

    def walk(routes) -> None:
        for route in routes:
            if type(route).__name__ == "Mount":  # /static is a sub-app, not our routes
                continue
            original = getattr(route, "original_router", None)
            nested = getattr(route, "routes", None) or getattr(original, "routes", None)
            if nested:
                walk(nested)
                continue
            if "GET" in getattr(route, "methods", set()) and "{name}" in route.path:
                out.append(route.path)

    walk(app.routes)
    return out


async def test_a_newer_data_model_is_refused_rather_than_rendered(q, seeded, client):
    """The upgrade order that causes this is the ordinary one: workers first.

    Every queue-scoped page, not just the one a person lands on: the table refreshes
    itself through a fragment of its own about once a second, which is where a wrong
    shape would actually be read.
    """
    await q.redis.hset(q.keys.meta, "model", str(DATA_MODEL_VERSION + 1))

    routes = _queue_routes(create_app([QUEUE], prefix=PREFIX))
    assert routes, "found no queue-scoped routes to check"
    for path in routes:
        page = await client.get(path.format_map(_Params()), headers={"HX-Request": "true"})
        assert page.status_code == 409, f"{path} rendered a shape it does not understand"
        assert "data model" in page.text.lower()


async def test_the_model_it_understands_renders_normally(q, seeded, client):
    """The guard reads one field; it must not cost the dashboard its own queue."""
    await q.redis.hset(q.keys.meta, "model", str(DATA_MODEL_VERSION))

    page = await client.get(f"/queues/{QUEUE}")

    assert page.status_code == 200
    assert "jobs-table" in page.text


async def test_opening_a_queue_does_not_stamp_it(q, seeded, client):
    """toro's own rule for this key: "a dashboard opening a queue must not create it
    by looking". Stamping is a claim about who wrote the data, and matador wrote none
    of it."""
    await q.redis.delete(q.keys.meta)

    page = await client.get(f"/queues/{QUEUE}")

    assert page.status_code == 200
    assert await q.redis.exists(q.keys.meta) == 0


async def test_a_stamp_it_cannot_read_is_not_a_newer_model(q, seeded, client):
    """Deliberate: a model field that is not a number says something wrote the key
    that is not toro. That is not evidence of a newer model, and locking an operator
    out of their own dashboard over it helps nobody. It reads as unstamped."""
    await q.redis.hset(q.keys.meta, "model", "banana")

    page = await client.get(f"/queues/{QUEUE}")

    assert page.status_code == 200
    assert "jobs-table" in page.text
