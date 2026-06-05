"""Unit: _human_bytes — Redis memory sizing shown in the top bar."""

import pytest

from matador.service import _human_bytes


@pytest.mark.parametrize(
    "n, expected",
    [
        (None, "—"),
        (0, "—"),
        (-5, "—"),  # nonsense input → dash, not "-5B"
        (512, "512B"),  # bytes: no decimals
        (1536, "1.50K"),
        (5 * 1024**2, "5.00M"),
        (3 * 1024**3, "3.00G"),
        (4 * 1024**4, "4.00T"),
        (2 * 1024**5, "2.00P"),  # past the unit table → petabytes
    ],
)
def test_human_bytes(n, expected):
    assert _human_bytes(n) == expected
