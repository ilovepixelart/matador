"""Unit: wants_fragment - the fragment-vs-full-page decision from request headers."""

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


def test_default_state_picks_the_most_signal():
    from matador.app import _default_state

    # running work first, then problems, then what's queued, then history
    assert _default_state({"active": 2, "failed": 9}) == "active"
    assert _default_state({"active": 0, "failed": 1, "wait": 5}) == "failed"
    assert _default_state({"active": 0, "failed": 0, "wait": 5}) == "wait"
    assert _default_state({"active": 0, "failed": 0, "wait": 0, "delayed": 3}) == "delayed"
    assert _default_state({"active": 0, "failed": 0, "wait": 0, "delayed": 0}) == "completed"
    # parked flows are folded into `active` upstream (no flows tab), so a queue whose
    # only signal is parked flows arrives here as active and lands on the active tab
    assert _default_state({"active": 2, "failed": 0, "wait": 0}) == "active"


def test_no_live_region_targets_this():
    """`hx-target="this"` is banned on auto-refreshing regions.

    The panel (`#queue-panel`) sets an inherited `hx-target="#queue-panel"`. A
    region with `hx-target="this"` that fires AFTER the panel navigated away (its
    SSE/poll listener outlived the swap) re-roots to that inherited target and
    wipes the panel - the child-job "body disappears" bug. Every self-refreshing
    region must target its OWN stable id instead, so a detached fire hits a
    missing target and htmx aborts. See docs/live-updates.md.
    """
    import pathlib

    templates = pathlib.Path(__file__).resolve().parents[2] / "matador" / "templates"
    offenders = [
        f"{p.relative_to(templates)}:{i}"
        for p in templates.rglob("*.html")
        for i, line in enumerate(p.read_text().splitlines(), 1)
        if 'hx-target="this"' in line
    ]
    assert not offenders, f'hx-target="this" re-roots to #queue-panel on a stale fire: {offenders}'
