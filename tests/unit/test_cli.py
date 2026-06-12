"""Unit: the `matador serve` CLI - arg parsing and wiring to uvicorn (no real server)."""

from unittest.mock import patch

import pytest

from matador import cli


def test_serve_wires_create_app_and_uvicorn():
    with (
        patch.object(cli, "create_app") as create_app,
        patch.object(cli.uvicorn, "run") as run,
    ):
        cli.main(
            [
                "serve",
                "--queues",
                "emails, billing , ",  # whitespace + trailing empties are trimmed
                "--redis",
                "redis://r:6379",
                "--prefix",
                "p",
                "--host",
                "10.0.0.1",
                "--port",
                "1234",
            ]
        )
    create_app.assert_called_once_with(["emails", "billing"], url="redis://r:6379", prefix="p")
    run.assert_called_once_with(create_app.return_value, host="10.0.0.1", port=1234)


def test_serve_defaults():
    with (
        patch.object(cli, "create_app") as create_app,
        patch.object(cli.uvicorn, "run") as run,
    ):
        cli.main(["serve", "--queues", "q"])
    create_app.assert_called_once_with(["q"], url="redis://localhost:6379", prefix="toro")
    run.assert_called_once_with(create_app.return_value, host="127.0.0.1", port=8000)


def test_queues_is_required():
    with pytest.raises(SystemExit):
        cli.main(["serve"])


def test_subcommand_is_required():
    with pytest.raises(SystemExit):
        cli.main([])
