"""Service layer: the dashboard's view of toro, decoupled from HTTP/templates.

Holds one Queue per configured name and exposes exactly the reads and actions
the UI needs. Keeping this separate from app.py keeps routes thin and testable.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from redis.asyncio import Redis
from redis.asyncio.client import PubSub
from toro import Job, JobState, Queue

STATES: tuple[JobState, ...] = ("active", "wait", "delayed", "completed", "failed")


def _human_bytes(n: float | None) -> str:
    if not n or n < 0:
        return "—"
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024:
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.2f}{unit}"
        n /= 1024
    return f"{n:.2f}P"


class UnknownQueueError(KeyError):
    """A request named a queue the dashboard isn't configured to watch.

    Kept as a domain error so the service stays HTTP-agnostic; app.py maps it to a 404.
    """


class Service:
    """The dashboard's read/action API over a fixed set of toro queues."""

    def __init__(
        self, names: list[str], *, url: str, prefix: str, connection: Redis | None = None
    ) -> None:
        # If the host hands us its own redis client, share it (and never close it);
        # otherwise each queue opens — and owns — its own connection.
        self._owns_connection = connection is None
        self.queues: dict[str, Queue] = {
            name: Queue(name, connection=connection, url=url, prefix=prefix) for name in names
        }
        # SSE plumbing: ONE pubsub subscription per Service, fanned out to every
        # connected stream via per-client events (the same shared-listener shape
        # as toro's result() dispatcher). N open dashboard tabs cost one Redis
        # connection, not N — open tabs can't starve the action routes' pool.
        self._listeners: set[asyncio.Event] = set()
        self._broadcast_pubsub: PubSub | None = None
        self._broadcast_task: asyncio.Task[None] | None = None
        self._broadcast_lock = asyncio.Lock()

    def _q(self, name: str) -> Queue:
        q = self.queues.get(name)
        if q is None:
            raise UnknownQueueError(name)
        return q

    async def redis_stats(self) -> dict[str, Any]:
        """Live Redis health for the top bar. Surfaces the memory + eviction-policy
        footguns that actually bite queue users.
        """
        r = next(iter(self.queues.values())).redis
        try:
            info = await r.info()
            keys = await r.dbsize()
        except Exception:
            # the bar polls every ~8s; a transient blip must degrade, not 500 repeatedly.
            # Empty info flows through the .get() defaults below to "?"/0 placeholders.
            info, keys, ok = {}, 0, False
        else:
            ok = True
        try:
            policy = (await r.config_get("maxmemory-policy")).get("maxmemory-policy", "")
        except Exception:  # some managed Redis block CONFIG GET
            policy = ""
        used = info.get("used_memory", 0)
        maxmem = info.get("maxmemory", 0)
        total_system = info.get("total_system_memory", 0)
        # Headroom: against maxmemory if configured, else the host's total RAM.
        limit = maxmem or total_system
        available = (limit - used) if limit else None
        return {
            "version": info.get("redis_version", "?"),
            "used_human": info.get("used_memory_human", "?"),
            "maxmemory": maxmem,
            "max_human": info.get("maxmemory_human") or "∞",
            "mem_pct": round(used / maxmem * 100) if maxmem else None,
            "available_human": _human_bytes(available),
            "available_pct": round(available / limit * 100) if (available and limit) else None,
            "limit_basis": "limit" if maxmem else "host RAM",
            "limit_human": _human_bytes(limit) if limit else "∞",
            "policy": policy or "?",
            "policy_ok": policy in ("noeviction", ""),  # noeviction is the safe one
            "clients": info.get("connected_clients", 0),
            "ops": info.get("instantaneous_ops_per_sec", 0),
            "keys": keys,
            "ok": ok,  # False → Redis was unreachable; values are placeholders
        }

    async def overview(self) -> list[dict[str, Any]]:
        out = []
        for name, q in self.queues.items():
            out.append(
                {
                    "name": name,
                    "counts": await q.counts(),
                    "paused": await q.is_paused(),
                }
            )
        return out

    async def workers(self) -> list[dict[str, Any]]:
        """Every live worker across all queues (each record carries its `queue`)."""
        out: list[dict[str, Any]] = []
        for name, q in self.queues.items():
            for w in await q.workers():
                w["queue"] = name
                out.append(w)
        out.sort(key=lambda w: (w["queue"], w["started"]))
        return out

    async def departed_workers(self, limit: int = 15) -> list[dict[str, Any]]:
        """Recent worker departures across all queues, newest first (the death-log)."""
        out: list[dict[str, Any]] = []
        for name, q in self.queues.items():
            for d in await q.departed_workers(limit=limit):
                d["queue"] = name
                out.append(d)
        out.sort(key=lambda d: d["at"], reverse=True)
        return out[:limit]

    async def clear_departed(self) -> int:
        """Clear the stopped/lost-worker history across all queues. Returns the count."""
        total = 0
        for q in self.queues.values():
            total += await q.clear_departed()
        return total

    async def queue_view(self, name: str) -> dict[str, Any]:
        q = self._q(name)
        return {
            "name": name,
            "counts": await q.counts(),
            "paused": await q.is_paused(),
            "schedulers": await q.schedulers(),
        }

    async def jobs(
        self, name: str, state: JobState, page: int = 1, per_page: int = 20
    ) -> list[dict[str, Any]]:
        start = (page - 1) * per_page
        jobs = await self._q(name).get_jobs(state, start, start + per_page - 1)
        return [{**self._summary(j), "queue": name} for j in jobs]

    async def search(
        self, name: str, state: JobState, query: str, scan_limit: int = 500
    ) -> list[dict[str, Any]]:
        jobs = await self._q(name).search(state, query, scan_limit)
        return [{**self._summary(j), "queue": name} for j in jobs]

    async def job(self, name: str, job_id: str) -> dict[str, Any] | None:
        q = self._q(name)
        j = await q.get_job(job_id)
        if not j:
            return None
        detail = self._detail(j)
        detail["logs"] = await q.get_logs(job_id)
        detail["queue"] = name  # jobs carry their queue (needed for cross-queue views)
        return detail

    async def schedulers(self, name: str) -> list[dict[str, Any]]:
        return await self._q(name).schedulers()

    # ---- actions ----------------------------------------------------------

    async def retry(self, name: str, job_id: str) -> bool:
        return await self._q(name).retry_job(job_id)

    async def remove(self, name: str, job_id: str) -> bool:
        return await self._q(name).remove_job(job_id)

    async def remove_many(self, name: str, job_ids: list[str]) -> int:
        """Remove a specific set of jobs (multi-select bulk delete). Returns how
        many were ACTUALLY removed — a job that vanished in a race doesn't count.
        """
        q = self._q(name)
        removed = 0
        for job_id in job_ids:
            removed += 1 if await q.remove_job(job_id) else 0
        return removed

    async def pause(self, name: str) -> None:
        await self._q(name).pause()

    async def resume(self, name: str) -> None:
        await self._q(name).resume()

    async def promote(self, name: str, job_id: str) -> bool:
        return await self._q(name).promote_job(job_id)

    async def retry_all(self, name: str) -> int:
        return await self._q(name).retry_all_failed()

    async def clean(self, name: str, state: JobState) -> int:
        """Remove every job in a state. The underlying toro call is bounded (1000 per
        call) so it can't block forever; loop in batches so the dashboard's Clean
        actually drains the state rather than nibbling 1000 off a large backlog.
        """
        q = self._q(name)
        total = 0
        while True:
            n = await q.clean(state, limit=1000)
            total += n
            if n < 1000 or total >= 100_000:  # drained, or a sane upper bound
                break
        return total

    async def remove_scheduler(self, name: str, scheduler_id: str) -> None:
        await self._q(name).remove_scheduler(scheduler_id)

    async def trigger_scheduler(self, name: str, scheduler_id: str) -> None:
        await self._q(name).trigger_scheduler(scheduler_id)

    async def _ensure_broadcaster(self) -> None:
        """Start the shared events listener (or restart it after a crash)."""
        if self._broadcast_task is not None and not self._broadcast_task.done():
            return
        async with self._broadcast_lock:
            if self._broadcast_task is not None and not self._broadcast_task.done():
                return  # someone else won the race while we awaited the lock
            if self._broadcast_pubsub is not None:  # a dead broadcaster's leftovers
                with contextlib.suppress(Exception):
                    await self._broadcast_pubsub.aclose()
            pubsub = next(iter(self.queues.values())).redis.pubsub()
            await pubsub.subscribe(*[q.keys.events for q in self.queues.values()])
            self._broadcast_pubsub = pubsub
            self._broadcast_task = asyncio.create_task(self._broadcast(pubsub))

    async def _broadcast(self, pubsub: PubSub) -> None:
        """Consume the shared subscription; wake every connected stream per event."""
        try:
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=None)
                if msg is not None:
                    for ev in self._listeners:
                        ev.set()
        except asyncio.CancelledError:
            raise
        except Exception:
            # The subscription died (Redis restart, network): wake every client so
            # its stream ends promptly; browsers reconnect on the retry hint and
            # the next stream restarts the broadcaster.
            for ev in self._listeners:
                ev.set()

    async def event_stream(
        self, is_disconnected: Callable[[], Awaitable[bool]] | None = None
    ) -> AsyncIterator[str]:
        """SSE stream: emit a COALESCED `changed` signal whenever any queue publishes
        a job event. toro publishes one event per job (completed/failed/progress), so
        under load that's a firehose; we emit on the first event, then let whatever
        lands in the next ~200ms ride the same repaint — a thousand finishes cost one
        refresh (~5 `changed`/s ceiling). The same signal doubles as the heartbeat
        after 8 quiet seconds. All streams share ONE pubsub via the broadcaster.
        """
        try:
            await self._ensure_broadcaster()
        except Exception:
            # Redis is unreachable right now: hand the client its reconnect cadence
            # and end cleanly — it retries until the broadcaster can subscribe again.
            yield "retry: 3000\n\n"
            return
        ev = asyncio.Event()
        self._listeners.add(ev)
        try:
            yield "retry: 3000\n\n"
            while True:
                # Exit promptly when the client goes away instead of waiting for the
                # next yield to raise — drops our listener registration right away.
                if is_disconnected is not None and await is_disconnected():
                    break
                if self._broadcast_task is None or self._broadcast_task.done():
                    break  # subscription died: end the stream, the client reconnects
                with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                    await asyncio.wait_for(ev.wait(), timeout=8.0)
                yield "event: changed\ndata: 1\n\n"  # signal, or heartbeat on timeout
                if ev.is_set():
                    await asyncio.sleep(0.2)  # the burst rides this repaint
                    ev.clear()
        finally:
            self._listeners.discard(ev)

    async def close(self) -> None:
        if self._broadcast_task is not None:
            self._broadcast_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._broadcast_task
            self._broadcast_task = None
        if self._broadcast_pubsub is not None:
            with contextlib.suppress(Exception):
                await self._broadcast_pubsub.aclose()
            self._broadcast_pubsub = None
        # Only close connections matador opened; a shared host client is theirs to manage.
        if self._owns_connection:
            for q in self.queues.values():
                await q.close()

    # ---- shaping ----------------------------------------------------------

    @staticmethod
    def _summary(j: Job) -> dict[str, Any]:
        return {
            "id": j.id,
            "name": j.name,
            "state": j.state,
            "attempts_made": j.attempts_made,
            "data": j.data,
            "failed_reason": j.failed_reason,
            "progress": j.progress,
        }

    @classmethod
    def _detail(cls, j: Job) -> dict[str, Any]:
        return {
            **cls._summary(j),
            "opts": j.opts.to_dict(),
            "returnvalue": j.returnvalue,
            "timestamp": j.timestamp,
            "processed_on": j.processed_on,
            "finished_on": j.finished_on,
            "stacktrace": j.stacktrace,
        }
