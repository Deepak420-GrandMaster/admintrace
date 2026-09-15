"""Who the question is about, and what kind of "no" a refusal is.

The failure these guard against is specific and was real: a question about one
named institution retrieved on its leftover words, matched generic
public-administration pages, and presented them as the nearest answer. They
are official pages, so nothing in the reply signals that it missed.
"""

from __future__ import annotations

import pytest

from app.directory.universities import search
from app.query.entity import (Resolution, mentions_institution_reference,
                              named_institutions, resolve)
from app.retrieval.answerability import (Answerability, classify,
                                         should_show_near_misses)


@pytest.fixture(scope="module")
def mbs():
    hits = search("Montpellier Business School", limit=1)
    if not hits:
        pytest.skip("institution register is not available in this environment")
    return hits[0]


# --------------------------------------------------------- naming a body --

def test_an_institution_named_in_full_is_found(mbs):
    found = named_institutions("I want to apply to Montpellier Business School")
    assert found and found[0].name == mbs.name


def test_an_institution_named_by_acronym_is_found(mbs):
    found = named_institutions("Is MBS hard to get into?")
    assert found and found[0].name == mbs.name


def test_an_ordinary_question_names_no_institution():
    assert named_institutions("How do I renew my residence permit?") == []
    assert named_institutions("What counts as proof of address?") == []


def test_a_capitalised_question_word_is_not_an_institution():
    """"How" and "France" start sentences; they are not schools."""
    assert named_institutions("How do I do this in France?") == []


# ------------------------------------------------------- the reference ----

@pytest.mark.parametrize("question", [
    "how to get admission in this school",
    "what does that university require",
    "comment entrer dans cette école",
    "quels sont les frais de cet établissement",
])
def test_a_reference_that_needs_context_is_recognised(question):
    assert mentions_institution_reference(question)


@pytest.mark.parametrize("question", [
    "How do I renew my residence permit?",
    "What deposit can a landlord ask for?",
])
def test_a_self_contained_question_needs_no_context(question):
    assert not mentions_institution_reference(question)


# ------------------------------------------------------------ resolving ---

def test_this_school_resolves_to_the_school_established_earlier(mbs):
    """The regression case: the school was named, then referred to."""
    resolution = resolve("how to get admission in this school",
                         ["I am applying to Montpellier Business School"])
    assert resolution.institution is not None
    assert resolution.institution.name == mbs.name
    assert resolution.from_context
    assert not resolution.needs_clarification


def test_an_acronym_established_earlier_also_carries(mbs):
    resolution = resolve("how do I apply to this school", ["Tell me about MBS"])
    assert resolution.institution is not None
    assert resolution.institution.name == mbs.name


def test_an_unestablished_reference_asks_rather_than_guesses():
    resolution = resolve("how to get admission in this school", [])
    assert resolution.needs_clarification
    assert resolution.institution is None


def test_naming_a_school_now_beats_one_named_before(mbs):
    """Naming something is how you change the subject."""
    resolution = resolve("and Montpellier Business School?",
                         ["I was looking at HEC"])
    assert resolution.institution is not None
    assert resolution.institution.name == mbs.name
    assert not resolution.from_context


def test_an_ordinary_question_is_not_about_an_institution():
    resolution = resolve("How do I renew my residence permit?", [])
    assert not resolution.is_about_institution
    assert resolution.institution is None
    assert not resolution.needs_clarification


def test_context_does_not_leak_into_an_unrelated_question(mbs):
    """A school named earlier must not capture a question that is not about one."""
    resolution = resolve("What counts as proof of address?",
                         ["I am applying to Montpellier Business School"])
    assert resolution.institution is None


# ------------------------------------------------------- answerability ----

def test_an_unnamed_reference_is_a_question_not_a_refusal():
    state = classify(Resolution(needs_clarification=True),
                     refused=False, citations=9)
    assert state is Answerability.NEEDS_CLARIFICATION


def test_a_refusal_about_a_named_body_is_the_wrong_corpus_not_a_dead_end(mbs):
    state = classify(Resolution(institution=mbs), refused=True, citations=0)
    assert state is Answerability.WRONG_SOURCE_TYPE


def test_a_refusal_with_no_body_in_play_is_simply_not_found():
    assert classify(Resolution(), refused=True, citations=0) is Answerability.NOT_FOUND


def test_an_answer_with_sources_is_supported():
    assert classify(Resolution(), refused=False, citations=2) is Answerability.SUPPORTED


def test_an_answer_without_sources_is_only_partial():
    assert classify(Resolution(), refused=False, citations=0) is Answerability.PARTIAL


def test_closest_pages_are_offered_only_when_this_was_the_right_corpus(mbs):
    """Generic pages under an institution question are noise dressed as evidence."""
    assert should_show_near_misses(Answerability.NOT_FOUND)
    assert not should_show_near_misses(Answerability.WRONG_SOURCE_TYPE)
    assert not should_show_near_misses(Answerability.NEEDS_CLARIFICATION)
