"""The clarification loop, driven through the real interface.

One bug lives here: someone asks how to validate their visa, is asked where
in France they are, answers "i live in antibes", and is asked where in France
they are. These tests fail if that ever comes back.

Slower than the smoke suite because each case is a whole conversation — two
questions, two answers, and a model call for each. Worth it: no unit test can
prove the state actually survives the round trip through Gradio.
"""

from __future__ import annotations

import os
import time

import pytest

# Every case here ends in an answer from the model.
pytestmark = pytest.mark.needs_answers

#: A hosted model answers in seconds; a local one on a laptop can take a
#: minute or more for the same question. Configurable so the same suite runs
#: against either without a number in the source being wrong for one of them.
ANSWER_TIMEOUT = int(os.environ.get("ADMINTRACE_ANSWER_TIMEOUT_MS", "120000"))

#: What the page shows when the model provider has cut us off. Anything
#: matching this is somebody else's quota, not a fault in the conversation
#: state, and a test that fails on it teaches nobody anything — the same
#: reasoning that makes the network tests opt-in.
_PROVIDER_DOWN = ("rate limit", "limite de d", "something went wrong while "
                  "preparing", "quota")


def skip_if_the_provider_is_down(page) -> None:
    text = page.locator(".rp-thread").inner_text().lower()
    if any(sign in text for sign in _PROVIDER_DOWN):
        pytest.skip("the model provider is rate-limited; the conversation "
                    "state cannot be observed through it right now")


def ask(page, text: str) -> None:
    page.fill("#rp-question textarea", text)
    page.click("button.rp-submit")
    # Generous: Gradio queues requests, and a generator from an earlier test
    # whose page has closed can still be finishing server-side. That is a
    # queue waiting its turn, not the interface failing to respond.
    page.wait_for_selector(".rp-thread .rp-turn", timeout=60_000)


def settle(page, *, expect_clarify: bool = False) -> str:
    """Wait for whatever the turn resolves into, and return the thread text."""
    if expect_clarify:
        page.wait_for_selector(".rp-clarify, .rp-limit, .rp-error",
                               timeout=ANSWER_TIMEOUT)
    else:
        page.wait_for_selector(".rp-answer, .rp-clarify, .rp-limit, .rp-error",
                               timeout=ANSWER_TIMEOUT)
    page.wait_for_timeout(1200)
    skip_if_the_provider_is_down(page)
    return page.locator(".rp-thread").inner_text()


def answered(page, extra: str = "") -> None:
    """Wait for a real answer, or stand aside if the provider is refusing us."""
    page.wait_for_selector(
        ".rp-answer, .rp-limit, .rp-error" + (f", {extra}" if extra else ""),
        timeout=ANSWER_TIMEOUT)
    page.wait_for_timeout(600)
    skip_if_the_provider_is_down(page)
    page.wait_for_selector(".rp-answer" + (f", {extra}" if extra else ""),
                           timeout=ANSWER_TIMEOUT)


def finished(page) -> None:
    """Wait until the turn has stopped streaming.

    The action bar is rendered only once a turn is complete, so it is the
    one honest "done" signal. Counting clarifications while an answer is
    still arriving reads a half-rendered thread and fails for a reason that
    has nothing to do with the conversation state.
    """
    page.wait_for_selector(".rp-actions", timeout=ANSWER_TIMEOUT)
    page.wait_for_timeout(600)


def clarifications(page) -> int:
    return page.locator(".rp-clarify").count()


# --------------------------------------------------------------- §31 visa --

def test_answering_where_you_live_resumes_the_visa_question(page):
    """The reported bug, end to end."""
    ask(page, "how to validated our visa in france")
    first = settle(page, expect_clarify=True)
    assert clarifications(page) == 1
    assert "where" in first.lower() or "wherever" in first.lower()

    ask(page, "i live in antibes")
    answered(page)
    finished(page)

    # The whole point: not asked a second time.
    assert clarifications(page) == 1, \
        "the location was requested again after being given"

    # Case-insensitively: the reader typed "antibes", and the thread shows
    # their words as they typed them.
    thread = page.locator(".rp-thread").inner_text().lower()
    assert "antibes" in thread or "alpes-maritimes" in thread, \
        "the place the reader gave is not reflected anywhere"
    assert page.locator(".rp-answer").count() >= 1


def test_the_resumed_answer_is_about_the_visa_not_about_antibes(page):
    ask(page, "how to validated our visa in france")
    settle(page, expect_clarify=True)
    ask(page, "i live in antibes")
    answered(page)
    finished(page)

    answer = page.locator(".rp-answer").last.inner_text().lower()
    assert any(word in answer for word in
               ("visa", "séjour", "sejour", "permit", "titre", "validate",
                "validation", "prefecture", "préfecture", "anef")), answer[:300]


# ---------------------------------------------------------- §32 free tram --

def test_a_free_tram_question_asks_which_city(page):
    ask(page, "how to get free tram in france")
    thread = settle(page, expect_clarify=True)
    assert clarifications(page) == 1

    # And shows no page dump while it is asking.
    assert "Closest pages we found" not in thread
    assert page.locator(".rp-source").count() == 0, \
        "sources were listed under a clarification"


def test_naming_the_city_resumes_the_transport_question(page):
    ask(page, "how to get free tram in france")
    settle(page, expect_clarify=True)

    ask(page, "Antibes")
    answered(page, ".rp-source-gap")
    finished(page)
    assert clarifications(page) == 1, "the city was requested twice"

    thread = page.locator(".rp-thread").inner_text()
    assert "Closest pages we found" not in thread
    # Nothing from somewhere else, whatever the outcome was.
    for stray in ("Paris", "RSA", "revenu de solidarité"):
        assert stray not in thread, f"an unrelated source leaked in: {stray}"


# -------------------------------------------------------------- §34 switch --

def test_the_reader_can_change_the_subject_mid_clarification(page):
    ask(page, "how to validated our visa in france")
    settle(page, expect_clarify=True)

    ask(page, "Actually, how do I open a bank account?")
    page.wait_for_selector(".rp-answer, .rp-clarify, .rp-source-gap, .rp-limit",
                           timeout=ANSWER_TIMEOUT)
    page.wait_for_timeout(1200)
    skip_if_the_provider_is_down(page)

    thread = page.locator(".rp-thread").inner_text()
    assert "bank account" in thread, "the new question was swallowed"


# ----------------------------------------------------------------- §33 fr --

def test_the_whole_flow_works_in_french(page):
    page.locator(".rp-switch-site label", has_text="Français").click()
    page.wait_for_timeout(2500)

    ask(page, "Comment valider mon visa en France ?")
    settle(page, expect_clarify=True)
    assert clarifications(page) == 1

    ask(page, "Antibes")
    answered(page)
    finished(page)
    assert clarifications(page) == 1, "on redemande la ville"

    thread = page.locator(".rp-thread").inner_text()
    for leak in ("Closest pages we found", "Where in France",
                 "Start with a topic"):
        assert leak not in thread, f"English leaked into French: {leak!r}"


# ---------------------------------------------------------------- §35 MBS --

def test_the_school_question_still_reaches_the_school(page):
    """Nothing above may have cost us the institution routing."""
    ask(page, "What are the admission requirements at Montpellier Business School?")
    answered(page)
    finished(page)

    toggle = page.locator(".rp-sources-toggle").first
    toggle.click()
    page.wait_for_timeout(400)
    domains = page.locator(".rp-source-id").all_text_contents()
    assert any("mbs-education.com" in d for d in domains), domains

@pytest.mark.parametrize("said", ["antibes", "06"])
def test_a_bare_answer_with_no_sentence_around_it_is_understood(page, said):
    """The cases only the pending task can carry.

    A capitalised "Antibes" or "in antibes" also resolves when the reply is
    re-read as free text alongside the earlier turns — so those alone do not
    prove the conversation layer is doing anything. A lowercase bare
    "antibes", and a bare département number, do: neither survives being
    treated as a new question, because neither looks like a place without
    the knowledge that a place is what was asked for.
    """
    ask(page, "how to validated our visa in france")
    settle(page, expect_clarify=True)

    ask(page, said)
    answered(page, ".rp-source-gap")
    finished(page)
    assert clarifications(page) == 1, \
        f"{said!r} was not understood as the answer to the question asked"

    # Not being asked twice is not enough. The other way this fails is
    # quieter and worse: the reply is treated as a new question, "antibes"
    # is retrieved on as if it were one, and the reader gets a confident
    # answer about the wrong thing. The visa task has to still be the
    # subject.
    # Read the answer itself, not the thread: the clarification turn above
    # it already says "préfecture", so scanning the whole thread would match
    # no matter how wrong the answer was.
    answer = page.locator(".rp-answer").last.inner_text().lower()
    assert any(word in answer for word in
               ("visa", "séjour", "sejour", "titre", "permit", "prefecture",
                "préfecture", "anef", "residence")), \
        (f"the original visa task was lost when {said!r} was read as a new "
         f"question; the answer was about: {answer[:200]}")

# ------------------------------- the exact live reproduction from the report --

@pytest.mark.parametrize("said", ["antibes", "antibes france"])
def test_the_reported_conversation_exactly(page, said):
    """The conversation as it was reported, word for word.

    Reported live as: "how to validate the visa" → "Where in France are
    you?" → "antibes" → "Where in France are you?" → "antibes france" →
    "The sources don't cover this" plus a list of unrelated pages.

    Both halves of that are asserted here: the location is not requested
    twice, and no candidate list is offered in place of an answer.
    """
    ask(page, "how to validate the visa")
    first = settle(page, expect_clarify=True)
    assert clarifications(page) == 1
    assert "where in france are you" in first.lower()

    started = time.monotonic()
    ask(page, said)
    answered(page, ".rp-source-gap")
    finished(page)
    # A diagnostic, not a speed test: generous on purpose so provider jitter
    # cannot make this flaky, tight enough to catch a two-minute regression.
    conversation_response_time_ms = int((time.monotonic() - started) * 1000)
    print(f"conversation_response_time_ms={conversation_response_time_ms}")
    assert conversation_response_time_ms < 60_000

    # 1. Never asked for the same thing twice.
    assert clarifications(page) == 1, \
        f"the location was requested again after {said!r} answered it"

    # 2. The original task, not a search for the reply.
    answer = page.locator(".rp-answer").last.inner_text().lower()
    assert any(word in answer for word in
               ("visa", "vls", "séjour", "sejour", "titre", "anef", "permit",
                "validat")), f"the visa task was lost; answer: {answer[:200]}"

    # 3. No page dump, in any of its old shapes.
    thread = page.locator(".rp-thread").inner_text()
    for banned in ("Closest pages we found", "Les pages les plus proches",
                   "Search service-public.gouv.fr for this",
                   "Chercher ceci sur service-public.gouv.fr"):
        assert banned not in thread, f"the old fallback is still live: {banned}"

    # 3b. The original bug: validation is not renewal. No renewal timing may
    # appear in the answer, and the renewal page may not be cited under it.
    for renewal in ("months before", "mois avant", "before your visa expires",
                    "before it expires", "clock starts", "day you land"):
        assert renewal not in answer, f"a renewal rule reached the answer: {renewal!r}"
    shown_sources = " ".join(page.locator(".rp-source").evaluate_all(
        "els => els.map(e => e.getAttribute('href') || '')"))
    assert "Renouvellement" not in shown_sources, \
        "the renewal page was cited under a validation answer"

    # 4. Any source actually shown is about the visa, not merely nearby.
    for title in page.locator(".rp-source-title").all_text_contents():
        assert not any(stray in title.lower() for stray in
                       ("rsa", "revenu de solidarité", "taxi", "senior")), \
            f"an unrelated source was selected: {title}"


def test_the_metric_for_repeated_clarifications_stays_at_zero(page):
    """§30: the invariant, measured rather than asserted by eye."""
    from app.query.conversation import metrics

    before = metrics().get("clarification_repeated", 0)
    ask(page, "how to validate the visa")
    settle(page, expect_clarify=True)
    ask(page, "antibes")
    answered(page, ".rp-source-gap")
    finished(page)
    assert metrics().get("clarification_repeated", 0) == before == 0
