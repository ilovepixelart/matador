"""E2E: tooltips — text comes from data-tip, the accessible name from the
element's own (sr-only) content, never aria-label (which would fight the
visible label; Sonar S6853/S7927)."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_hovering_an_action_button_shows_its_tooltip(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    btn = page.locator('#jobs button[data-tip="Retry this job"]').first
    btn.hover()
    tip = page.locator("#tip")
    expect(tip).to_be_visible()
    expect(tip).to_have_text("Retry this job")
    # Accessible name comes from content, and no aria-label is involved.
    expect(btn).to_have_accessible_name("Retry this job")
    assert btn.get_attribute("aria-label") is None


def test_theme_toggle_keeps_name_and_tooltip(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    btn = page.locator("[data-js-theme-toggle]")
    expect(btn).to_have_accessible_name("Toggle theme")
    btn.hover()
    expect(page.locator("#tip")).to_have_text("Toggle theme")


def test_icon_button_labels_are_for_screen_readers_only(page: Page, base_url, seeded):
    # The sr-only name must stay invisible to eyes: 1px and clipped. Guards the
    # build too — a stale app.css without the .sr-only utility renders these as
    # plain text on every icon button.
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    span = page.locator('#jobs button[data-tip="Retry this job"] .sr-only').first
    box = span.bounding_box()
    assert box["width"] <= 1 and box["height"] <= 1, f"sr-only text is visible: {box}"
