"""Unit: _pretty_json — server-side Pygments highlighting of job data/opts/result."""

import datetime

from markupsafe import Markup

from matador.app import _pretty_json


def test_highlights_each_token_type():
    html = str(_pretty_json({"to": "ada@example.com", "n": 42, "ok": True, "x": None}))
    assert 'class="nt"' in html  # object keys
    assert 'class="s2"' in html  # string values
    assert 'class="mi"' in html  # integers
    assert 'class="kc"' in html  # true / null
    assert "ada@example.com" in html  # the actual value survives


def test_returns_markup_so_jinja_does_not_escape_it():
    assert isinstance(_pretty_json({"a": 1}), Markup)


def test_falls_back_on_non_json_types_without_crashing():
    # default=str keeps it from blowing up on e.g. a date in the payload
    html = str(_pretty_json({"when": datetime.date(2026, 1, 1)}))
    assert "2026-01-01" in html
