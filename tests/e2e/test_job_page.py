"""E2E: the standalone job page - drill into one job, see its detail + metadata,
go back, and a gone job shows a styled not-found."""

import re

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_job_page_shows_detail_metadata_and_back(page: Page, base_url, seeded):
    jid = seeded["completed"]
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/{jid}")
    panel = page.locator("#queue-panel")
    expect(panel).to_contain_text(f"#{jid}")  # full id (untruncated)
    expect(panel).to_contain_text(QUEUE)  # carries its queue
    expect(panel).to_contain_text("ada@example.com")  # the job data
    expect(panel).to_contain_text("duration")  # started→finished delta

    panel.locator("a.btn-ghost").first.click()  # back link
    expect(page).to_have_url(re.compile(rf"/queues/{QUEUE}"))
    expect(page.locator("#jobs")).to_be_visible()


def test_job_page_gone_job_shows_styled_not_found(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}/jobs/ghost-404")
    expect(page.locator("#queue-panel")).to_contain_text("Job not found")
