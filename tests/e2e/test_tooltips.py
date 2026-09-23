"""E2E: tooltips - text comes from data-tip, the accessible name from the
element's own (sr-only) content, never aria-label (which would fight the
visible label; Sonar S6853/S7927)."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_hovering_an_action_button_shows_its_tooltip(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    btn = page.locator('#jobs button[data-tip="Retry this job"]').first
    btn.hover()
    tip = page.locator("#tip")
    expect(tip).to_have_css("opacity", "1")  # the tip's text outlives its showing
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
    # build too - a stale app.css without the .sr-only utility renders these as
    # plain text on every icon button.
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    span = page.locator('#jobs button[data-tip="Retry this job"] .sr-only').first
    box = span.bounding_box()
    assert box["width"] <= 1 and box["height"] <= 1, f"sr-only text is visible: {box}"


def test_escape_dismisses_a_tooltip_without_moving_focus(page: Page, base_url, seeded):
    """WCAG 1.4.13: content that appears on focus has to be dismissible without
    moving the pointer or the focus. A tip sits over whatever is beneath it, so a
    keyboard user who cannot dismiss it cannot read what it covers."""
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    btn = page.locator('#jobs button[data-tip="Retry this job"]').first
    # The table morphs once shortly after load (its first live refresh), and a focus
    # that lands in that window goes back to the body with it. Focus until it sticks,
    # rather than sleeping a guess at how long that takes.
    page.wait_for_function(
        """(sel) => {
            const el = document.querySelector(sel);
            if (!el) return false;
            el.focus();
            return document.activeElement === el;
        }""",
        arg='#jobs button[data-tip="Retry this job"]',
    )
    # opacity, not to_be_visible(): the tip is always in the DOM and always has a
    # box, so only what a person can actually see says whether it is showing
    expect(page.locator("#tip")).to_have_css("opacity", "1")

    page.keyboard.press("Escape")

    expect(page.locator("#tip")).to_have_css("opacity", "0")
    expect(btn).to_be_focused()  # dismissed, not escaped from


def test_the_tip_says_what_it_is(page: Page, base_url, seeded):
    """A bare div is announced as nothing. `role="tooltip"` is what the platform
    reads it as, and costs one attribute."""
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")

    expect(page.locator("#tip")).to_have_attribute("role", "tooltip")
