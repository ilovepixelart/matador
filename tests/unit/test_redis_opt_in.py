"""The Redis layers run only against the Redis REDIS_URL names, never a default.

Port 6379 on a development machine may belong to another project, and these tests
write to it. Run as real pytest sessions in a subprocess over a copy of the shared
conftest, so what is proved is the wiring itself. The only Redis URL used here is a
closed port: nothing in this file reaches a server.
"""

import os
import pathlib
import subprocess
import sys

import pytest

CONFTEST = pathlib.Path(__file__).resolve().parents[1] / "conftest.py"
CLOSED = "redis://127.0.0.1:1"


def _session(tmp_path, url: str | None, folder: str, body: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "REDIS_URL"}
    if url is not None:
        env["REDIS_URL"] = url
    (tmp_path / "conftest.py").write_text(CONFTEST.read_text())
    (tmp_path / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nmarkers = ["unit: x", "integration: x", "e2e: x"]\n'
    )
    (tmp_path / folder).mkdir()
    (tmp_path / folder / "test_inner.py").write_text(body)
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-rs", str(tmp_path)],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env=env,
        timeout=60,
        check=False,
    )


@pytest.mark.parametrize("folder", ["integration", "e2e"])
def test_a_redis_layer_skips_when_redis_url_is_unset(tmp_path, folder):
    run = _session(tmp_path, None, folder, "def test_x():\n    raise AssertionError\n")

    assert "1 skipped" in run.stdout, run.stdout
    assert "set REDIS_URL to a Redis you own" in run.stdout


def test_reachability_is_checked_on_redis_url(tmp_path):
    run = _session(tmp_path, CLOSED, "integration", "def test_x():\n    raise AssertionError\n")

    assert "1 skipped" in run.stdout, run.stdout
    assert f"no Redis answers on REDIS_URL ({CLOSED})" in run.stdout


@pytest.mark.parametrize("undo", ["", "    monkeypatch.undo()\n"], ids=["plain", "after-undo"])
def test_a_queue_and_worker_without_url_use_redis_url(tmp_path, undo):
    """Also after the test's own `monkeypatch.undo()`, which reverts everything
    the test's monkeypatch set, autouse fixtures' patches included."""
    body = (
        "from toro import Queue, Worker\n\n"
        "def test_x(monkeypatch):\n"
        f"{undo}"
        "    for client in (Queue('q').redis, Worker('q', print).redis):\n"
        "        kw = client.connection_pool.connection_kwargs\n"
        "        assert (kw['host'], kw['port']) == ('127.0.0.1', 1)\n"
    )

    run = _session(tmp_path, CLOSED, "unit", body)

    assert "1 passed" in run.stdout, run.stdout
