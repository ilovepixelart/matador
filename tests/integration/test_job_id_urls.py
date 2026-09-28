"""Integration: a job id is a value inside a URL, not part of its syntax.

toro accepts `?`, `#`, `&`, `%` and `,` in a custom job id. Put into a path unencoded,
`order?x=1` became the path `/jobs/order` with a query string, so the row's buttons
acted on the job named `order`: Remove deleted the wrong job.
"""

import re

import pytest

from .conftest import QUEUE, hx


def _row(html: str, job_id: str) -> str:
    """The rendered row (a `<details>`) whose checkbox carries `job_id`."""
    value = f'value="{job_id.replace("&", "&amp;")}"'
    rows = [chunk for chunk in html.split("<details ")[1:] if value in chunk]
    assert rows, f"no row for {job_id!r}"
    return rows[0]


def _attr(row: str, name: str) -> str:
    found = re.search(rf'{name}="([^"]+)"', row)
    assert found, f"no {name} in the row"
    return found.group(1).replace("&amp;", "&")


@pytest.mark.parametrize("odd", ["order?x=1", "order#frag", "order&x", "order%3F"])
async def test_a_row_removes_the_job_it_shows(client, q, odd):
    await q.add("plain", {}, job_id="order")
    await q.add("odd", {}, job_id=odd)
    page = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())

    remove = _attr(_row(page.text, odd), "hx-delete")
    r = await client.request("DELETE", remove, headers=hx())

    assert r.status_code == 200
    assert await q.get_job(odd) is None, f"{odd!r} is still there after its Remove"
    assert await q.get_job("order") is not None, "Remove took a different job"


@pytest.mark.parametrize("odd", ["order?x=1", "order#frag", "order%3F"])
async def test_a_row_opens_the_job_it_shows(client, q, odd):
    await q.add("plain", {"who": "the-plain-one"}, job_id="order")
    await q.add("odd", {"who": "the-odd-one"}, job_id=odd)
    page = await client.get(f"/queues/{QUEUE}/jobs?state=wait", headers=hx())
    link = _attr(_row(page.text, odd), "hx-get")

    r = await client.get(link, headers=hx())

    assert r.status_code == 200
    assert "the-odd-one" in r.text, "the row opened a different job"
    assert "the-plain-one" not in r.text
