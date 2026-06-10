"""E2E: the hotkeys popover — native Popover API (button click + ? key),
light-dismiss and Esc come from the platform, not our JS."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_button_opens_the_hotkeys_popover(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#hotkeys")).not_to_be_visible()
    page.locator('[popovertarget="hotkeys"]').click()
    expect(page.locator("#hotkeys")).to_be_visible()
    expect(page.locator("#hotkeys")).to_contain_text("Search")
    page.keyboard.press("Escape")  # the platform closes it
    expect(page.locator("#hotkeys")).not_to_be_visible()


def test_question_mark_toggles_the_popover(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.keyboard.press("?")
    expect(page.locator("#hotkeys")).to_be_visible()
    page.keyboard.press("?")
    expect(page.locator("#hotkeys")).not_to_be_visible()


def test_question_mark_in_the_search_box_just_types(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    box = page.locator('input[name="query"]')
    box.click()
    page.keyboard.type("why?")
    expect(box).to_have_value("why?")
    expect(page.locator("#hotkeys")).not_to_be_visible()
