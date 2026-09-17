"""The slower sweep: every viewport, every section, keyboard and network.

Not run on every deploy — the smoke suite is for that. This is the one that
catches a control clipped at 320px, a section that only breaks in French, or a
page quietly fetching something it should not.
"""

from __future__ import annotations

import os

import pytest

from tests.browser.conftest import wait_for_answer, no_horizontal_overflow, \
    small_touch_targets

#: A hosted model answers in seconds; a local one on a laptop takes a minute
#: or more for the same question. Configurable so one suite runs against
#: either without a number in the source being wrong for one of them.
ANSWER_TIMEOUT = int(os.environ.get("CLARE_ANSWER_TIMEOUT_MS", "90000"))

VIEWPORTS = [320, 390, 768, 1024, 1280, 1440, 1920]


# --------------------------------------------------------------- layout ---

@pytest.mark.parametrize("width", VIEWPORTS)
def test_no_viewport_overflows_horizontally(sized, width):
    page = sized(width)
    ok, detail = no_horizontal_overflow(page)
    assert ok, f"{width}px: {detail}"


@pytest.mark.parametrize("width", VIEWPORTS)
def test_the_composer_and_its_send_control_stay_usable(sized, width):
    page = sized(width)
    field = page.locator("#rp-question textarea")
    send = page.locator("button.rp-submit")
    assert field.is_visible() and send.is_visible(), f"{width}px"
    box = send.bounding_box()
    assert box["x"] >= -1 and box["x"] + box["width"] <= width + 1, \
        f"{width}px: send control is clipped"


@pytest.mark.parametrize("width", VIEWPORTS)
def test_the_report_control_stays_a_circle_in_the_corner(sized, width):
    page = sized(width)
    box = page.locator(".rp-report > .label-wrap").bounding_box()
    assert 44 <= box["height"] <= 48, f"{width}px: {box['height']}px tall"
    assert box["x"] >= 0, f"{width}px: pushed off the left edge"
    assert box["y"] + box["height"] <= page.viewport_size["height"] + 1


@pytest.mark.parametrize("width", [320, 390, 768])
def test_the_language_control_never_overflows_on_a_phone(sized, width):
    page = sized(width)
    box = page.locator(".rp-switch-site").bounding_box()
    assert box["x"] + box["width"] <= width + 1, f"{width}px: language control clipped"


def test_large_screens_do_not_stretch_the_content(sized):
    page = sized(1920, 1080)
    width = page.evaluate(
        "Math.round(document.querySelector('.contain').getBoundingClientRect().width)")
    assert width <= 1240, f"content stretched to {width}px"


# -------------------------------------------------------- accessibility ---

@pytest.mark.parametrize("width", [320, 768, 1440])
def test_every_control_meets_the_touch_target_floor(sized, width):
    page = sized(width)
    small = small_touch_targets(page)
    assert not small, f"{width}px: {small}"


def test_the_skip_link_is_the_first_thing_a_keyboard_reaches(page):
    page.keyboard.press("Tab")
    focused = page.evaluate(
        "() => { const a = document.activeElement;"
        " return {cls: (a.className||'').toString(), text: (a.textContent||'').trim()}; }")
    assert "rp-skip" in focused["cls"], focused


def test_tabbing_moves_through_the_page_without_trapping(page):
    seen = []
    for _ in range(14):
        page.keyboard.press("Tab")
        seen.append(page.evaluate(
            "() => document.activeElement.tagName + '.' +"
            " (document.activeElement.className||'').toString().slice(0,24)"))
    assert len(set(seen)) > 4, f"focus appears trapped: {set(seen)}"


def test_focus_is_visible_on_the_report_control(page):
    page.locator(".rp-report > .label-wrap").focus()
    outline = page.evaluate(
        "getComputedStyle(document.querySelector('.rp-report > .label-wrap'))"
        ".outlineWidth")
    assert outline not in ("", "0px"), "keyboard focus must be visible"


def test_the_document_language_follows_the_interface(page):
    assert page.evaluate("document.documentElement.lang") == "en"
    page.locator(".rp-switch-site label", has_text="Français").click()
    page.wait_for_timeout(2500)
    assert page.evaluate("document.documentElement.lang") == "fr"


# ------------------------------------------------------------- sections ---

def test_the_glossary_section_loads_in_both_languages(page):
    page.locator('[role="tab"]', has_text="Words").click()
    page.wait_for_selector(".rp-gloss", timeout=15_000)
    assert page.locator(".rp-gloss").count() > 5
    assert page.locator(".rp-gloss-search textarea, .rp-gloss-search input").is_visible()

    page.locator(".rp-switch-site label", has_text="Français").click()
    page.wait_for_timeout(2500)
    text = page.locator(".contain").inner_text()
    assert "Les mots" in text
    for leak in ("Search a word", "Start with a topic"):
        assert leak not in text, f"English leaked: {leak!r}"


@pytest.mark.needs_corpus
def test_the_sources_section_reports_the_corpus(page):
    page.locator('[role="tab"]', has_text="Sources").click()
    page.wait_for_selector(".rp-stat", timeout=40_000)
    assert page.locator(".rp-stat").count() >= 4
    refresh = page.locator("button.rp-chip", has_text="Refresh")
    assert refresh.is_visible()
    box = refresh.bounding_box()
    assert box["width"] < 300, "the refresh control should not span the page"
    ok, detail = no_horizontal_overflow(page)
    assert ok, detail


# --------------------------------------------------------- conversation ---

def test_a_question_needing_a_place_asks_for_one(page):
    page.fill("#rp-question textarea", "What do I need to renew my residence permit?")
    page.click("button.rp-submit")
    page.wait_for_selector(".rp-clarify", timeout=60_000)
    assert "Where in France" in page.locator(".rp-clarify-head").inner_text()


def test_start_over_returns_to_the_landing_page(page):
    page.fill("#rp-question textarea", "What counts as proof of address?")
    page.click("button.rp-submit")
    page.wait_for_selector(".rp-thread .rp-turn", timeout=15_000)
    page.wait_for_timeout(1000)
    page.click("button.rp-restart")
    page.wait_for_timeout(1500)
    assert not page.evaluate("document.body.classList.contains('rp-answered')")
    assert page.locator(".rp-headline").is_visible()
    assert page.locator(".rp-cat").count() >= 5


@pytest.mark.needs_answers
def test_switching_language_mid_conversation_keeps_it_coherent(page):
    page.fill("#rp-question textarea",
              "What are the admission requirements at Montpellier Business School?")
    page.click("button.rp-submit")
    wait_for_answer(page, ANSWER_TIMEOUT)

    page.locator(".rp-switch-site label", has_text="Français").click()
    page.wait_for_timeout(3000)
    text = page.locator(".contain").inner_text()
    assert page.evaluate("document.documentElement.lang") == "fr"
    # The answer keeps the language it was written in; the chrome follows.
    for leak in ("Reply in", "Start over", "Source:"):
        assert leak not in text, f"chrome left in English: {leak!r}"


# ------------------------------------------------------------ the report --

def test_the_report_sheet_opens_and_takes_a_report(page):
    page.locator(".rp-report > .label-wrap").click()
    page.wait_for_selector(".rp-report-text textarea", timeout=10_000)
    areas = page.locator(".rp-report-text textarea")
    assert areas.count() == 2, "what happened, and what you were trying to do"
    assert page.locator("button.rp-report-cancel").is_visible()
    assert page.locator("button.rp-report-send").is_visible()

    box = page.locator(".rp-report > div:not(.label-wrap)").last.bounding_box()
    assert box["x"] >= -1 and box["y"] >= -1, "the sheet is off-screen"


def test_the_report_control_opens_from_the_keyboard(page):
    control = page.locator(".rp-report > .label-wrap")
    control.focus()
    page.keyboard.press("Enter")
    page.wait_for_selector(".rp-report-text textarea", timeout=10_000)
    assert page.locator(".rp-report-text textarea").first.is_visible()


# -------------------------------------------------------------- network ---

def test_the_page_does_not_call_anything_unexpected(browser_context, clare_app):
    """A landing page should talk to itself and to its font provider."""
    page = browser_context.new_page()
    seen: list[str] = []
    page.on("request", lambda r: seen.append(r.url))
    # Not networkidle: Gradio keeps an event stream open, so the page never
    # goes idle and the wait would always time out.
    page.goto(clare_app.base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#rp-question textarea", timeout=20_000)
    page.wait_for_timeout(1500)

    allowed = ("127.0.0.1", "localhost", "fonts.googleapis.com",
               "fonts.gstatic.com", "data:", "blob:")
    unexpected = [u for u in seen if not any(a in u for a in allowed)]
    page.close()
    assert not unexpected, f"unexpected outbound requests: {unexpected[:5]}"


def test_no_application_request_fails(browser_context, clare_app):
    page = browser_context.new_page()
    failures: list[str] = []
    page.on("response", lambda r: failures.append(f"{r.status} {r.url}")
            if r.status >= 500 and "127.0.0.1" in r.url else None)
    page.goto(clare_app.base_url, wait_until="domcontentloaded")
    page.wait_for_selector("#rp-question textarea", timeout=20_000)
    page.wait_for_timeout(1000)
    page.close()
    assert not failures, failures
