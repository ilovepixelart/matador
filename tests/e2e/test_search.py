"""E2E: the debounced live search narrows the list and restores when cleared."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_search_narrows_then_restores(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    jobs = page.locator("#jobs")
    expect(jobs).to_contain_text("alpha")

    page.fill('input[name="query"]', "beta")  # debounce is absorbed by auto-retry
    expect(jobs).to_contain_text("beta")
    expect(jobs).not_to_contain_text("alpha")

    page.fill('input[name="query"]', "")
    expect(jobs).to_contain_text("alpha")  # cleared → full list back
