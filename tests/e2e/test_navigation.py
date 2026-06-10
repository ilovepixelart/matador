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
    for col in ("id", "name", "time"):
        # document order: the header span precedes every row span
        header_x = page.locator(f'#jobs [data-col="{col}"]').first.bounding_box()["x"]
        row_x = page.locator(f'#jobs details [data-col="{col}"]').first.bounding_box()["x"]
        assert abs(header_x - row_x) < 1, f"{col} column drifts: header {header_x} vs row {row_x}"


def test_cursor_semantics(page: Page, base_url, seeded):
    # The cursor communicates affordance: pointer on clickables, help on
    # hover-for-more info, not-allowed on disabled controls.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    cur = lambda sel: page.eval_on_selector(sel, "el => getComputedStyle(el).cursor")  # noqa: E731
    assert cur("#jobs details summary") == "pointer"  # rows open
    assert cur("#sidebar-backdrop") == "pointer"  # closes the drawer
    page.wait_for_selector("#redis-bar .chip[title]")
    assert cur("#redis-bar .chip[title]") == "help"  # info on hover, not clickable
    page.eval_on_selector("#jobs button", "el => el.disabled = true")
    assert cur("#jobs button") == "not-allowed"  # can't press while in flight


def test_open_row_does_not_deform_the_columns(page: Page, base_url, seeded):
    # The detail body spans every track of the shared subgrid — without a zero
    # intrinsic width it feeds the max-content tracks and deforms ALL rows.
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    label = page.locator("#jobs details summary label").first
    before = label.bounding_box()["width"]
    page.locator("#jobs details summary").first.click()
    expect(page.locator("#jobs details[open] .acc-body > *").first).to_be_visible()
    assert abs(label.bounding_box()["width"] - before) < 1, "open row inflated the checkbox track"
