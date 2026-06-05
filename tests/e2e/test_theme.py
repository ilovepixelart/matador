"""E2E: the theme toggle flips the document and persists across a full reload."""

from playwright.sync_api import Page

from .conftest import QUEUE


def _is_dark(page: Page) -> bool:
    return page.evaluate("() => document.documentElement.classList.contains('dark')")


def test_theme_toggle_persists_across_reload(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}")
    before = _is_dark(page)
    page.locator("button[data-js-theme-toggle]").click()
    assert _is_dark(page) is not before  # flipped immediately

    page.reload()
    assert _is_dark(page) is not before  # persisted via localStorage on reload
