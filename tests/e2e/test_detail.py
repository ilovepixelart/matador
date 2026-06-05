"""E2E: the <details> accordion lazy-loads job detail over htmx on first open."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_accordion_lazy_loads_detail_on_open(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=completed")
    row = page.locator("#jobs details").first
    expect(row).to_contain_text("okjob")
    # the return-value section lives only in the lazily-loaded detail (the summary
    # shows a data preview, so we key off detail-only content instead)
    expect(row).not_to_contain_text("result")
    row.locator("summary").click()
    expect(row).to_contain_text("result")  # detail fetched on toggle
    expect(row).to_contain_text("done")  # the actual return value
