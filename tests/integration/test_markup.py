"""Browsers parse leniently - one mismatched closing tag silently eats DOM
(it happened: a </div> closing a <span> cell collapsed 20 rows to 2, and the
suite only caught it three layers up). Assert tag balance server-side for the
element types the views are built of.
"""

import re
from pathlib import Path

from toro import FlowChild as c  # noqa: N813 - `c("fetch", ...)` keeps trees readable

import matador
from matador.service import STREAM_RATES

from .conftest import QUEUE, hx

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


async def test_oob_twins_match_the_markup_they_replace(client, q, seeded):
    """A live refresh swaps a tab count and, on a flow page, the title's state pill
    out of band. Each twin is drawn by the same macro as the element it replaces, so
    the two cannot drift; this pins that by comparing the rendered spans."""
    page = await client.get(f"/queues/{QUEUE}?state=wait")
    fragment = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    shell = re.search(r'<span id="tabcount-wait"[^>]*>[^<]*</span>', page.text).group(0)
    twin = re.search(r'<span id="tabcount-wait"[^>]*>[^<]*</span>', fragment.text).group(0)
    assert twin.replace(' hx-swap-oob="true"', "") == shell

    parent = await q.add_flow("report", {}, children=[c("later", {}, delay=600_000)])
    job_page = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}")
    flow = await client.get(f"/queues/{QUEUE}/jobs/{parent.id}/flow?title=1")
    shell = re.search(r'<span id="job-state-pill"[^>]*>[^<]*</span>', job_page.text).group(0)
    twin = re.search(r'<span id="job-state-pill"[^>]*>[^<]*</span>', flow.text).group(0)
    assert twin.replace(' hx-swap-oob="true"', "") == shell
