"""E2E: keyboard affordances - "/" jumps to search, Escape closes what's open.
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


def test_j_and_k_move_the_row_cursor(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.keyboard.press("j")  # no cursor yet: j lands on the first row
    first = page.locator("#jobs details summary").first
    expect(first).to_be_focused()
    page.keyboard.press("j")
    expect(page.locator("#jobs details summary").nth(1)).to_be_focused()
    page.keyboard.press("k")
    expect(first).to_be_focused()
    page.keyboard.press("k")  # top of the list: stays put
    expect(first).to_be_focused()


def test_o_opens_and_closes_the_focused_row(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.keyboard.press("j")
    page.keyboard.press("o")
    expect(page.locator("#jobs details[open]")).to_have_count(1)
    page.keyboard.press("o")
    expect(page.locator("#jobs details[open]")).to_have_count(0)


def test_x_selects_the_focused_row(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.keyboard.press("j")
    page.keyboard.press("x")
    expect(page.locator("#jobs .jcheck:checked")).to_have_count(1)
    expect(page.locator("#bulk-bar")).to_be_visible()  # selection feeds the bulk bar
    page.keyboard.press("x")
    expect(page.locator("#jobs .jcheck:checked")).to_have_count(0)


def test_jkxo_type_normally_in_the_search_box(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    box = page.locator('input[name="query"]')
    box.click()
    page.keyboard.type("jokx")
    expect(box).to_have_value("jokx")
    expect(page.locator("#jobs details[open]")).to_have_count(0)
