"""Unit: _page_window — the windowed paginator (first, last, current ± span, ellipsis)."""

import pytest

from matador.app import _page_window


@pytest.mark.parametrize(
    "page, pages, expected",
    [
        (1, 1, [1]),
        (1, 5, [1, 2, 3, 4, 5]),  # <= 7 pages: show them all
        (1, 7, [1, 2, 3, 4, 5, 6, 7]),
        (1, 11, [1, 2, 3, None, 11]),  # near the start
        (6, 11, [1, None, 4, 5, 6, 7, 8, None, 11]),  # middle: ellipsis on both sides
        (11, 11, [1, None, 9, 10, 11]),  # near the end
    ],
)
def test_page_window(page, pages, expected):
    assert _page_window(page, pages) == expected


def test_window_is_sorted_and_unique():
    win = [p for p in _page_window(50, 100) if p is not None]
    assert win == sorted(win)
    assert len(win) == len(set(win))
    assert win[0] == 1 and win[-1] == 100  # always anchors first + last
