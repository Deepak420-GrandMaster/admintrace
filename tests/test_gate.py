"""The relevance gate, and that a refusal is really a refusal."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.retrieval.gate import apply_gate
from app.retrieval.types import Retrieved


def hit(chunk_id: str, score: float) -> Retrieved:
    return Retrieved(
        chunk_id=chunk_id,
        text="Un passage.",
        metadata={"fiche_id": chunk_id.split("#")[0], "fiche_title_fr": "Titre"},
        dense_score=score,
        dense_rank=1,
        sources=["dense"],
    )


@pytest.fixture
def settings():
    return get_settings()


def test_nothing_retrieved_is_a_refusal(settings):
    decision = apply_gate([], settings)
    assert decision.should_refuse
    assert not decision.passed
    assert decision.reason


def test_everything_below_threshold_is_a_refusal(settings):
    below = settings.relevance_threshold - 0.05
    decision = apply_gate([hit("F1#1", below), hit("F2#1", below - 0.1)], settings)
    assert decision.should_refuse
    assert not decision.passed
    assert len(decision.rejected) == 2


def test_weak_passages_never_reach_the_prompt(settings):
    """A refusal must not smuggle its rejected passages through as context."""
    below = settings.relevance_threshold - 0.05
    decision = apply_gate([hit("F1#1", below)], settings)
    assert decision.passed == []


def test_passages_below_threshold_are_dropped_from_a_passing_answer(settings):
    above = settings.relevance_threshold + 0.2
    below = settings.relevance_threshold - 0.2
    decision = apply_gate([hit("F1#1", above), hit("F2#1", below)], settings)
    assert not decision.should_refuse
    assert [c.chunk_id for c in decision.passed] == ["F1#1"]
    assert [c.chunk_id for c in decision.rejected] == ["F2#1"]


def test_threshold_boundary_is_inclusive(settings):
    decision = apply_gate([hit("F1#1", settings.relevance_threshold)], settings)
    assert not decision.should_refuse


def test_decision_reports_the_scores_it_used(settings):
    """The debug panel and the eval both depend on this being truthful."""
    decision = apply_gate([hit("F1#1", 0.81), hit("F2#1", 0.42)], settings)
    assert decision.best_score == pytest.approx(0.81)
    assert decision.threshold == settings.relevance_threshold
    assert len(decision.all_candidates) == 2
