"""Fusion, ranking, and keyword tokenisation."""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.query.detect import detect
from app.retrieval.hybrid import fuse
from app.retrieval.keyword import fold, tokenize
from app.retrieval.types import Retrieved


def dense_hit(chunk_id: str, rank: int, score: float) -> Retrieved:
    return Retrieved(chunk_id=chunk_id, text="", metadata={},
                     dense_score=score, dense_rank=rank, sources=["dense"])


def keyword_hit(chunk_id: str, rank: int, score: float) -> Retrieved:
    return Retrieved(chunk_id=chunk_id, text="", metadata={},
                     keyword_score=score, keyword_rank=rank, sources=["keyword"])


@pytest.fixture
def settings():
    return get_settings()


def test_a_passage_found_by_both_retrievers_outranks_one_found_by_one(settings):
    fused = fuse([dense_hit("both", 2, 0.7), dense_hit("dense-only", 1, 0.8)],
                 [keyword_hit("both", 1, 12.0)], settings)
    assert fused[0].chunk_id == "both"
    assert set(fused[0].sources) == {"dense", "keyword"}


def test_keyword_only_results_are_kept(settings):
    """Exact French terms are precisely what semantic search loses."""
    fused = fuse([dense_hit("a", 1, 0.6)], [keyword_hit("b", 1, 9.0)], settings)
    assert {h.chunk_id for h in fused} == {"a", "b"}


def test_fusion_survives_one_retriever_returning_nothing(settings):
    assert [h.chunk_id for h in fuse([dense_hit("a", 1, 0.6)], [], settings)] == ["a"]
    assert [h.chunk_id for h in fuse([], [keyword_hit("b", 1, 4.0)], settings)] == ["b"]


def test_fusion_does_not_duplicate_a_passage(settings):
    fused = fuse([dense_hit("same", 1, 0.5)], [keyword_hit("same", 1, 7.0)], settings)
    assert len(fused) == 1


def test_accents_are_folded_on_both_sides():
    """Someone without a French keyboard must find the same passages."""
    assert fold("récépissé") == fold("recepisse")
    assert tokenize("Récépissé de dépôt") == tokenize("Recepisse de depot")


def test_tokenizer_drops_single_characters_but_keeps_terms():
    tokens = tokenize("Le récépissé à l'OFII")
    assert "recepisse" in tokens and "ofii" in tokens


def test_language_detection_separates_the_two_languages():
    assert detect("How do I renew my residence permit?").language == "en"
    assert detect("Comment renouveler mon titre de séjour ?").language == "fr"


def test_mixed_language_resolves_to_english():
    """Specified behaviour: ties go to English."""
    assert detect("How do I get a titre de séjour?").language == "en"
