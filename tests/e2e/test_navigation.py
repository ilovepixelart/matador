"""E2E: htmx tab swaps, deep-linking, and history (the browser-only behaviours)."""

import re

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_tabs_swap_the_job_list(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs")).to_contain_text("alpha")
    page.locator(f'a[hx-get="/queues/{QUEUE}?state=failed"]').click()
    expect(page.locator("#jobs")).to_contain_text("badjob")  # swapped in
    expect(page.locator("#jobs")).not_to_contain_text("alpha")  # old content gone


def test_deep_link_renders_cold(page: Page, base_url, seeded):
    # the highest-value htmx test: a pushed URL must full-page render the same view
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    expect(page.locator("#jobs")).to_contain_text("badjob")


def test_back_button_restores_previous_tab(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs")).to_contain_text("alpha")
    page.locator(f'a[hx-get="/queues/{QUEUE}?state=failed"]').click()
    expect(page).to_have_url(re.compile(r"state=failed"))
    page.go_back()
    expect(page).to_have_url(re.compile(r"state=wait"))
    expect(page.locator("#jobs")).to_contain_text("alpha")


def test_job_list_columns_align_with_the_header(page: Page, base_url, seeded):
    # The header and rows consume one column contract (job_cols) — measure the
    # rendered left edges so width drift between them can't come back.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    for col in ("id", "name"):
        # document order: the header span precedes every row span
        header_x = page.locator(f'#jobs [data-col="{col}"]').first.bounding_box()["x"]
        row_x = page.locator(f'#jobs details [data-col="{col}"]').first.bounding_box()["x"]
        assert abs(header_x - row_x) < 1, f"{col} column drifts: header {header_x} vs row {row_x}"
