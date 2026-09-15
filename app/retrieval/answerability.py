"""What kind of "no" this is.

The gate in :mod:`app.retrieval.gate` decides one thing: whether the retrieved
passages answer the question. That is the right question for retrieval and the
wrong one for the interface, because it collapses several different situations
into a single refusal:

* the corpus genuinely does not cover this;
* the corpus could not cover it, because the question is about one named
  institution's own procedures and this corpus is public administration;
* nobody has said yet which institution the question is about.

Told apart, each has an obvious next step — look elsewhere, go to the
institution, or answer one short question. Collapsed together they produce the
same flat refusal plus a list of official-looking pages that were never about
what was asked, which reads as an answer and is not one.

This module only classifies. It retrieves nothing, calls no model, and cannot
turn a refusal into an answer.
"""

from __future__ import annotations

from enum import Enum

from app.query.entity import Resolution


class Answerability(str, Enum):
    #: The sources state what was asked.
    SUPPORTED = "supported"
    #: The sources cover part of it, and the gaps are worth naming.
    PARTIAL = "partial"
    #: The question depends on something nobody has named yet.
    NEEDS_CLARIFICATION = "needs_clarification"
    #: Nothing in the corpus addresses this.
    NOT_FOUND = "not_found"
    #: A named body's own procedures. Official pages exist; they are not here.
    WRONG_SOURCE_TYPE = "wrong_source_type"


def classify(resolution: Resolution, *, refused: bool, citations: int,
             retrieved_anything: bool = True) -> Answerability:
    """Name the situation, from what the resolver and the gate already found.

    Order matters. A question that turns on an unnamed institution is
    unanswerable whatever retrieval came back with, so that is checked before
    anything else — otherwise a lucky topical match would answer a question
    nobody has finished asking.
    """
    if resolution.needs_clarification:
        return Answerability.NEEDS_CLARIFICATION

    if not refused:
        return Answerability.SUPPORTED if citations else Answerability.PARTIAL

    # Refused, and we know which body it was about: the refusal is a statement
    # about this corpus, not about whether an answer exists anywhere.
    if resolution.institution is not None:
        return Answerability.WRONG_SOURCE_TYPE

    return Answerability.NOT_FOUND if retrieved_anything else Answerability.NOT_FOUND


def should_show_near_misses(state: Answerability) -> bool:
    """Whether "closest pages we found" helps or misleads.

    It helps only when the corpus was the right place to look and simply came
    up short. When the question was about a named institution, or was never
    finished, the nearest public-administration pages are noise wearing the
    costume of an answer.
    """
    return state is Answerability.NOT_FOUND
