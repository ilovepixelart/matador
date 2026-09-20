"""Integration: the global concurrency cap in the dashboard - what the service
reports about it and what the metrics strip and the workers list render.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from html.parser import HTMLParser

import pytest
from toro import Queue, Worker

from matador.service import Service

from .conftest import PREFIX, QUEUE, hx


async def _noop(job):
    return None


@asynccontextmanager
async def live(q: Queue, *workers: Worker):
    """Run real workers until their presence records are up; stop them on exit."""
    tasks = [asyncio.create_task(w.run()) for w in workers]
    try:
        for _ in range(200):
            if len(await q.workers()) >= len(workers):
                break
            await asyncio.sleep(0.02)
        yield
    finally:
        for w in workers:
            await w.stop(grace_period=1)
        for t in tasks:
            t.cancel()


def worker(processor=_noop, **kw) -> Worker:
    return Worker(QUEUE, processor, prefix=PREFIX, stalled_interval=0, **kw)


@pytest.fixture
async def svc(q):
    return Service([QUEUE], url="redis://localhost:6379", prefix=PREFIX, connection=q.redis)


async def test_service_reports_cap_states(q, svc):
    """The cap is a worker option, so the queue's cap is whatever its live workers
    agree on: nothing, one value, or a disagreement worth flagging."""
    assert (await svc.metrics(QUEUE))["cap"] is None  # no workers at all

    async with live(q, worker()):
        assert (await svc.metrics(QUEUE))["cap"] is None  # a live worker, but no cap

    async with live(q, worker(global_concurrency=3), worker(global_concurrency=3)):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["state"], cap["limit"], cap["caps"]) == ("open", 3, [3])

    async with live(q, worker(global_concurrency=3), worker(global_concurrency=5)):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["state"], cap["limit"], cap["caps"]) == ("mixed", None, [3, 5])

    async with live(q, worker(global_concurrency=3), worker()):
        cap = (await svc.metrics(QUEUE))["cap"]
        assert (cap["state"], cap["limit"], cap["caps"]) == ("mixed", None, [0, 3])


class _CapChip(HTMLParser):
    """Pull the cap chip out of the strip: its attributes, its VISIBLE text, what
    it says to a screen reader, and every class inside it. Asserting on the whole
    response would be a false green - the latency chip can carry `text-warning` too."""

    def __init__(self) -> None:
        super().__init__()
        self.attrs: dict[str, str | None] | None = None
        self.classes: list[str] = []
        self._text: list[str] = []
        self._sr: list[str] = []
        self._depth = 0
        self._sr_depth = 0  # depth at which an sr-only element opened

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if self.attrs is None and "data-cap-state" in a:
            self.attrs = a
        elif not self._depth:
            return
        self._depth += 1
        classes = (a.get("class") or "").split()
        self.classes += classes
        if "sr-only" in classes and not self._sr_depth:
            self._sr_depth = self._depth

    def handle_endtag(self, tag):
        if self._depth:
            if self._depth == self._sr_depth:
                self._sr_depth = 0
            self._depth -= 1

    def handle_data(self, data):
        if self._depth:
            (self._sr if self._sr_depth else self._text).append(data)

    @property
    def text(self) -> str:
        return " ".join(" ".join(self._text).split())

    @property
    def sr_text(self) -> str:
        return " ".join(" ".join(self._sr).split())


async def cap_chip(client) -> _CapChip | None:
    r = await client.get(f"/queues/{QUEUE}/metrics", headers=hx())
    assert r.status_code == 200
    parser = _CapChip()
    parser.feed(r.text)
    return parser if parser.attrs is not None else None


@asynccontextmanager
async def holding(q: Queue, held: int, waiting: int, **worker_kw):
    """A live worker holding `held` jobs active, with `waiting` more queued."""
    release = asyncio.Event()

    async def block(job):
        await release.wait()

    for i in range(held + waiting):
        await q.add("job", {"i": i})
    w = worker(block, concurrency=held + waiting, **worker_kw)
    async with live(q, w):
        for _ in range(200):
            if (await q.counts())["active"] == held:
                break
            await asyncio.sleep(0.02)
        try:
            yield
        finally:
            release.set()


async def test_cap_chip_absent_without_a_cap(q, client):
    # "no cap" is non-data ink, like "0 failed": the chip must not render at all
    async with holding(q, held=1, waiting=0):
        assert await cap_chip(client) is None


async def test_cap_chip_shows_occupancy(q, client):
    async with holding(q, held=2, waiting=0, global_concurrency=3):
        chip = await cap_chip(client)
        assert chip is not None
        assert chip.attrs["data-cap-state"] == "open"
        assert chip.text == "cap 2/3"
        assert "text-warning" not in chip.classes and "text-danger" not in chip.classes


async def test_cap_chip_names_the_cap_as_the_wait(q, client):
    # 2 slots, both taken, 3 more queued: they wait on the cap, not on a worker
    async with holding(q, held=2, waiting=3, global_concurrency=2):
        chip = await cap_chip(client)
        assert chip is not None
        assert chip.attrs["data-cap-state"] == "full"
        assert chip.text == "at cap 2/2 · 3 waiting"
        assert "slot" in (chip.attrs["data-tip"] or "")
        assert chip.sr_text == chip.attrs["data-tip"]  # the tip reaches screen readers
        assert chip.attrs["tabindex"] == "0"  # and the keyboard
        # at the cap is intended behavior: color must keep meaning "a problem"
        assert "text-warning" not in chip.classes and "text-danger" not in chip.classes


async def test_cap_chip_warns_on_mixed_caps(q, client):
    async with live(q, worker(global_concurrency=3), worker(global_concurrency=5)):
        chip = await cap_chip(client)
        assert chip is not None
        assert chip.attrs["data-cap-state"] == "mixed"
        assert chip.text == "cap mixed 3, 5"
        assert "text-warning" in chip.classes

    async with live(q, worker(global_concurrency=3), worker()):
        chip = await cap_chip(client)
        assert chip is not None
        assert chip.text == "cap mixed none, 3"  # a worker with no cap is a disagreement too


async def test_workers_list_shows_the_cap(q, client):
    """A mixed fleet has to be traceable to the worker, so each row says its cap.
    A worker with none says nothing: "cap 0" would be ink with no data in it."""
    capped, plain = worker(global_concurrency=3), worker()
    async with live(q, capped, plain):
        r = await client.get("/workers/list", headers=hx())
        assert r.status_code == 200
        assert {w["id"] for w in await q.workers()} == {capped.token, plain.token}
        # both workers share a host and pid in-process, so count the markers
        assert r.text.count('data-worker-cap="3"') == 1
        assert r.text.count("data-worker-cap") == 1
