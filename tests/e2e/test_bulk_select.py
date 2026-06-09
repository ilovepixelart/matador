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


def test_checking_a_row_does_not_open_it(page: Page, base_url, seeded_many):
    # The checkbox sits inside the <details> summary. Checking it must not toggle the
    # row open — the <label> forwards the click to the checkbox, which consumes the
    # activation, so the summary never toggles.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details[open]")).to_have_count(0)

    page.locator(".jcheck").nth(0).check()
    expect(page.locator("#bulk-count")).to_have_text("1")  # the check registered
    expect(page.locator("#jobs details[open]")).to_have_count(0)  # but the row stayed closed


def test_clicking_row_action_area_does_not_open_it(page: Page, base_url, seeded_many):
    # Clicking the padding/gaps of the per-row action area must not toggle the row.
    # row-controls.js preventDefaults the summary's toggle for [data-no-toggle].
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details[open]")).to_have_count(0)

    page.locator("summary div[data-no-toggle]").first.click(position={"x": 2, "y": 10})
    expect(page.locator("#jobs details[open]")).to_have_count(0)  # row stayed closed


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
