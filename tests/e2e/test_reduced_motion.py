"""E2E: prefers-reduced-motion is honored — animations and transitions are
effectively disabled when the OS asks for less motion."""

from playwright.sync_api import Page

from .conftest import QUEUE


def _spin_duration(page: Page) -> float:
    return page.eval_on_selector(
        "#search-spin svg", "el => parseFloat(getComputedStyle(el).animationDuration)"
    )


def test_animations_stop_under_reduced_motion(page: Page, base_url, seeded):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    assert _spin_duration(page) >= 0.5  # the spinner really animates by default

    page.emulate_media(reduced_motion="reduce")
    assert _spin_duration(page) < 0.05  # and effectively stops on request
