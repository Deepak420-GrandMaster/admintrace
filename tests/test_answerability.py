"""The answerability gate must reach the same verdict every time.

A question answered on one attempt and refused on the next destroys the one
thing this product sells. The model behind the judgement is not reliably
deterministic even at temperature zero, so determinism is enforced around it.
"""

from __future__ import annotations

from app.retrieval.gate import (GateDecision, _normalise, _read_verdict,
                                _verdict_key)
from app.retrieval.types import Retrieved


def passage(chunk_id: str) -> Retrieved:
    return Retrieved(chunk_id=chunk_id, text="Un passage.",
                     metadata={"fiche_id": chunk_id.split("#")[0]},
                     dense_score=0.7, dense_rank=1, sources=["dense"])


def decision(*chunk_ids: str) -> GateDecision:
    return GateDecision(passed=[passage(c) for c in chunk_ids], threshold=0.35)


def test_trivial_wording_differences_are_the_same_question():
    """Case, accents, punctuation and spacing carry no meaning here."""
    base = decision("F1#1")
    for variant in ("What proof of address do I need?",
                    "what proof of address do i need",
                    "  What  proof of address do I need ?? "):
        assert _verdict_key(variant, base) == _verdict_key(
            "What proof of address do I need?", base)


def test_accents_do_not_create_a_second_question():
    base = decision("F1#1")
    assert _verdict_key("Quels justificatifs sont acceptés ?", base) == \
        _verdict_key("Quels justificatifs sont acceptes ?", base)


def test_a_different_question_is_a_different_judgement():
    base = decision("F1#1")
    assert _verdict_key("What proof of address do I need?", base) != \
        _verdict_key("How much is the deposit?", base)


def test_different_evidence_reopens_the_question():
    """A re-index changes what was seen, so the verdict must be asked again."""
    question = "What proof of address do I need?"
    assert _verdict_key(question, decision("F1#1")) != \
        _verdict_key(question, decision("F2#1"))


def test_the_order_passages_arrive_in_does_not_matter():
    question = "What proof of address do I need?"
    assert _verdict_key(question, decision("F1#1", "F2#1")) == \
        _verdict_key(question, decision("F2#1", "F1#1"))


def test_a_clear_yes_or_no_is_read_correctly():
    assert _read_verdict("YES")[0] is True
    assert _read_verdict("NO\nThe pages do not say.")[0] is False
    assert _read_verdict("**NO** — not covered")[0] is False
    assert _read_verdict("yes, the passages cover it")[0] is True


def test_an_unreadable_reply_is_not_silently_a_refusal():
    """Denying an answer on a malformed reply punishes the reader for the model."""
    assert _read_verdict("")[0] is None
    assert _read_verdict("Maybe, hard to say")[0] is None
    assert _read_verdict("I think it depends")[0] is None
