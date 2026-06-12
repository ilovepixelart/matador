"""Unit: _selected_from_url - which sidebar entry the HX-Current-URL points at.

Parsed with rfind rather than a greedy regex, so a long client-controlled header
can't trigger O(n^2) backtracking (ReDoS).
"""

import time

import pytest

from matador.app import WORKERS_SEL, _selected_from_url


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://x/matador/queues/emails?state=active", "emails"),
        ("/matador/queues/emails", "emails"),
        ("/queues/billing#frag", "billing"),
        ("/admin/queues/queues/reports", "reports"),  # sub-path mount: last wins
        ("/matador/queues/my%20queue", "my queue"),  # url-decoded
        ("/matador/workers", WORKERS_SEL),
        ("/matador/workers?x=1", WORKERS_SEL),
        ("/matador/queues/", None),  # empty name
        ("/matador/", None),
        ("", None),
    ],
)
def test_selected_from_url(url, expected):
    assert _selected_from_url(url) == expected


def test_selected_from_url_is_linear_on_pathological_input():
    # The old greedy `.*/queues/...` ran in O(n^2) on a long no-match input; the
    # rfind parse is linear, so this finishes effectively instantly.
    evil = "/" * 200_000
    start = time.perf_counter()
    assert _selected_from_url(evil) is None
    assert time.perf_counter() - start < 1.0
