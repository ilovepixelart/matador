"""Unit: the Jinja filters + schedule label — pure formatting helpers."""

import re

from matador.app import _TEMPLATES, _schedule_label


def test_schedule_label_cron():
    assert _schedule_label({"cron": "0 0 * * *"}) == "cron 0 0 * * *"


def test_schedule_label_every_seconds():
    assert _schedule_label({"every": 30000}) == "every 30s"


def test_schedule_label_every_minutes():
    assert _schedule_label({"every": 120000}) == "every 2m"


def test_clock_filter():
    clock = _TEMPLATES.env.filters["clock"]
    assert clock(0) == "—"  # falsy → em dash, not "00:00:00"
    assert clock(None) == "—"
    assert re.fullmatch(r"\d\d:\d\d:\d\d", clock(1700000000000))


def test_comma_filter():
    comma = _TEMPLATES.env.filters["comma"]
    assert comma(1234567) == "1,234,567"
    assert comma(0) == "0"
