"""E2E: multi-select bulk delete - the browser-only behaviours. The hard part is
the selection surviving pagination (ids live in a JS Set across htmx swaps)."""

import re

import pytest
from playwright.sync_api import Page, expect

from .conftest import QUEUE, wait_for_live


def test_selecting_rows_reveals_the_bulk_bar(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details")).to_have_count(20)  # page 1 of 25
    expect(page.locator("#bulk-bar")).not_to_be_visible()

    page.locator(".jcheck").nth(0).check()
    page.locator(".jcheck").nth(1).check()
    expect(page.locator("#bulk-bar")).to_be_visible()
    expect(page.locator("#bulk-count")).to_have_text("2")


def test_checking_a_row_does_not_open_it(page: Page, base_url, seeded_many):
    # The checkbox sits inside the <details> summary. Checking it must not toggle the
    # row open - the <label> forwards the click to the checkbox, which consumes the
    # activation, so the summary never toggles.
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details[open]")).to_have_count(0)

    page.locator(".jcheck").nth(0).check()
    expect(page.locator("#bulk-count")).to_have_text("1")  # the check registered
    expect(page.locator("#jobs details[open]")).to_have_count(0)  # but the row stayed closed


def test_clicking_row_action_area_does_not_open_it(page: Page, base_url, seeded_many):
    # Clicking the padding/gaps of the per-row action area must not toggle the row.
    # row-controls.js preventDefaults the summary's toggle for [data-no-toggle].
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    expect(page.locator("#jobs details[open]")).to_have_count(0)

    page.locator("summary [data-no-toggle]").first.click(position={"x": 2, "y": 10})
    expect(page.locator("#jobs details[open]")).to_have_count(0)  # row stayed closed


def test_selection_persists_across_pages(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator(".jcheck").nth(0).check()
    page.locator(".jcheck").nth(1).check()
    expect(page.locator("#bulk-count")).to_have_text("2")

    page.get_by_role("link", name="2", exact=True).click()  # paginate (htmx, not a reload)
    expect(page).to_have_url(re.compile(r"page=2"))
    expect(page.locator("#bulk-count")).to_have_text("2")  # ← survived the swap
    expect(page.locator("#bulk-bar")).to_be_visible()

    page.locator(".jcheck").nth(0).check()  # +1 on page 2
    expect(page.locator("#bulk-count")).to_have_text("3")


def test_bulk_delete_via_confirm_dialog(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    for i in range(3):
        page.locator(".jcheck").nth(i).check()
    expect(page.locator("#bulk-count")).to_have_text("3")

    page.locator("#bulk-delete").click()
    dialog = page.locator("dialog[open]")
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Delete 3 jobs?")  # dynamic count
    dialog.locator("#confirm-ok").click()

    expect(page.locator("#bulk-count")).to_have_text("0")  # cleared after delete
    expect(page.locator("#bulk-bar")).not_to_be_visible()
    expect(page.locator("#jobs details")).to_have_count(20)  # 25 → 22, page still full


def test_select_all_on_page_then_clear(page: Page, base_url, seeded_many):
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    page.locator("#select-all").check()
    expect(page.locator("#bulk-count")).to_have_text("20")  # the whole page
    page.get_by_role("button", name="clear").click()
    expect(page.locator("#bulk-bar")).not_to_be_visible()


# Follows ONE live refresh of the job list end to end: the first request the list
# issues after these listeners are attached, identified by its XHR. Filtering on
# the target is not enough: htmx fires afterSwap and afterSettle once per settled
# element, all carrying the same target, and a refresh that swapped before the
# listeners were attached can still settle after, with the box untouched. An
# action ('x' or 'click'), if any, lands in that request's afterSwap: after the
# morph, before the settle. The state is read inside the page once the request has
# settled and two frames have run, so no later refresh can be sampled instead.
# It starts only once nothing is requesting or settling and two more frames have
# run, so no callback an earlier refresh queued can land inside this one's swap.
_ONE_REFRESH = """async (action) => {
  const frames = (n) => new Promise((done) => {
    const step = (k) => (k ? requestAnimationFrame(() => step(k - 1)) : done());
    step(n);
  });
  while (document.querySelector('.htmx-request, .htmx-settling')) await frames(1);
  await frames(2);
  return await new Promise((resolve) => {
    const live = document.querySelector('[data-jobs-live]');
    const box = () => document.querySelector('#jobs .jcheck');
    const checked = () => document.querySelectorAll('#jobs .jcheck:checked').length;
    let xhr = null, swapped = false;
    const onRequest = (e) => { if (!xhr && e.detail.elt === live) xhr = e.detail.xhr; };
    const onSwap = (e) => {
        if (swapped || !xhr || e.detail.xhr !== xhr) return;
        swapped = true;
        if (action === 'x') {
            document.dispatchEvent(new KeyboardEvent('keydown', {key: 'x', bubbles: true}));
        } else if (action === 'click') {
            box().click();
        }
    };
    const onSettle = (e) => {
        if (!swapped || e.detail.xhr !== xhr) return;
        for (const [type, fn] of [['htmx:beforeRequest', onRequest],
                                  ['htmx:afterSwap', onSwap],
                                  ['htmx:afterSettle', onSettle]]) {
            document.body.removeEventListener(type, fn);
        }
        const atSettle = box().checked;
        requestAnimationFrame(() => requestAnimationFrame(
            () => resolve({atSettle, finalChecked: checked()})));
    };
    document.body.addEventListener('htmx:beforeRequest', onRequest);
    document.body.addEventListener('htmx:afterSwap', onSwap);
    document.body.addEventListener('htmx:afterSettle', onSettle);
    htmx.trigger(live, 'sse:changed');
  });
}"""


def test_a_selection_is_never_visibly_lost_by_a_refresh(page: Page, base_url, seeded_many):
    """The server does not know what you have selected, so every live refresh brings
    back unchecked boxes and the page re-applies the selection. Re-applying it a
    frame later leaves a window where the ticks are gone: a click landing in it
    toggles a box the page is about to re-tick, and in a background tab, where
    requestAnimationFrame does not run at all, the selection simply looks lost.

    Asserted at the instant the swap settles, which is the only moment that matters.
    """
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    wait_for_live(page)
    page.locator(".jcheck").first.check()
    expect(page.locator("#bulk-count")).to_have_text("1")

    page.evaluate("""() => {
        window.__checkedAtSettle = null;
        document.body.addEventListener('htmx:afterSettle', () => {
            const box = document.querySelector('.jcheck');
            window.__checkedAtSettle = box ? box.checked : null;
        }, { once: true });
        htmx.trigger(document.querySelector('[data-jobs-live]'), 'sse:changed');
    }""")
    page.wait_for_function("() => window.__checkedAtSettle !== null")

    assert page.evaluate("() => window.__checkedAtSettle") is True
    expect(page.locator("#bulk-count")).to_have_text("1")


@pytest.mark.parametrize("action", ["x", "click"])
def test_a_row_deselected_during_a_refresh_stays_deselected(
    page: Page, base_url, seeded_many, action
):
    """A live refresh brings back unchecked boxes and the page re-applies the
    selection. A deselect that lands between the swap and that re-apply must win:
    the reader unchecked the row, so it may not come back checked and stay in the
    set the bulk delete will send.
    """
    page.goto(f"{base_url}/queues/{QUEUE}?state=wait")
    wait_for_live(page)
    page.keyboard.press("j")
    page.keyboard.press("x")
    expect(page.locator("#jobs .jcheck:checked")).to_have_count(1)

    refresh = page.evaluate(_ONE_REFRESH, action)

    assert refresh["finalChecked"] == 0
    expect(page.locator("#bulk-bar")).to_be_hidden()
