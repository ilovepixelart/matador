"""E2E: with no saved choice the theme follows the OS (prefers-color-scheme);
a saved choice always wins over the OS."""

from playwright.sync_api import Page, expect

from .conftest import QUEUE


def test_first_visit_follows_the_os_scheme(page: Page, base_url, seeded):
    page.emulate_media(color_scheme="light")
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("html")).not_to_have_class("dark")

    page.emulate_media(color_scheme="dark")
    page.reload()
    expect(page.locator("html")).to_have_class("dark")


def test_saved_choice_beats_the_os(page: Page, base_url, seeded):
    page.emulate_media(color_scheme="light")
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("[data-js-theme-toggle]").click()  # user explicitly picks dark
    page.reload()
    expect(page.locator("html")).to_have_class("dark")  # OS says light; choice wins
