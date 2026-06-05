"""Seed toro queues with realistic, per-queue-themed datasets for matador.

    uv run python seed.py

Each queue gets its own job names, payloads and error messages, and a DIFFERENT
count in every state (all big enough to page through). A handful of jobs per queue
are processed for real (so they carry progress, logs and stack traces); the rest
are bulk-written straight to Redis for volume.
"""

import asyncio
import json
import random
import time

from toro import Queue, Worker

THEMES = {
    "emails": {
        "paused": False,
        "counts": {"completed": 64, "failed": 23, "wait": 41, "delayed": 9},
        "jobs": [
            "send-welcome",
            "send-receipt",
            "password-reset",
            "send-newsletter",
            "send-digest",
        ],
        "errors": [
            "SMTP 550: mailbox full",
            "recipient address bounced",
            "provider rate limit exceeded",
            "TLS handshake failed",
            "invalid sender domain",
        ],
        "data": lambda i: {
            "to": f"user{i}@example.com",
            "template": f"tmpl_{i % 6}",
            "locale": random.choice(["en", "de", "fr", "es"]),
            "opens": random.randint(0, 3),
        },
    },
    "billing": {
        "paused": False,
        "counts": {"completed": 138, "failed": 52, "wait": 27, "delayed": 6},
        "jobs": [
            "charge-card",
            "generate-invoice",
            "process-refund",
            "sync-subscription",
            "retry-dunning",
        ],
        "errors": [
            "card declined",
            "insufficient funds",
            "gateway timeout",
            "3-D Secure required",
            "expired card",
            "currency not supported",
        ],
        "data": lambda i: {
            "customer": f"cus_{1000 + i}",
            "amount_cents": random.randint(500, 49900),
            "currency": "usd",
            "card_last4": random.choice(["4242", "0005", "1881"]),
        },
    },
    "media": {
        "paused": False,
        "counts": {"completed": 95, "failed": 76, "wait": 58, "delayed": 13},
        "jobs": [
            "transcode-video",
            "resize-image",
            "generate-thumbnail",
            "extract-audio",
            "apply-watermark",
        ],
        "errors": [
            "unsupported codec",
            "ffmpeg exited 1",
            "source file corrupt",
            "out of memory",
            "resolution too high",
            "missing audio track",
        ],
        "data": lambda i: {
            "asset": f"asset_{i}.{random.choice(['mp4', 'mov', 'png'])}",
            "size_mb": random.randint(2, 940),
            "format": random.choice(["mp4", "webm", "webp"]),
        },
    },
    "notifications": {
        "paused": True,
        "counts": {"completed": 212, "failed": 31, "wait": 24, "delayed": 7},
        "jobs": ["send-push", "send-sms", "fire-webhook", "send-slack", "send-inapp"],
        "errors": [
            "device token expired",
            "carrier rejected message",
            "webhook returned 500",
            "user unsubscribed",
            "invalid phone number",
            "payload too large",
        ],
        "data": lambda i: {
            "user": f"u_{i}",
            "channel": random.choice(["ios", "android", "sms", "slack"]),
            "priority": random.choice(["low", "normal", "high"]),
        },
    },
}


async def _bulk(q: Queue, state: str, n: int, theme: dict) -> None:
    """Write n finished (display-only) jobs straight into a state, fast."""
    if n <= 0:
        return
    now = int(time.time() * 1000)
    end_id = await q.redis.incrby(q.keys.id, n)
    first = end_id - n + 1
    zkey = getattr(q.keys, state)
    pipe = q.redis.pipeline(transaction=False)
    for k in range(n):
        jid = first + k
        ts = now - (n - k) * random.randint(15_000, 90_000)
        m = {
            "id": jid,
            "name": random.choice(theme["jobs"]),
            "data": json.dumps(theme["data"](jid)),
            "opts": json.dumps({"attempts": 3}),
            "timestamp": ts,
            "attemptsMade": random.choice([1, 1, 1, 2, 3]),
            "state": state,
            "processedOn": ts + 40,
            "finishedOn": ts + random.randint(60, 1300),
        }
        if state == "completed":
            m["returnvalue"] = json.dumps({"ok": True, "ms": random.randint(15, 1100)})
            m["progress"] = "100"
        else:
            m["failedReason"] = random.choice(theme["errors"])
        pipe.hset(q.keys.job(jid), mapping=m)
        pipe.zadd(zkey, {str(jid): ts})
    await pipe.execute()


async def _process(job):
    await job.log(f"{job.name} #{job.id} started")
    await job.log(f"payload: {job.data}")
    if job.data.get("_fail"):
        await job.log("validation failed")
        raise RuntimeError(job.data.get("_error", "processing error"))
    for p in (25, 50, 75, 100):
        await job.update_progress(p)
        await job.log(f"progress {p}%")
        await asyncio.sleep(0.01)
    await job.log("finished ok")
    return {"ok": True, "duration_ms": random.randint(40, 900)}


async def seed_queue(name: str) -> dict:
    theme = THEMES[name]
    c = theme["counts"]
    q = Queue(name)
    for key in await q.redis.keys(q.keys.base + "*"):
        await q.redis.delete(key)

    # A few REAL jobs first, so the detail view has progress, logs and traces.
    for i in range(5):
        await q.add(random.choice(theme["jobs"]), theme["data"](i), remove_on_complete=False)
    for i in range(4):
        d = theme["data"](900 + i)
        d["_fail"], d["_error"] = True, random.choice(theme["errors"])
        await q.add(random.choice(theme["jobs"]), d, attempts=1, remove_on_fail=False)
    worker = Worker(name, _process, prefix="toro", concurrency=3, stalled_interval=0)
    task = asyncio.create_task(worker.run())
    for _ in range(400):
        cc = await q.counts()
        if cc["completed"] >= 5 and cc["failed"] >= 4:
            break
        await asyncio.sleep(0.05)
    await worker.stop()
    task.cancel()

    # Bulk-fill the rest for volume (pagination).
    await _bulk(q, "completed", c["completed"] - 5, theme)
    await _bulk(q, "failed", c["failed"] - 4, theme)

    # Waiting jobs at mixed priorities (worker is stopped, so they stay queued).
    for i in range(c["wait"]):
        await q.add(
            random.choice(theme["jobs"]),
            theme["data"](i),
            priority=random.choice([0, 0, 0, 0, 5, 10]),
        )
    for i in range(c["delayed"]):
        await q.add(random.choice(theme["jobs"]), theme["data"](i), delay=(i + 1) * 600_000)

    await q.add_scheduler(f"{name}-nightly", cron="0 0 * * *", name="nightly-rollup")
    await q.add_scheduler(f"{name}-poll", every=60_000, name="health-poll")
    if theme["paused"]:
        await q.pause()

    counts = await q.counts()
    await q.close()
    return counts


async def main():
    for name in THEMES:
        counts = await seed_queue(name)
        flag = " (paused)" if THEMES[name]["paused"] else ""
        print(f"  {name:<14}{flag:<10} {counts}")
    print("\nseeded. open:  uv run uvicorn run:app --port 8011")


if __name__ == "__main__":
    asyncio.run(main())
