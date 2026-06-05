"""Unit: wants_fragment — the fragment-vs-full-page decision from request headers."""

from matador.app import wants_fragment


class _Req:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_fragment_for_plain_htmx_request():
    assert wants_fragment(_Req({"hx-request": "true"})) is True


def test_full_page_for_non_htmx_request():
    assert wants_fragment(_Req()) is False


def test_full_page_for_history_restore():
    # a history-restore re-fetches the pushed URL and must get the WHOLE page back
    assert (
        wants_fragment(_Req({"hx-request": "true", "hx-history-restore-request": "true"})) is False
    )
