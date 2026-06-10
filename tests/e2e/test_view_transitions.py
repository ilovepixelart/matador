"""E2E: user-initiated panel swaps (tabs) animate via the View Transitions API
where supported — and live SSE refreshes deliberately do NOT."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_tab_swaps_start_a_view_transition(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.evaluate(
        """() => { window.__vt = 0;
             const orig = document.startViewTransition?.bind(document);
             if (orig) document.startViewTransition =
               (cb) => { window.__vt++; return orig(cb); } }"""
    )
    page.locator(f'a[hx-get="/queues/{QUEUE}?state=failed"]').click()
    expect(page.locator("#jobs")).to_contain_text("badjob")  # swap landed
    assert page.evaluate("window.__vt") >= 1, "tab swap did not use a view transition"
