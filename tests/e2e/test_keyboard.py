"""E2E: keyboard affordances — "/" jumps to search, Escape closes what's open.
Both stay out of the way while you're typing.
"""

from playwright.sync_api import Page, expect

from .conftest import QUEUE

PHONE = {"width": 390, "height": 844}


def test_slash_focuses_the_search_box(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.keyboard.press("/")
    box = page.locator('input[name="query"]')
    expect(box).to_be_focused()
    page.keyboard.type("alpha")
    expect(box).to_have_value("alpha")  # the "/" itself was not typed


def test_slash_while_typing_is_just_a_slash(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    box = page.locator('input[name="query"]')
    box.click()
    page.keyboard.type("a/b")
    expect(box).to_have_value("a/b")  # no hijack inside inputs


def test_escape_closes_an_open_row(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("#jobs details:first-of-type summary").click()
    expect(page.locator("#jobs details[open]")).to_have_count(1)
    page.keyboard.press("Escape")
    expect(page.locator("#jobs details[open]")).to_have_count(0)


def test_escape_closes_the_drawer(page: Page, base_url, seeded):
    page.set_viewport_size(PHONE)
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("[data-js-sidebar-toggle]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()
    page.keyboard.press("Escape")
    expect(page.locator("#sidebar")).not_to_be_in_viewport()
