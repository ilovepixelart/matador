"""E2E: WCAG 2.2 SC 2.5.8 Target Size (Minimum), Level AA - every pointer
target is at least 24x24 CSS pixels (padding counts toward the target; the
checkbox's clickable <label> is its target).
"""

from playwright.sync_api import Page

from .conftest import QUEUE

MIN = 24


def _box(page: Page, selector: str) -> dict:
    el = page.locator(selector).first
    el.wait_for(state="visible")
    return el.bounding_box()


def test_pointer_targets_meet_24px_minimum(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    offenders = []
    for label, sel in [
        ("row retry button", '#jobs button[data-tip="Retry this job"]'),
        ("row remove button", '#jobs button[data-tip="Remove this job"]'),
        ("row checkbox label", "#jobs details summary label"),
        ("select-all label", "#jobs label:has(#select-all)"),
        ("toolbar retry-all", '#jobs button:has-text("retry all")'),
        ("theme toggle", "[data-js-theme-toggle]"),
        ("tab link", "#queue-panel a[hx-push-url]"),
    ]:
        box = _box(page, sel)
        if box["width"] < MIN or box["height"] < MIN:
            offenders.append(f"{label}: {box['width']:.0f}x{box['height']:.0f}")
    assert not offenders, f"targets under 24x24px: {offenders}"


def test_pagination_targets_meet_24px_minimum(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    box = _box(page, '#jobs nav[aria-label="pagination"] a')
    assert box["width"] >= MIN and box["height"] >= MIN, f"pager target {box}"


def test_keyboard_focus_is_never_fully_obscured(page: Page, base_url, seeded_many):
    # SC 2.4.11 Focus Not Obscured: panels scroll BELOW the fixed chrome (the
    # header sits outside the scroll container), so a focused control can never
    # end up hidden under it - prove it for the bottom-most row action.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    btn = page.locator("#jobs button").last
    btn.focus()
    box = btn.bounding_box()
    header = page.locator("header").bounding_box()
    assert box["y"] >= header["y"] + header["height"], "focused control under the sticky chrome"
