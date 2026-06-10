"""E2E: on phone-sized viewports the sidebar collapses into an off-canvas
drawer — opened by the header menu button, closed by the backdrop or by
picking a queue (you navigated; the drawer's job is done).
"""

from playwright.sync_api import Page, expect

from .conftest import QUEUE

PHONE = {"width": 390, "height": 844}


def test_sidebar_becomes_a_drawer_on_phones(page: Page, base_url, seeded):
    page.set_viewport_size(PHONE)
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")

    sidebar = page.locator("#sidebar")
    expect(sidebar).not_to_be_in_viewport()  # parked off-canvas, panel gets the width
    menu = page.locator("[data-js-sidebar-toggle]")
    expect(menu).to_be_visible()

    menu.click()
    expect(sidebar).to_be_in_viewport()  # drawer slid in

    page.locator("#sidebar-backdrop").click(position={"x": 380, "y": 400})
    expect(sidebar).not_to_be_in_viewport()  # backdrop click closes


def test_drawer_closes_after_picking_a_queue(page: Page, base_url, seeded):
    page.set_viewport_size(PHONE)
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("[data-js-sidebar-toggle]").click()
    expect(page.locator("#sidebar")).to_be_in_viewport()

    page.locator(f'#sidebar a[href*="{QUEUE}"]').first.click()
    expect(page.locator("#sidebar")).not_to_be_in_viewport()


def test_desktop_keeps_the_fixed_sidebar(page: Page, base_url, seeded):
    page.set_viewport_size({"width": 1280, "height": 800})
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#sidebar")).to_be_in_viewport()  # always there
    expect(page.locator("[data-js-sidebar-toggle]")).not_to_be_visible()
