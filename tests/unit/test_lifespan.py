"""Unit: the app lifespan closes its Service even when shutdown is exceptional."""

import pytest

from matador.app import create_app
from matador.service import Service


async def test_lifespan_closes_service_when_the_app_exits_with_an_error(monkeypatch):
    closed: list[Service] = []

    async def record_close(self: Service) -> None:
        closed.append(self)

    monkeypatch.setattr(Service, "close", record_close)
    app = create_app(["emails"])

    with pytest.raises(RuntimeError, match="boom"):
        async with app.router.lifespan_context(app):
            raise RuntimeError("boom")

    assert len(closed) == 1
