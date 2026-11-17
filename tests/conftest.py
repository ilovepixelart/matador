"""Shared test harness for the matador pyramid.

Layout: tests/unit (pure view/transform logic) · tests/integration (ASGI + Redis)
· tests/e2e (Playwright). Tests are auto-marked by folder, so `pytest -m unit` runs
the fast layer. Integration/e2e use the Redis named by the REDIS_URL environment
variable and skip when it is unset; there is no default, since port 6379 on a
developer machine may belong to another project and these tests write to it. They
also skip when that Redis does not answer.
"""

from __future__ import annotations

import os

import pytest
from toro import Queue, Worker

PREFIX = "matadortest"
# The one place the suite learns which Redis to write to: opt-in, never defaulted.
REDIS_URL = os.environ.get("REDIS_URL", "")
_REDIS_LAYERS = ("integration", "e2e")
_NO_REDIS_URL = pytest.mark.skip(
    reason="set REDIS_URL to a Redis you own, e.g. redis://localhost:6391"
)


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        if "/unit/" in path:
            item.add_marker(pytest.mark.unit)
        elif "/integration/" in path:
            item.add_marker(pytest.mark.integration)
        elif "/e2e/" in path:
            item.add_marker(pytest.mark.e2e)
        # Skipped before setup, so no fixture of a Redis layer ever builds a client.
        if not REDIS_URL and any(item.get_closest_marker(layer) for layer in _REDIS_LAYERS):
            item.add_marker(_NO_REDIS_URL)


_redis_up: bool | None = None


def _redis_reachable() -> bool:
    global _redis_up
    if _redis_up is None:
        try:
            import redis as _sync

            _sync.from_url(REDIS_URL).ping()
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
        pytest.skip(f"no Redis answers on REDIS_URL ({REDIS_URL})")


@pytest.fixture(scope="session", autouse=True)
def _default_to_redis_url():
    """A toro Queue or Worker built without `url` would reach the library default,
    localhost:6379, whatever REDIS_URL says: every test's default is REDIS_URL.

    A MonkeyPatch of its own, not a test's: a test that calls
    `monkeypatch.undo()` must not put the library default back.
    """
    with pytest.MonkeyPatch.context() as patch:
        if REDIS_URL:
            for cls in (Queue, Worker):
                patch.setattr(cls, "__init__", _with_url(cls.__init__))
        yield


def _with_url(init):
    def with_url(self, *args, **kw):
        kw.setdefault("url", REDIS_URL)
        init(self, *args, **kw)

    return with_url
