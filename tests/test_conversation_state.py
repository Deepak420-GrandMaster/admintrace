"""A clarification reply is an answer, not a new question.

The bug these exist to keep dead: ask how to validate a visa, get asked where
in France you are, answer "i live in antibes", and get asked where in France
you are. Every test here is a shape of reply somebody actually types.
"""

from __future__ import annotations

import pytest

from app.query.conversation import (
    DEFAULT_EXPIRY_TURNS, Field, PendingTask, Status, looks_like_a_new_question,
    may_ask_for, place_of, resolve_clarification_response, resumed_question,
    start,
)
from app.sources.jurisdiction import resolve, resolve_reply
from app.sources.route import plan


def visa_task() -> PendingTask:
    return start("how to validated our visa in france",
                 requested=Field.LOCATION, intent="residence_permit")


# ------------------------------------------------------ the original bug ---

def test_antibes_answers_the_location_question():
    """A. The exact conversation from the bug report."""
    task = visa_task()
    reply = resolve_clarification_response(task, "i live in antibes")

    assert reply.resume, reply.note
    assert reply.filled == Field.LOCATION.value
    assert reply.task.context["location"] == "Antibes"
    assert reply.task.context["department"] == "Alpes-Maritimes"
    # The original task survived the clarification turn.
    assert "visa" in reply.question


def test_the_location_is_never_asked_for_twice():
    """The hard invariant. Everything else here is a way of reaching it."""
    task = visa_task()
    reply = resolve_clarification_response(task, "i live in antibes")
    assert not may_ask_for(reply.task, Field.LOCATION)

    # And the router agrees: with the place known, it stops asking.
    routing = plan(reply.question, place=place_of(reply.task))
    assert not routing.needs_place


@pytest.mark.parametrize("said", [
    "Antibes",            # B. bare, capitalised
    "antibes",            # lowercase, as people type
    "Antibes France",     # B. with the country appended
    "i live in antibes",
    "near Antibes",
    "Alpes-Maritimes",    # C. the département itself
    "06",                 # the département number
])
def test_every_way_of_saying_where_you_are(said):
    reply = resolve_clarification_response(visa_task(), said)
    assert reply.resume, f"{said!r} was not understood: {reply.note}"
    assert reply.task.context["department"] == "Alpes-Maritimes"


def test_a_one_word_answer_is_enough():
    """§7: the reader must not have to write a sentence."""
    for city in ("Antibes", "Nice", "Paris", "Montpellier"):
        reply = resolve_clarification_response(visa_task(), city)
        assert reply.resume, f"{city} alone was not accepted"


def test_an_ambiguous_commune_asks_rather_than_guessing():
    """Saint-Denis is in 93 and in 974. Picking one would be a guess."""
    reply = resolve_clarification_response(visa_task(), "Saint-Denis")
    assert not reply.resume
    assert reply.unresolved
    assert "more than one" in reply.note


# ------------------------------------------------------- the other fields --

def test_naming_a_school_answers_the_entity_question():
    """D."""
    task = start("what are the admission requirements",
                 requested=Field.ENTITY, intent="admission")
    reply = resolve_clarification_response(task, "MBS")
    assert reply.resume, reply.note
    assert reply.task.context["institution_id"]
    assert "MBS" in reply.task.context["institution_name"] \
        or "Montpellier" in reply.task.context["institution_name"]


def test_yes_answers_a_yes_or_no_question():
    """E. "yes" is an answer; it is never a thing to go and retrieve."""
    task = start("do I need a residence permit", requested=Field.USER_STATUS)
    task.context["status_if_yes"] = "student"
    reply = resolve_clarification_response(task, "yes")
    assert reply.resume, reply.note
    assert reply.task.context["user_status"] == "student"
    # The word itself must not reach the search.
    assert "yes" not in resumed_question(reply.task).lower().split()


def test_saying_student_answers_it_too():
    task = start("do I need a residence permit", requested=Field.USER_STATUS)
    reply = resolve_clarification_response(task, "I'm a student")
    assert reply.resume
    assert reply.task.context["user_status"] == "student"


def test_a_date_answers_the_date_question():
    """F."""
    task = start("what are the fees", requested=Field.DATE)
    reply = resolve_clarification_response(task, "September 2026")
    assert reply.resume, reply.note
    assert "2026" in reply.task.context["date"]


# ---------------------------------------------------------- multi-turn -----

def test_a_second_question_does_not_lose_the_first_answer():
    """§6: location must survive being asked something else."""
    task = start("I need to renew my residence permit",
                 requested=Field.LOCATION, intent="residence_permit")
    first = resolve_clarification_response(task, "Antibes.")
    assert first.resume

    # Now ask a different field on the same task.
    carried = first.task
    carried.requested_clarification = Field.USER_STATUS.value
    carried.missing_fields = [Field.USER_STATUS.value]
    carried.status = Status.AWAITING_CLARIFICATION.value

    second = resolve_clarification_response(carried, "yes")
    assert second.resume
    assert second.task.context["location"] == "Antibes", "location was lost"
    assert second.task.context["user_status"]
    assert not may_ask_for(second.task, Field.LOCATION)


# ------------------------------------------------------- changing the topic --

def test_the_reader_can_change_the_subject():
    """G. A new question is a new question, not a location."""
    reply = resolve_clarification_response(
        visa_task(), "Actually, how do I open a bank account?")
    assert reply.switched
    assert reply.task is None
    assert "bank account" in reply.question


def test_a_plain_new_question_during_clarification_switches_too():
    reply = resolve_clarification_response(
        visa_task(), "How do I open a bank account in France?")
    assert reply.switched


def test_a_short_reply_is_never_mistaken_for_a_new_question():
    """The guard must not swing the other way and lose ordinary answers."""
    for said in ("Antibes", "Antibes France", "06", "yes", "Nice please"):
        assert not looks_like_a_new_question(said), said


def test_a_pending_task_expires_rather_than_nagging():
    task = visa_task()
    for _ in range(DEFAULT_EXPIRY_TURNS):
        outcome = resolve_clarification_response(task, "hmm")
    assert outcome.switched
    assert outcome.task is None


# --------------------------------------------------------- resumed query ----

def test_the_resumed_question_is_the_original_question():
    """The place must not be glued onto the query.

    "how to validate the visa Antibes" retrieves worse than "how to validate
    the visa": the corpus has no pages about Antibes, so the city only
    dilutes what the question is about. Where the reader is belongs to
    routing, and travels as a Place.
    """
    from app.query.conversation import retrieval_context

    reply = resolve_clarification_response(visa_task(), "Antibes")
    assert "visa" in reply.question, "the original task was dropped"
    assert "Antibes" not in reply.question, \
        "the place was glued onto the semantic query"

    # It is not lost — it is structured.
    location = retrieval_context(reply.task)["location"]
    assert location["city"] == "Antibes"
    assert location["department"] == "Alpes-Maritimes"
    assert location["department_code"] == "06"
    assert location["region"] == "Provence-Alpes-Côte d'Azur"
    assert location["country"] == "France"


def test_an_institution_does_stay_in_the_question():
    """An entity changes what is being asked, not merely where."""
    task = start("what are the entry requirements", requested=Field.ENTITY)
    reply = resolve_clarification_response(task, "MBS")
    assert "requirements" in reply.question
    assert "MBS" in reply.question or "Montpellier" in reply.question


def test_a_place_already_given_is_rebuilt_without_reparsing_text():
    reply = resolve_clarification_response(visa_task(), "Antibes")
    place = place_of(reply.task)
    assert place.known
    assert place.department.name == "Alpes-Maritimes"
    # No registered préfecture source for 06 — and none is invented.
    assert place.authority_source_id == ""


def test_a_department_we_cannot_read_still_names_its_authority():
    """Knowing where Antibes is, is not the same as having read the page."""
    reply = resolve_clarification_response(visa_task(), "Antibes")
    place = place_of(reply.task)
    assert place.authority_name == "Préfecture des Alpes-Maritimes"
    assert place.authority_source_id == ""
