"""Shared test harness for the matador pyramid.

Layout: tests/unit (pure view/transform logic) · tests/integration (ASGI + Redis)
· tests/e2e (Playwright). Tests are auto-marked by folder, so `pytest -m unit` runs
the fast layer; integration/e2e skip cleanly when no Redis is reachable.
"""

from __future__ import annotations

import pytest

PREFIX = "matadortest"


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        if "/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/e2e/" in path:
            item.add_marker(pytest.mark.e2e)


_redis_up: bool | None = None


def _redis_reachable() -> bool:
    global _redis_up
    if _redis_up is None:
        try:
            import redis as _sync

            _sync.from_url("redis://localhost:6379").ping()
            _redis_up = True
        except Exception:
            _redis_up = False
    return _redis_up


@pytest.fixture(autouse=True)
def _require_redis(request):
    needs_redis = request.node.get_closest_marker("integration") or request.node.get_closest_marker(
        "e2e"
    )
    if needs_redis and not _redis_reachable():
        pytest.skip("needs a Redis on localhost:6379")
