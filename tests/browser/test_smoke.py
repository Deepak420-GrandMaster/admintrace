"""The five minutes of checking worth doing on every deploy.

Fast on purpose. If this passes, the interface loads, speaks both languages,
takes a question and shows an answer with a source — which is the whole
product. The slower matrix lives next door.
"""

from __future__ import annotations

import os

import pytest

from tests.browser.conftest import wait_for_answer, no_horizontal_overflow

#: A hosted model answers in seconds; a local one on a laptop takes a minute
#: or more for the same question. Configurable so one suite runs against
#: either without a number in the source being wrong for one of them.
ANSWER_TIMEOUT = int(os.environ.get("ADMINTRACE_ANSWER_TIMEOUT_MS", "90000"))


def test_the_landing_page_loads_and_says_what_it_is(page):
    assert page.locator(".rp-wordmark").inner_text().startswith("AdminTrace")
    assert page.locator(".rp-headline").is_visible()
    assert page.locator("#rp-question textarea").is_visible()
    assert page.locator(".rp-cat").count() >= 5
    ok, detail = no_horizontal_overflow(page)
    assert ok, detail


def test_the_language_switch_changes_the_whole_interface(page):
    page.locator(".rp-switch-site label", has_text="Français").click()
    page.wait_for_timeout(2500)
    assert page.evaluate("document.documentElement.lang") == "fr"
    text = page.locator(".contain").inner_text()
    assert "De quoi avez-vous besoin" in text
    for leak in ("What do you need", "Start with a topic", "Reply in"):
        assert leak not in text, f"English leaked into French: {leak!r}"

    page.locator(".rp-switch-site label", has_text="English").click()
    page.wait_for_timeout(2500)
    assert page.evaluate("document.documentElement.lang") == "en"
    assert "What do you need" in page.locator(".contain").inner_text()


def test_the_report_control_is_one_circular_icon_with_no_visible_text(page):
    control = page.locator(".rp-report > .label-wrap")
    box = control.bounding_box()
    assert 44 <= box["width"] <= 48 and 44 <= box["height"] <= 48
    assert page.evaluate(
        "getComputedStyle(document.querySelector('.rp-report > .label-wrap'))"
        ".borderRadius") == "50%"
    assert control.get_attribute("aria-label") == "Report a problem"
    # The label is clipped for screen readers, never painted.
    assert page.evaluate("""() => {
        const s = document.querySelector('.rp-report > .label-wrap > span:not(.icon)');
        const r = s.getBoundingClientRect();
        return r.width <= 2 && r.height <= 2;
    }""")


@pytest.mark.needs_answers
def test_asking_a_question_produces_an_answer_with_a_source(page):
    page.fill("#rp-question textarea",
              "What are the admission requirements at Montpellier Business School?")
    page.click("button.rp-submit")
    page.wait_for_selector(".rp-thread .rp-turn", timeout=15_000)
    wait_for_answer(page, ANSWER_TIMEOUT)

    badge = page.locator(".rp-answer .rp-badge").first.inner_text()
    assert "Source" in badge or "official page" in badge

    # Sources are folded away by default; opening them is part of the
    # affordance, so the test opens them rather than reading hidden text.
    toggle = page.locator(".rp-sources-toggle").first
    assert toggle.is_visible(), "the sources disclosure should be offered"
    toggle.click()
    page.wait_for_timeout(400)

    # Cited from the school itself, not a government page standing in for it.
    domains = page.locator(".rp-source-id").all_text_contents()
    assert any("mbs-education.com" in d for d in domains), domains
    assert page.locator(".rp-freshness").count() >= 1


def test_the_composer_becomes_a_follow_up_bar_once_there_is_a_conversation(page):
    page.fill("#rp-question textarea", "What counts as proof of address?")
    page.click("button.rp-submit")
    page.wait_for_selector(".rp-thread .rp-turn", timeout=15_000)
    page.wait_for_timeout(1200)
    assert page.evaluate("document.body.classList.contains('rp-answered')")
    placeholder = page.get_attribute("#rp-question textarea", "placeholder")
    assert "follow-up" in (placeholder or "").lower()
    assert page.locator("button.rp-restart").is_visible()


@pytest.mark.parametrize("width", [320, 768, 1440])
def test_the_smoke_widths_do_not_overflow(sized, width):
    page = sized(width)
    ok, detail = no_horizontal_overflow(page)
    assert ok, f"{width}px: {detail}"
