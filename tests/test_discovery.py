"""Finding the page that answers, not the page that matches.

The failure these exist to stop is subtle and was real: a press release about
last year's admission round shares every important word with a question about
admission requirements, and used to outrank the page that actually lists them.
"""

from __future__ import annotations

import pytest

from app.sources import purpose
from app.sources.live import score_candidate, wants_current_information
from app.sources.registry import by_id


@pytest.fixture(scope="module")
def mbs():
    source = by_id("mbs")
    assert source is not None
    return source


# -------------------------------------------------------------- purpose ---

@pytest.mark.parametrize("question,expected", [
    ("What do I need for admission to this school?", "admission"),
    ("How much are the tuition fees?", "tuition"),
    ("When is the application deadline?", "deadline"),
    ("What proof of address do I need?", "documents"),
    ("How do I renew my titre de séjour?", "residence_permit"),
])
def test_a_question_reveals_what_the_reader_is_trying_to_do(question, expected):
    assert expected in [p.id for p in purpose.detect(question)]


def test_a_question_about_nothing_in_particular_has_no_purpose():
    assert purpose.detect("hello there") == ()


@pytest.mark.parametrize("url,expected", [
    ("https://x.fr/admissions/", "admissions"),
    ("https://x.fr/programmes-formations/bachelor/admissions-bachelor/", "admissions"),
    ("https://x.fr/actualites/resultats-du-jury/", "news"),
    ("https://x.fr/evenements/portes-ouvertes/", "event"),
    ("https://x.fr/financement-bachelor/", "fees"),
    ("https://x.fr/contact/", "contact"),
    ("https://x.fr/candidature/", "application_portal"),
])
def test_a_url_reveals_what_the_page_is_for(url, expected):
    assert purpose.classify(url) == expected


def test_a_page_type_is_read_from_the_title_when_the_path_says_nothing():
    assert purpose.classify("https://x.fr/p/12", title="Conditions d'admission") \
        == "admissions"


def test_editorial_pages_are_recognised_as_such():
    assert purpose.is_editorial("news")
    assert purpose.is_editorial("event")
    assert purpose.is_editorial("marketing")
    assert not purpose.is_editorial("admissions")
    assert not purpose.is_editorial("requirements")


# --------------------------------------------------------------- ranking --

def test_an_admissions_page_outranks_a_news_item_about_admissions(mbs):
    """The exact case that used to go wrong."""
    found = purpose.detect("What are the admission requirements?")
    admissions, _ = score_candidate(
        "https://www.mbs-education.com/programmes-formations/bachelor/admissions-bachelor/",
        anchor="Conditions d'admission", found=found, source=mbs)
    news, news_type = score_candidate(
        "https://www.mbs-education.com/actualites/sigem-2020-resultats-du-jury-dadmission/",
        anchor="SIGEM 2020 : résultats du jury d'admission", found=found, source=mbs)
    assert news_type == "news"
    assert admissions > news
    assert news < 0, "a press release should be pushed below the fold, not merely ranked lower"


def test_a_dated_announcement_is_the_right_document_for_what_changed_recently(mbs):
    found = purpose.detect("has the admission rule changed recently?")
    url = "https://www.mbs-education.com/actualites/nouvelles-modalites-admission/"
    without, _ = score_candidate(url, found=found, source=mbs, wants_news=False)
    with_news, _ = score_candidate(url, found=found, source=mbs, wants_news=True)
    assert with_news > without


def test_the_sites_own_label_for_a_link_counts_most(mbs):
    """A menu entry reading "Admissions" is the clearest signal a site gives."""
    found = purpose.detect("admission requirements")
    labelled, _ = score_candidate("https://www.mbs-education.com/p/91",
                                  anchor="Conditions d'admission",
                                  found=found, source=mbs)
    bare, _ = score_candidate("https://www.mbs-education.com/p/91",
                              found=found, source=mbs)
    assert labelled > bare


def test_a_fees_page_wins_a_question_about_cost(mbs):
    found = purpose.detect("how much does it cost?")
    fees, fees_type = score_candidate(
        "https://www.mbs-education.com/programmes-formations/bachelor/financement-bachelor/",
        anchor="Financement", found=found, source=mbs)
    generic, _ = score_candidate("https://www.mbs-education.com/a-propos/",
                                 found=found, source=mbs)
    assert fees_type == "fees"
    assert fees > generic


def test_the_application_portal_ranks_below_the_page_that_explains(mbs):
    """Where you do it is not what it requires."""
    found = purpose.detect("what do I need to apply?")
    portal, portal_type = score_candidate("https://www.mbs-education.com/candidature/",
                                          anchor="Candidater", found=found, source=mbs)
    explains, _ = score_candidate("https://www.mbs-education.com/admissions/",
                                  anchor="Conditions d'admission",
                                  found=found, source=mbs)
    assert portal_type == "application_portal"
    assert explains > portal


def test_deeply_buried_pages_are_mildly_penalised(mbs):
    found = purpose.detect("admission")
    shallow, _ = score_candidate("https://www.mbs-education.com/admissions/",
                                 found=found, source=mbs, depth=1)
    deep, _ = score_candidate(
        "https://www.mbs-education.com/a/b/c/d/e/f/g/admissions/",
        found=found, source=mbs, depth=2)
    assert shallow > deep


# ------------------------------------------------------------ currentness --

@pytest.mark.parametrize("question", [
    "has the rule changed recently?",
    "what are the current requirements?",
    "quelles sont les conditions actuelles ?",
    "is this still the case?",
])
def test_questions_about_now_ask_for_a_live_read(question):
    assert wants_current_information(question)


@pytest.mark.parametrize("question", [
    "what documents do I need?",
    "quels papiers faut-il ?",
])
def test_ordinary_questions_are_happy_with_a_cached_read(question):
    assert not wants_current_information(question)
