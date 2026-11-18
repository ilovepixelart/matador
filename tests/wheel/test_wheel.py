"""Wheel: the built package, installed into a fresh environment, run as a user runs it.

The other layers import the source tree beside a toro checkout, so a packaging mistake
(a template or asset left out of the wheel, a dependency the metadata forgot, a broken
`matador` command) passes them all. Here `uv build` makes the wheel, a fresh virtual
environment outside the repository installs it with its dependencies from PyPI, and
tests/wheel/scenarios.py runs in that environment: it starts the dashboard through the
installed `matador` command and calls every route against a real toro queue on the
Redis REDIS_URL names. It runs twice, once with the toro-queue the resolver picks and
once with the oldest toro-queue the wheel's metadata accepts.

The same scenarios then run against the release `allowlist.json` names as its baseline,
installed from PyPI, and every observation has to match, except the changes the
allowlist records with the pull request that made each one.

    REDIS_URL=redis://localhost:6391 uv run pytest -m wheel

`MATADOR_E2E_CANDIDATE` replaces the built wheel with another artifact (a wheel path or
a requirement such as `matador-dashboard==1.0.6`), which is how the suite is shown to
fail.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import redis

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
SCENARIOS = HERE / "scenarios.py"
ALLOWLIST = json.loads((HERE / "allowlist.json").read_text())
BASELINE = f"matador-dashboard=={ALLOWLIST['baseline']}"
CANDIDATE = os.environ.get("MATADOR_E2E_CANDIDATE", "")


def _scenario_names() -> list[str]:
    spec = importlib.util.spec_from_file_location("matador_wheel_scenarios", SCENARIOS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # registered first: its dataclasses resolve their annotations through sys.modules
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return list(module.SCENARIOS)


NAMES = _scenario_names()


def _uv() -> str:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv builds the wheel and the environments")
    return uv


def _pip(python: Path, workdir: Path, requirement: str) -> None:
    subprocess.run(
        [_uv(), "pip", "install", "--quiet", "--python", str(python), requirement],
        check=True,
        cwd=workdir,
    )


def _install(workdir: Path, label: str, requirement: str) -> Path:
    """A fresh environment outside the repository holding `requirement` and the
    dependencies its metadata names, from PyPI, nothing else."""
    venv = workdir / label
    subprocess.run(
        [_uv(), "venv", "--quiet", "--python", sys.executable, str(venv)], check=True, cwd=workdir
    )
    python = venv / "bin" / "python"
    _pip(python, workdir, requirement)
    return python


def _run(python: Path, workdir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # -I and a working directory outside the repository: `import matador` can only be
    # the installed one, never the source tree.
    return subprocess.run(
        [str(python), "-I", str(SCENARIOS), *args],
        cwd=workdir,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )


def _scenarios(workdir: Path, label: str, requirement: str, *, toro_floor: bool) -> dict[str, Any]:
    python = _install(workdir, label, requirement)
    if toro_floor:
        floor = _run(python, workdir, "toro-floor")
        assert floor.returncode == 0, floor.stderr
        _pip(python, workdir, f"toro-queue=={floor.stdout.strip()}")
    out = workdir / f"{label}.json"
    proc = _run(python, workdir, "--out", str(out))
    assert proc.returncode == 0, f"the scenario runner itself failed:\n{proc.stderr}"
    shutil.rmtree(workdir / label)
    return json.loads(out.read_text())


@pytest.fixture(scope="session")
def workdir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    # Session fixtures are built before conftest's per-test Redis check runs, and a
    # runner with no Redis would fail every scenario rather than skip them.
    try:
        redis.Redis.from_url(os.environ.get("REDIS_URL", "")).ping()
    except redis.RedisError:
        pytest.skip("no Redis answers on REDIS_URL")
    return tmp_path_factory.mktemp("wheel")


@pytest.fixture(scope="session")
def artifact(workdir: Path) -> str:
    if CANDIDATE:
        return CANDIDATE
    dist = workdir / "dist"
    subprocess.run(
        [_uv(), "build", "--wheel", "--out-dir", str(dist)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    return str(next(dist.glob("*.whl")))


@pytest.fixture(scope="session")
def candidate(workdir: Path, artifact: str) -> dict[str, Any]:
    return _scenarios(workdir, "candidate", artifact, toro_floor=False)


@pytest.fixture(scope="session")
def on_toro_floor(workdir: Path, artifact: str) -> dict[str, Any]:
    return _scenarios(workdir, "toro-floor", artifact, toro_floor=True)


@pytest.fixture(scope="session")
def published(workdir: Path) -> dict[str, Any]:
    return _scenarios(workdir, "published", BASELINE, toro_floor=False)


def _outcome(result: dict[str, Any]) -> dict[str, Any]:
    """What a run observed, key by key; a scenario that failed is one value."""
    if not result["ok"]:
        return {"error": result["error"]}
    return result["observed"]


@pytest.mark.parametrize("name", NAMES)
def test_the_installed_wheel(name: str, candidate: dict[str, Any]):
    result = candidate[name]
    assert result["ok"], f"toro {candidate['_toro']}: {result['error']}\n{result['trace']}"


@pytest.mark.parametrize("name", NAMES)
def test_the_installed_wheel_on_the_oldest_toro_it_accepts(
    name: str, on_toro_floor: dict[str, Any], candidate: dict[str, Any]
):
    result = on_toro_floor[name]
    assert result["ok"], f"toro {on_toro_floor['_toro']}: {result['error']}\n{result['trace']}"
    assert _outcome(result) == _outcome(candidate[name]), (
        f"toro {on_toro_floor['_toro']} and toro {candidate['_toro']} disagree"
    )


@pytest.mark.parametrize("name", NAMES)
def test_it_behaves_as_the_published_release(
    name: str, candidate: dict[str, Any], published: dict[str, Any]
):
    ours, theirs = _outcome(candidate[name]), _outcome(published[name])
    listed = {c["key"]: c for c in ALLOWLIST["changes"] if c["scenario"] == name}
    unlisted = {}
    for key in sorted(ours.keys() | theirs.keys()):
        was, now = theirs.get(key), ours.get(key)
        change = listed.get(key)
        if was != now and (
            change is None or (change["published"], change["candidate"]) != (was, now)
        ):
            unlisted[key] = {ALLOWLIST["baseline"]: was, "candidate": now}
    assert unlisted == {}, (
        f"differs from {BASELINE} and is not in tests/wheel/allowlist.json:\n"
        + json.dumps(unlisted, indent=2)
    )


@pytest.mark.parametrize(
    "change", ALLOWLIST["changes"], ids=lambda c: f"{c['scenario']}.{c['key']}"
)
def test_every_listed_change_is_still_the_difference(
    change: dict[str, Any], candidate: dict[str, Any], published: dict[str, Any]
):
    """An entry nobody can observe any more is a hole in the differential: it would let
    the same difference through again later, unexplained."""
    ours = _outcome(candidate[change["scenario"]]).get(change["key"])
    theirs = _outcome(published[change["scenario"]]).get(change["key"])
    assert (theirs, ours) == (change["published"], change["candidate"]), (
        f"#{change['pr']} ({change['change']}) is no longer what differs: "
        "update the entry, or drop it once the baseline release includes it"
    )
