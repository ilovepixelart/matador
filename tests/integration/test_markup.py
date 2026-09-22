"""Browsers parse leniently - one mismatched closing tag silently eats DOM
(it happened: a </div> closing a <span> cell collapsed 20 rows to 2, and the
suite only caught it three layers up). Assert tag balance server-side for the
element types the views are built of.
"""

import re
from pathlib import Path

import matador
from matador.service import STREAM_RATES

from .conftest import QUEUE

TEMPLATES = Path(matador.__file__).parent / "templates"

BALANCED = ("div", "span", "details", "summary", "label", "button", "table", "dialog", "a")


async def test_rendered_views_have_balanced_tags(client, seeded):
    for url in (f"/queues/{QUEUE}?state=failed", f"/queues/{QUEUE}?state=wait", "/"):
        r = await client.get(url)
        assert r.status_code == 200
        for tag in BALANCED:
            opens = len(re.findall(rf"<{tag}[\s>]", r.text))
            closes = r.text.count(f"</{tag}>")
            assert opens == closes, f"{url}: <{tag}> opens {opens} vs closes {closes}"


def _live_regions() -> list[tuple[str, str]]:
    """Every opening tag, in every template, whose hx-trigger listens to the stream.
    Read from the template sources: a region is live wherever it is declared, not
    only on the pages a test happens to render."""
    pattern = re.compile(r"<[a-zA-Z][^<>]*hx-trigger=\"[^\"]*sse:[^\"]*\"[^<>]*>")
    return [
        (str(path.relative_to(TEMPLATES)), tag)
        for path in sorted(TEMPLATES.rglob("*.html"))
        for tag in pattern.findall(path.read_text())
    ]


def test_live_regions_use_server_cadence():
    """The stream enforces each region's refresh rate, with a trailing edge. htmx's
    `throttle` has none (it drops the last change of a burst), and an `hx-sync` that
    names no strategy drops a refresh that arrives while one is in flight: both lose
    the tail, so every region states `queue last`."""
    regions = _live_regions()
    assert len(regions) >= 8, regions  # the regions known today; more are welcome
    for where, tag in regions:
        trigger = re.search(r'hx-trigger="([^"]*)"', tag).group(1)
        assert "throttle" not in trigger, f"{where}: client throttle on a stream trigger: {trigger}"
        names = re.findall(r"sse:([\w-]+)", trigger)
        assert names and all(n in STREAM_RATES for n in names), f"{where}: {trigger}"
        assert 'hx-sync="this:queue last"' in tag, f"{where}: must state queue last"
