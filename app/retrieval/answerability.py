"""What kind of "not quite" this is.

The gate in :mod:`app.retrieval.gate` decides one thing: whether the retrieved
passages answer the question. That is the right question for retrieval and the
wrong one for a reader, because it collapses situations whose only honest
responses are completely different.

The distinction that matters most, and the one this system kept getting wrong:

* the authority is reachable and does not publish this — a **source gap**;
* the authority exists and could not be read — **authority unavailable**.

They look identical from inside the retrieval code and are opposite from
outside it. Telling someone "there is no official information about this" when
the truth is "CAF is behind a bot wall today" sends them away from an answer
that exists. So each state below has its own sentence, and none of them is
allowed to borrow another's.

This module only classifies. It retrieves nothing, calls no model, and can
never turn a refusal into an answer.
"""

from __future__ import annotations

from enum import Enum

from app.query.entity import Resolution


class Answerability(str, Enum):
    #: The authoritative evidence was found and it answers the question.
    SUPPORTED = "supported"
    #: Part of the answer is supported; an authoritative part is still missing.
    PARTIAL = "partial"
    #: The right authority is known and its current page could not be read.
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    #: The right authority is readable and does not publish this.
    SOURCE_GAP = "source_gap"
    #: A source that should have been reachable was not. A fault, not a gap.
    RETRIEVAL_FAILURE = "retrieval_failure"
    #: Two authoritative sources give materially different requirements.
    CONFLICTING_SOURCES = "conflicting_sources"
    #: The question turns on something nobody has named yet.
    NEEDS_CLARIFICATION = "needs_clarification"
    #: The only evidence available is older than the freshness policy allows.
    STALE = "stale"
    #: Nothing in the corpus addresses this, and no other authority applies.
    NOT_FOUND = "not_found"
    #: A named body's own procedures. Official pages exist; not in this corpus.
    WRONG_SOURCE_TYPE = "wrong_source_type"


#: The translation key each state speaks through. Kept here so a new state
#: cannot be added without deciding what it says to a reader.
MESSAGE_KEY = {
    Answerability.AUTHORITY_UNAVAILABLE: "state_authority_unavailable",
    Answerability.SOURCE_GAP: "state_source_gap",
    Answerability.RETRIEVAL_FAILURE: "state_retrieval_failure",
    Answerability.CONFLICTING_SOURCES: "state_conflicting_sources",
    Answerability.STALE: "state_stale",
}


def classify(resolution: Resolution, *, refused: bool, citations: int,
             retrieved_anything: bool = True,
             authority_known: bool = False,
             authority_reachable: bool = True,
             conflicts: int = 0,
             stale_evidence: bool = False,
             missing_required: bool = False) -> Answerability:
    """Name the situation from what the resolver, the router and the gate found.

    Order matters, and it is the order of how badly each thing invalidates an
    answer. A question nobody has finished asking cannot be answered however
    good the evidence; two official sources contradicting each other is not
    something to average; evidence too old to trust is not evidence.
    """
    if resolution.needs_clarification:
        return Answerability.NEEDS_CLARIFICATION

    if conflicts > 0:
        return Answerability.CONFLICTING_SOURCES

    # The authority is known and could not be read. This is emphatically not
    # the same as "no official information exists", and must never be reported
    # as though it were.
    if authority_known and not authority_reachable:
        return Answerability.AUTHORITY_UNAVAILABLE

    if stale_evidence and refused:
        return Answerability.STALE

    if not refused:
        if stale_evidence:
            return Answerability.STALE
        if missing_required:
            return Answerability.PARTIAL
        return Answerability.SUPPORTED if citations else Answerability.PARTIAL

    # Refused. Which kind?
    if authority_known and authority_reachable:
        # We could read the body that owns this, and it does not say.
        return Answerability.SOURCE_GAP
    if resolution.institution is not None:
        return Answerability.WRONG_SOURCE_TYPE
    if not retrieved_anything:
        return Answerability.RETRIEVAL_FAILURE
    return Answerability.NOT_FOUND


def should_show_near_misses(state: Answerability) -> bool:
    """Whether "closest pages we found" helps or misleads.

    It helps only when the corpus was the right place to look and came up
    short. When the question was about a named body, was never finished, or
    failed for a reason that has nothing to do with coverage, the nearest
    public-administration pages are noise wearing the costume of an answer.
    """
    return state is Answerability.NOT_FOUND


def may_answer_confidently(state: Answerability) -> bool:
    """Whether the interface may present this as a settled answer."""
    return state is Answerability.SUPPORTED


def is_access_failure(state: Answerability) -> bool:
    """A problem reaching a source, as opposed to a source not covering it."""
    return state in (Answerability.AUTHORITY_UNAVAILABLE,
                     Answerability.RETRIEVAL_FAILURE,
                     Answerability.STALE)
