"""Browsers parse leniently — one mismatched closing tag silently eats DOM
(it happened: a </div> closing a <span> cell collapsed 20 rows to 2, and the
suite only caught it three layers up). Assert tag balance server-side for the
element types the views are built of.
"""

import re

from .conftest import QUEUE

BALANCED = ("div", "span", "details", "summary", "label", "button", "table", "dialog", "a")


async def test_rendered_views_have_balanced_tags(client, seeded):
    for url in (f"/queues/{QUEUE}?state=failed", f"/queues/{QUEUE}?state=wait", "/"):
        r = await client.get(url)
        assert r.status_code == 200
        for tag in BALANCED:
            opens = len(re.findall(rf"<{tag}[\s>]", r.text))
            closes = r.text.count(f"</{tag}>")
            assert opens == closes, f"{url}: <{tag}> opens {opens} vs closes {closes}"
