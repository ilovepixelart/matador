"""E2E: destructive actions go through the custom confirm <dialog>, and
pause/resume toggles - the full browser→htmx→Redis round trip."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_remove_via_confirm_dialog_drops_the_row(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    rows = page.locator("#jobs details")
    expect(rows).to_have_count(3)

    page.get_by_role("button", name="Remove this job").first.click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_be_visible()  # our modal, not window.confirm
    dialog.locator("#confirm-ok").click()

    expect(rows).to_have_count(2)  # one really removed


def test_confirm_dialog_survives_history_restore(page: Page, base_url, seeded):
    """A Back/Forward navigation re-fetches the pushed URL and htmx replaces the
    whole <body> - swapping in a fresh confirm <dialog>. A behaviour that captured
    the dialog at load would now showModal() on a detached node ('element is not
    in a Document') and the modal would silently never open. Delegation survives it.
    """
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    rows = page.locator("#jobs details")
    expect(rows).to_have_count(3)

    # htmx push (no JS reload), then browser Back (full-body history restore,
    # still no JS reload) - exactly the sequence that orphaned the dialog node.
    page.get_by_role("link", name="failed").first.click()
    expect(page.locator("#jobs")).to_contain_text("badjob")  # the failed tab loaded
    page.go_back()
    expect(rows).to_have_count(3)  # restored to the wait tab

    # the modal must still open, and Confirm must still issue the request
    page.get_by_role("button", name="Remove this job").first.click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_be_visible()
    dialog.locator("#confirm-ok").click()
    expect(rows).to_have_count(2)


def test_cancel_dialog_keeps_every_row(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    rows = page.locator("#jobs details")
    expect(rows).to_have_count(3)

    page.get_by_role("button", name="Remove this job").first.click()
    page.locator("dialog[open] #confirm-cancel").click()
    expect(page.locator("dialog")).not_to_be_visible()
    expect(rows).to_have_count(3)  # nothing happened


def test_pause_then_resume(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.get_by_role("button", name="Pause").click()
    expect(page.get_by_role("button", name="Resume")).to_be_visible()
    page.get_by_role("button", name="Resume").click()
    expect(page.get_by_role("button", name="Pause")).to_be_visible()


def test_retry_all_outcome_lands_in_the_live_region(page, base_url, seeded):
    from playwright.sync_api import expect

    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    page.locator('button:has-text("retry all")').click()
    page.locator("#confirm-ok").click()
    expect(page.locator("#announce")).to_contain_text("queued for retry")
