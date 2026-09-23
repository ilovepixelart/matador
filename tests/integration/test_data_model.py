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

from .conftest import QUEUE

pytestmark = pytest.mark.asyncio


async def test_a_newer_data_model_is_refused_rather_than_rendered(q, seeded, client):
    """The upgrade order that causes this is the ordinary one: workers first."""
    await q.redis.hset(q.keys.meta, "model", str(DATA_MODEL_VERSION + 1))

    page = await client.get(f"/queues/{QUEUE}")

    assert page.status_code == 409
    assert "data model" in page.text.lower()
    assert "jobs-table" not in page.text, "rendered a shape it does not understand"


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
