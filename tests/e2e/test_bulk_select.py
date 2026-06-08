"""E2E: multi-select bulk delete — the browser-only behaviours. The hard part is
the selection surviving pagination (ids live in a JS Set across htmx swaps)."""

import re

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_selecting_rows_reveals_the_bulk_bar(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details")).to_have_count(20)  # page 1 of 25
    expect(page.locator("#bulk-bar")).not_to_be_visible()

    page.locator(".jcheck").nth(0).check()
    page.locator(".jcheck").nth(1).check()
    expect(page.locator("#bulk-bar")).to_be_visible()
    expect(page.locator("#bulk-count")).to_have_text("2")


def test_selection_persists_across_pages(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator(".jcheck").nth(0).check()
    page.locator(".jcheck").nth(1).check()
    expect(page.locator("#bulk-count")).to_have_text("2")

    page.get_by_role("link", name="2", exact=True).click()  # paginate (htmx, not a reload)
    expect(page).to_have_url(re.compile(r"page=2"))
    expect(page.locator("#bulk-count")).to_have_text("2")  # ← survived the swap
    expect(page.locator("#bulk-bar")).to_be_visible()

    page.locator(".jcheck").nth(0).check()  # +1 on page 2
    expect(page.locator("#bulk-count")).to_have_text("3")


def test_bulk_delete_via_confirm_dialog(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    for i in range(3):
        page.locator(".jcheck").nth(i).check()
    expect(page.locator("#bulk-count")).to_have_text("3")

    page.locator("#bulk-delete").click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Delete 3 jobs?")  # dynamic count
    dialog.locator("#confirm-ok").click()

    expect(page.locator("#bulk-count")).to_have_text("0")  # cleared after delete
    expect(page.locator("#bulk-bar")).not_to_be_visible()
    expect(page.locator("#jobs details")).to_have_count(20)  # 25 → 22, page still full


def test_select_all_on_page_then_clear(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("#select-all").check()
    expect(page.locator("#bulk-count")).to_have_text("20")  # the whole page
    page.get_by_role("button", name="clear").click()
    expect(page.locator("#bulk-bar")).not_to_be_visible()
