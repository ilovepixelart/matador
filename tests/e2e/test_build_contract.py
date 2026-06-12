"""E2E: the build's contract - a class NAME in the markup proves nothing if
its CSS got dropped (a purged utility, a broken @source pin, a dead dark
variant: each happened or nearly happened today). These assert computed
styles, not class lists.
"""

from playwright.sync_api import Page

from .conftest import QUEUE


def test_theme_toggle_actually_restyles(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    style = lambda: page.evaluate(  # noqa: E731
        "() => [getComputedStyle(document.body).backgroundColor,"
        "       getComputedStyle(document.documentElement).colorScheme]"
    )
    bg_before, scheme_before = style()
    page.locator("[data-js-theme-toggle]").click()
    bg_after, scheme_after = style()
    assert bg_before != bg_after, "theme toggled but the page did not restyle"
    assert scheme_before != scheme_after, "native widgets did not follow the theme"


def test_jinja_built_badge_classes_are_styled(page: Page, base_url, seeded):
    # state badges build their classes in Jinja (bg-{{token}}/10) - the scanner
    # can't see them; @source inline() pins them into the build. Verify the
    # pin holds: the badge background actually resolves to a colour.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    bg = page.eval_on_selector(
        "#sidebar a .tabular-nums > span", "el => getComputedStyle(el).backgroundColor"
    )
    assert bg not in ("rgba(0, 0, 0, 0)", "transparent"), f"badge unstyled: {bg}"


def test_confirm_dialog_opens_centered(page: Page, base_url, seeded):
    # v4 preflight zeroes margins on EVERYTHING including <dialog>, which
    # silently kills the UA's margin:auto centering - it happened.
    page.goto(f"{base_url}/queues/{QUEUE}?state=failed")
    page.locator('#jobs button[data-tip="Remove this job"]').first.click()
    box = page.locator("#confirm-dialog").bounding_box()
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    # generous tolerance: the UA reserves slightly asymmetric space at small
    # viewports; the regression guarded here is a full top-left jump
    assert abs(box["x"] - (vw - box["x"] - box["width"])) < 30, "dialog off-center horizontally"
    assert abs(box["y"] - (vh - box["y"] - box["height"])) < 30, "dialog off-center vertically"
