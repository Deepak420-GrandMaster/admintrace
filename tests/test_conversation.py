"""Conversation behaviour: what carries over, and what must not.

A conversation on screen promises the assistant remembers. Retrieval does not,
so these tests pin down exactly which questions are allowed to inherit the one
before them.
"""

from __future__ import annotations

import pytest

from app.answer.generate import classify_followup, resolve_followup

PREVIOUS = "I need to renew my residence permit."

CONTINUATIONS = [
    "What documents do I need?",          # names no subject of its own
    "What about proof of address?",       # elliptical opener
    "And what if I cannot get an appointment?",
    "How much does it cost?",
    "Et les papiers ?",
    "Combien ça coûte ?",
    "Quels documents faut-il fournir ?",
    "Où dois-je aller ?",
]

NEW_TOPICS = [
    "What is the weather in Paris?",       # a subject of its own, off-domain
    "How much deposit can a landlord ask for?",
    "Comment ouvrir un compte bancaire ?",
    "What is a récépissé?",
    "Quel temps fait-il à Paris ?",
]


@pytest.mark.parametrize("question", CONTINUATIONS)
def test_questions_without_a_subject_carry_the_previous_one(question):
    assert classify_followup(question, PREVIOUS) == "CONTINUATION"
    expanded, carried = resolve_followup(question, PREVIOUS)
    assert carried
    assert PREVIOUS in expanded and question in expanded


@pytest.mark.parametrize("question", NEW_TOPICS)
def test_questions_with_their_own_subject_stand_alone(question):
    """Dragging the last topic into these would answer the wrong question."""
    assert classify_followup(question, PREVIOUS) == "NEW_TOPIC"
    expanded, carried = resolve_followup(question, PREVIOUS)
    assert not carried
    assert expanded == question


def test_the_first_question_of_a_conversation_inherits_nothing():
    assert classify_followup("What documents do I need?", None) == "NEW_TOPIC"
    assert resolve_followup("What documents do I need?", None) == (
        "What documents do I need?", False)


def test_starting_over_clears_the_context():
    """Start over passes no previous question, so nothing can leak into it."""
    question = "What documents can I use as proof of address?"
    assert resolve_followup(question, None)[0] == question


def test_empty_and_whitespace_questions_are_handled():
    assert resolve_followup("", PREVIOUS) == ("", False)
    assert resolve_followup("   ", PREVIOUS)[0] == ""


def test_a_very_long_question_is_not_truncated_or_expanded():
    long_question = "How do I renew my residence permit " + "in detail " * 60 + "?"
    expanded, carried = resolve_followup(long_question, PREVIOUS)
    assert not carried
    assert expanded == long_question.strip()
