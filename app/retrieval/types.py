"""The shape of a retrieval result, shared by every retriever."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Retrieved:
    """One candidate passage and how each retriever judged it."""

    chunk_id: str
    text: str
    metadata: dict

    # Cosine similarity against the query, 0 to 1. This is the interpretable
    # number, and the one the relevance gate uses.
    dense_score: float = 0.0
    # BM25 score. Unbounded and corpus-relative, so it ranks but never gates.
    keyword_score: float = 0.0
    # Position in each retriever's own ordering, 1-based; None if unranked.
    dense_rank: int | None = None
    keyword_rank: int | None = None
    # Reciprocal rank fusion score, used for ordering only.
    fused_score: float = 0.0
    sources: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.metadata.get("fiche_title_fr", "")

    @property
    def url(self) -> str:
        return self.metadata.get("source_url", "")
