"""Unit: matador's public API is one function (toro docs/specs/one-point-oh.md).

The dashboard's whole surface is `create_app` and its keyword options. Everything
else, including every module under `matador.`, is internal and may change in a patch
release: a host app that reached into one would break on an ordinary upgrade, and
this is the file that says so.
"""

import inspect
import pathlib

import pytest

import matador

PUBLIC = {"create_app"}

# Every option is part of the contract too: a host app configures the dashboard
# through these and nothing else.
OPTIONS = {
    "names",
    "url",
    "prefix",
    "connection",
    "dependencies",
    "require_same_origin",
    "show_stacktraces",
    "can_mutate",
}

DOCS = pathlib.Path(__file__).resolve().parents[2] / "docs"
README = DOCS.parent / "README.md"


def _documentation() -> str:
    pages = [page.read_text() for page in DOCS.glob("*.md")]
    return "\n".join([*pages, README.read_text()])


def test_the_public_api_is_the_one_that_was_frozen():
    assert set(matador.__all__) == PUBLIC


def test_the_options_are_the_ones_that_were_frozen():
    """An option added without being added here is a promise nobody made; one removed
    is a promise somebody broke."""
    assert set(inspect.signature(matador.create_app).parameters) == OPTIONS


@pytest.mark.parametrize("option", sorted(OPTIONS))
def test_every_option_is_documented(option):
    assert option in _documentation(), f"{option} configures the dashboard and is on no page"
