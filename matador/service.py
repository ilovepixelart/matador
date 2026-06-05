"""Service layer: the dashboard's view of toro, decoupled from HTTP/templates.

Holds one Queue per configured name and exposes exactly the reads and actions
the UI needs. Keeping this separate from app.py keeps routes thin and testable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from toro import Queue

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from redis.asyncio import Redis
    from toro import Job

STATES = ["active", "wait", "delayed", "completed", "failed"]


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

    def _q(self, name: str) -> Queue:
        q = self.queues.get(name)
        if q is None:
            raise UnknownQueueError(name)
        return q

    async def redis_stats(self) -> dict:
        """Live Redis health for the top bar. Surfaces the memory + eviction-policy
        footguns that actually bite queue users.
        """
        r = next(iter(self.queues.values())).redis
        info = await r.info()
        keys = await r.dbsize()
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
        }

    async def overview(self) -> list[dict]:
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

    async def workers(self) -> list[dict]:
        """Every live worker across all queues (each record carries its `queue`)."""
        out: list[dict] = []
        for name, q in self.queues.items():
            for w in await q.workers():
                w["queue"] = name
                out.append(w)
        out.sort(key=lambda w: (w["queue"], w["started"]))
        return out

    async def departed_workers(self, limit: int = 15) -> list[dict]:
        """Recent worker departures across all queues, newest first (the death-log)."""
        out: list[dict] = []
        for name, q in self.queues.items():
            for d in await q.departed_workers(limit=limit):
                d["queue"] = name
                out.append(d)
        out.sort(key=lambda d: d["at"], reverse=True)
        return out[:limit]

    async def queue_view(self, name: str) -> dict:
        q = self._q(name)
        return {
            "name": name,
            "counts": await q.counts(),
            "paused": await q.is_paused(),
            "schedulers": await q.schedulers(),
        }

    async def jobs(self, name: str, state: str, page: int = 1, per_page: int = 20) -> list[dict]:
        start = (page - 1) * per_page
        jobs = await self._q(name).get_jobs(state, start, start + per_page - 1)
        return [self._summary(j) for j in jobs]

    async def search(self, name: str, state: str, query: str, scan_limit: int = 500) -> list[dict]:
        jobs = await self._q(name).search(state, query, scan_limit)
        return [self._summary(j) for j in jobs]

    async def job(self, name: str, job_id: str) -> dict | None:
        q = self._q(name)
        j = await q.get_job(job_id)
        if not j:
            return None
        detail = self._detail(j)
        detail["logs"] = await q.get_logs(job_id)
        return detail

    async def schedulers(self, name: str) -> list[dict]:
        return await self._q(name).schedulers()

    # ---- actions ----------------------------------------------------------

    async def retry(self, name: str, job_id: str) -> bool:
        return await self._q(name).retry_job(job_id)

    async def remove(self, name: str, job_id: str) -> bool:
        return await self._q(name).remove_job(job_id)

    async def remove_many(self, name: str, job_ids: list[str]) -> int:
        """Remove a specific set of jobs (multi-select bulk delete). Returns the count."""
        q = self._q(name)
        for job_id in job_ids:
            await q.remove_job(job_id)
        return len(job_ids)

    async def pause(self, name: str) -> None:
        await self._q(name).pause()

    async def resume(self, name: str) -> None:
        await self._q(name).resume()

    async def promote(self, name: str, job_id: str) -> bool:
        return await self._q(name).promote_job(job_id)

    async def retry_all(self, name: str) -> int:
        return await self._q(name).retry_all_failed()

    async def clean(self, name: str, state: str) -> int:
        return await self._q(name).clean(state)

    async def remove_scheduler(self, name: str, scheduler_id: str) -> None:
        await self._q(name).remove_scheduler(scheduler_id)

    async def trigger_scheduler(self, name: str, scheduler_id: str) -> None:
        await self._q(name).trigger_scheduler(scheduler_id)

    async def event_stream(self) -> AsyncIterator[str]:
        """SSE stream: emit a COALESCED `changed` signal whenever any queue publishes
        a job event. toro publishes one event per job (completed/failed/progress), so
        under load that's a firehose; we emit on the first event, then drain the burst
        for ~200ms before emitting again — so a thousand finishes cost one repaint
        (~5 `changed`/s ceiling), not hundreds. An 8s heartbeat covers quiet changes.
        """
        loop = asyncio.get_running_loop()
        r = next(iter(self.queues.values())).redis
        pubsub = r.pubsub()
        await pubsub.subscribe(*[q.keys.events for q in self.queues.values()])
        try:
            yield "retry: 3000\n\n"
            while True:
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=8.0)
                yield "event: changed\ndata: 1\n\n"
                if msg is not None:
                    # Coalesce the rest of the burst: consume + discard whatever lands
                    # in the next 200ms so the client repaints once, and the pub/sub
                    # buffer can't grow unbounded under high throughput.
                    deadline = loop.time() + 0.2
                    while (remaining := deadline - loop.time()) > 0:
                        drained = await pubsub.get_message(
                            ignore_subscribe_messages=True, timeout=remaining
                        )
                        if drained is None:
                            break
        finally:
            await pubsub.aclose()

    async def close(self) -> None:
        # Only close connections matador opened; a shared host client is theirs to manage.
        if self._owns_connection:
            for q in self.queues.values():
                await q.close()

    # ---- shaping ----------------------------------------------------------

    @staticmethod
    def _summary(j: Job) -> dict:
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
    def _detail(cls, j: Job) -> dict:
        return {
            **cls._summary(j),
            "opts": j.opts.to_dict(),
            "returnvalue": j.returnvalue,
            "timestamp": j.timestamp,
            "processed_on": j.processed_on,
            "finished_on": j.finished_on,
            "stacktrace": j.stacktrace,
        }
