"""Unit: what a release actually ships.

The wheel is the dashboard, and the dashboard is templates, CSS and JavaScript as
much as it is Python: a wheel that builds cleanly and serves an unstyled page is a
broken release that every other check passes. The sdist is what a distro packager or
an auditor builds from. Both are published to PyPI forever, so what goes in them is a
decision rather than whatever happened to be in the working tree.
"""

import pathlib
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

import pytest

import matador

ROOT = pathlib.Path(__file__).resolve().parents[2]
# Not shipped: they describe how this repository is worked on, not how the package is
# built, installed or verified. (`.gitignore` is not on this list: hatchling ships it
# on purpose, because it is how the sdist reproduces its own file selection.)
NOT_SHIPPED = (".github", ".vscode", ".pre-commit-config.yaml")
# Shipped: the package, and enough to build it, restyle it and check it for yourself.
SHIPPED = ("matador", "tests", "docs", "styles", "pyproject.toml", "README.md", "LICENSE")


def _build(target: str, suffix: str) -> pathlib.Path:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv builds the distributions")
    out = pathlib.Path(tempfile.mkdtemp())
    # the command is a fixed list of literals and a resolved executable path
    subprocess.run(  # noqa: S603
        [uv, "build", target, "--out-dir", str(out)], cwd=ROOT, check=True, capture_output=True
    )
    return next(out.glob(f"*{suffix}"))


@pytest.fixture(scope="module")
def sdist_entries() -> set[str]:
    with tarfile.open(_build("--sdist", ".tar.gz")) as tar:
        # names are "<name>-<version>/<path>"; the first segment is the root
        return {name.split("/")[1] for name in tar.getnames() if "/" in name}


@pytest.fixture(scope="module")
def wheel() -> zipfile.ZipFile:
    return zipfile.ZipFile(_build("--wheel", ".whl"))


@pytest.mark.parametrize("entry", NOT_SHIPPED)
def test_the_sdist_leaves_the_workshop_behind(entry: str, sdist_entries: set[str]):
    assert entry not in sdist_entries


@pytest.mark.parametrize("entry", SHIPPED)
def test_the_sdist_carries_what_it_should(entry: str, sdist_entries: set[str]):
    assert entry in sdist_entries


def test_the_wheel_carries_the_stylesheet_it_serves(wheel: zipfile.ZipFile):
    """The CSS is built from `styles/input.css` by a tool that is not part of the
    install, so if it is missing from the wheel nobody can rebuild it downstream: the
    dashboard just renders unstyled."""
    assert "matador/static/app.css" in wheel.namelist()
    assert wheel.getinfo("matador/static/app.css").file_size > 0


@pytest.mark.parametrize("kind", ["templates", "static/js"])
def test_the_wheel_carries_what_it_renders(kind: str, wheel: zipfile.ZipFile):
    shipped = [name for name in wheel.namelist() if name.startswith(f"matador/{kind}/")]
    on_disk = [path for path in (ROOT / "matador" / kind).rglob("*") if path.is_file()]
    assert len(shipped) == len(on_disk), f"{len(on_disk) - len(shipped)} {kind} files missing"


def test_the_wheel_takes_its_version_from_the_module(wheel: zipfile.ZipFile):
    """The version is written down once, in `matador/__init__.py`, and the build
    backend derives the package's from it. This asserts that wiring against the real
    archive: the two used to agree only because they were edited in step by hand.
    """
    metadata = next(name for name in wheel.namelist() if name.endswith("METADATA"))
    assert f"Version: {matador.__version__}" in wheel.read(metadata).decode()


def test_the_wheel_depends_on_the_published_queue(wheel: zipfile.ZipFile):
    """`tool.uv.sources` points at a local checkout so the two repositories develop
    together. It is a development override, and a release that carried it would be
    uninstallable for everyone else."""
    metadata = next(name for name in wheel.namelist() if name.endswith("METADATA"))
    requirements = wheel.read(metadata).decode()
    assert "Requires-Dist: toro-queue>=" in requirements
    assert "../toro" not in requirements
