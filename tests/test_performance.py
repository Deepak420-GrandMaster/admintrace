"""What an answer is allowed to cost.

The measurements behind these: Groq reserves ``max_tokens`` against its
per-minute budget as well as charging the prompt, so a 429 reads "Limit 8000,
Used 5445, Requested 6193". Two calls per question — one asking whether the
passages answered, one writing the answer — came to ~9,400 requested tokens
against a limit of 8,000 a minute. A single question could not fit inside its
own minute, so the next one queued and a reader waited two minutes for
nothing. These tests keep that from coming back.
"""

from __future__ import annotations

import pytest

from app.answer import evidence
from app.answer.evidence import (
    DECISIVE_DOCUMENTS, DECISIVE_SCORE, DECISIVE_SUPPORT, Verdict, assess,
)
from app.config import get_settings
from app.retrieval.types import Retrieved


def hit(score: float, fiche: str) -> Retrieved:
    return Retrieved(chunk_id=f"{fiche}-1", text="x",
                     metadata={"fiche_id": fiche, "fiche_title_fr": "t"},
                     dense_score=score)


class FakeDecision:
    def __init__(self, passed, should_refuse=False, reason=""):
        self.passed = passed
        self.rejected = []
        self.should_refuse = should_refuse
        self.reason = reason


# ------------------------------------------------- what costs a model call --

def test_strong_evidence_is_settled_without_a_model():
    """§36: a well-supported question spends one call, not two."""
    passed = [hit(DECISIVE_SCORE + 0.05, f"F{i}") for i in range(DECISIVE_SUPPORT)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ANSWER
    assert not verdict.needs_model


def test_nothing_relevant_is_refused_without_a_model():
    """A refusal the backend can already make costs nothing."""
    verdict = assess(FakeDecision([hit(0.8, "F1")]), selected=[])
    assert verdict.verdict is Verdict.REFUSE
    assert not verdict.needs_model
    assert "about something else" in verdict.detail


def test_a_gate_refusal_needs_no_second_opinion():
    decision = FakeDecision([], should_refuse=True, reason="below threshold")
    verdict = assess(decision, selected=[])
    assert verdict.verdict is Verdict.REFUSE
    assert not verdict.needs_model


def test_weak_evidence_still_asks_the_model():
    """The judgement a similarity score cannot make is still bought.

    Measured over this corpus, supported and unsupported questions overlap
    exactly on best score — "can I use a phone bill as proof of address"
    (supported) and "can I bring my dog on the TGV" (not) both score 0.58.
    So anything short of decisive goes to the model, which is the stage that
    exists for precisely this case.
    """
    passed = [hit(DECISIVE_SCORE - 0.05, f"F{i}") for i in range(DECISIVE_SUPPORT)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL
    assert verdict.needs_model


def test_one_page_repeated_is_not_corroboration():
    """Five passages from one fiche is one source, not five."""
    passed = [hit(0.9, "F1") for _ in range(DECISIVE_SUPPORT + 2)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL, \
        "one document was mistaken for several agreeing sources"


def test_a_single_strong_passage_does_not_carry_an_answer():
    passed = [hit(0.95, "F1")]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL


def test_the_decisive_bar_stays_high():
    """A regression guard on the constants themselves.

    Lowering these is how the expensive check quietly stops running on
    questions that needed it. The values came from measurement; changing
    them should mean measuring again.
    """
    assert DECISIVE_SCORE >= 0.60, "the decisive score bar was lowered"
    assert DECISIVE_SUPPORT >= 4, "fewer supporting passages now skip the check"
    assert DECISIVE_DOCUMENTS >= 2, "one document could now carry an answer"


# -------------------------------------------------------- the token budget --

def test_the_answer_ceiling_is_sized_to_the_length_policy():
    """§11: not a 2000-token budget for a 300-word answer.

    The ceiling is reserved against the provider's rate limit whether or not
    it is spent, so an over-generous one is paid for on every question.
    """
    from app.answer.generate import ANSWER_TOKEN_CEILING, EVIDENCE_PASSAGES

    assert ANSWER_TOKEN_CEILING <= 1200, "the ceiling is back above the policy"
    # 300 words plus reasoning still has to fit; measured at 546.
    assert ANSWER_TOKEN_CEILING >= 700, "too tight for a COMPLEX answer"
    assert EVIDENCE_PASSAGES <= 5, "the prompt is carrying the whole result set"


def test_the_model_is_shown_what_will_be_cited():
    """The prompt and the citations must be the same evidence.

    They were not: the model saw everything the similarity gate passed while
    the citations listed only what survived relevance, so an answer could
    lean on a page the reader was never shown.
    """
    from app.answer.generate import _evidence_for

    class D:
        passed = [hit(0.9, "F1"), hit(0.8, "F2")]
        selected = [hit(0.9, "F1")]

    shown = _evidence_for(D())
    assert [h.metadata["fiche_id"] for h in shown] == ["F1"]


# ------------------------------------------------------------- telemetry ----

def test_a_trace_times_its_phases():
    from app.telemetry import Trace

    trace = Trace()
    with trace.phase("retrieval"):
        pass
    assert "retrieval" in trace.phases
    assert trace.retrieval_time >= 0


def test_targets_exist_for_every_response_class():
    from app.answer.length import AnswerClass
    from app.telemetry import TARGET_SECONDS

    for klass in AnswerClass:
        assert klass.name in TARGET_SECONDS, klass.name


def test_provider_health_counts_what_went_wrong():
    from app.telemetry import provider_health

    health = provider_health([
        {"rate_limited": True, "retry_after": 30, "provider": "groq"},
        {"rate_limited": False, "provider": "ollama", "empty_answer": True},
        {"rate_limited": False, "provider": "groq"},
    ])
    assert health["rate_limit_count"] == 1
    assert health["fallback_count"] == 1
    assert health["empty_answers"] == 1
    assert health["rate_limit_wait_max"] == 30


def test_percentiles_are_not_nonsense_on_small_samples():
    from app.telemetry import percentile

    assert percentile([], 0.5) == 0.0
    assert percentile([4.0], 0.95) == 4.0
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0
