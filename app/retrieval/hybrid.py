"""Merge dense and keyword results by reciprocal rank fusion.

Fusion works on ranks rather than raw scores, because a cosine similarity and
a BM25 score are not on the same scale and never will be. Rank fusion needs no
calibration between them and degrades gracefully when one retriever finds
nothing at all.
"""

from __future__ import annotations

from app.config import Settings, get_settings
from app.retrieval import dense, keyword
from app.retrieval.types import Retrieved

# Standard reciprocal rank fusion constant. Large enough that the top few
# ranks are not allowed to dominate everything below them.
RRF_K = 60


def fuse(dense_hits: list[Retrieved], keyword_hits: list[Retrieved],
         settings: Settings | None = None) -> list[Retrieved]:
    settings = settings or get_settings()
    merged: dict[str, Retrieved] = {}

    for hit in dense_hits:
        merged[hit.chunk_id] = hit

    for hit in keyword_hits:
        existing = merged.get(hit.chunk_id)
        if existing is None:
            merged[hit.chunk_id] = hit
        else:
            existing.keyword_score = hit.keyword_score
            existing.keyword_rank = hit.keyword_rank
            existing.sources = sorted(set(existing.sources) | {"keyword"})

    for hit in merged.values():
        score = 0.0
        if hit.dense_rank:
            score += settings.dense_weight / (RRF_K + hit.dense_rank)
        if hit.keyword_rank:
            score += settings.keyword_weight / (RRF_K + hit.keyword_rank)
        hit.fused_score = score

    return sorted(merged.values(), key=lambda h: -h.fused_score)


def search(query: str, limit: int | None = None, pool: int = 25,
           settings: Settings | None = None) -> list[Retrieved]:
    """Retrieve with both strategies, fuse, and score everything comparably."""
    settings = settings or get_settings()
    limit = limit or settings.retrieval_k

    dense_hits = dense.search(query, pool, settings)
    keyword_hits = keyword.search(query, pool, settings)
    fused = fuse(dense_hits, keyword_hits, settings)[:limit]

    # Passages that only keyword search found still need a semantic score, so
    # that the gate judges every candidate on the same scale.
    missing = [h.chunk_id for h in fused if h.dense_rank is None]
    if missing:
        scores = dense.similarity_for(missing, query, settings)
        for hit in fused:
            if hit.dense_rank is None:
                hit.dense_score = scores.get(hit.chunk_id, 0.0)

    return fused


def _rrf(ranked_lists: list[tuple[str, list[Retrieved]]],
         settings: Settings) -> dict[str, Retrieved]:
    """Fuse any number of ranked lists.

    Reciprocal rank fusion is defined over a set of ranked lists, so the
    English and French forms of a query contribute their own lists rather than
    having their scores averaged. Combining pre-computed fusion scores from
    separate runs, as an earlier version did, compares numbers that were never
    on the same scale and measurably destabilised the ordering.
    """
    merged: dict[str, Retrieved] = {}
    scores: dict[str, float] = {}

    for kind, hits in ranked_lists:
        weight = settings.dense_weight if kind == "dense" else settings.keyword_weight
        for rank, hit in enumerate(hits, start=1):
            existing = merged.get(hit.chunk_id)
            if existing is None:
                merged[hit.chunk_id] = hit
                existing = hit
            else:
                existing.sources = sorted(set(existing.sources) | set(hit.sources))
                if hit.dense_score > existing.dense_score:
                    existing.dense_score = hit.dense_score
                if hit.keyword_score > existing.keyword_score:
                    existing.keyword_score = hit.keyword_score
            if kind == "dense":
                existing.dense_rank = min(existing.dense_rank or rank, rank)
            else:
                existing.keyword_rank = min(existing.keyword_rank or rank, rank)
            scores[hit.chunk_id] = scores.get(hit.chunk_id, 0.0) + weight / (RRF_K + rank)

    for chunk_id, score in scores.items():
        merged[chunk_id].fused_score = score
    return merged


def _diversify(hits: list[Retrieved], limit: int, per_document: int) -> list[Retrieved]:
    """Cap how many passages one document may occupy.

    Without this, a handful of near-identical sections of the same fiche fill
    every slot, so the answer is built from one document's wording repeated
    rather than from the range of sources that actually bear on the question.
    """
    kept: list[Retrieved] = []
    seen: dict[str, int] = {}
    overflow: list[Retrieved] = []
    for hit in hits:
        document = hit.metadata.get("fiche_id", hit.chunk_id)
        if seen.get(document, 0) < per_document:
            seen[document] = seen.get(document, 0) + 1
            kept.append(hit)
        else:
            overflow.append(hit)
        if len(kept) == limit:
            return kept
    # Backfill only if diversity left the result short.
    return (kept + overflow)[:limit]


def search_many(queries: list[str], limit: int | None = None, pool: int = 25,
                settings: Settings | None = None) -> list[Retrieved]:
    """Search with several phrasings of one question and fuse the lot."""
    settings = settings or get_settings()
    limit = limit or settings.retrieval_k

    ranked_lists: list[tuple[str, list[Retrieved]]] = []
    for query in queries:
        ranked_lists.append(("dense", dense.search(query, pool, settings)))
        ranked_lists.append(("keyword", keyword.search(query, pool, settings)))

    merged = _rrf(ranked_lists, settings)

    # Fusion decides WHICH passages are considered, not in WHAT ORDER.
    #
    # Keyword search earns its place by recall: it finds passages carrying an
    # exact French term that semantic search blurred past. It is a poor judge
    # of rank, because it scores any passage sharing the words — asked how to
    # open a bank account, BM25 rates the sole-trader and joint-account pages
    # as highly as the personal one, and rank fusion then lets that outvote a
    # much stronger semantic match. Observed: the personal-account page fell
    # from second to fifth and the page on your legal right to an account —
    # the one thing a newcomer refused by a bank most needs — fell out of the
    # results entirely.
    #
    # So the pool is fused, every candidate is scored on the one comparable
    # scale, and that score orders them.
    pool = sorted(merged.values(),
                  key=lambda h: (-h.fused_score, -h.dense_score))[:pool]

    missing = [h.chunk_id for h in pool if h.dense_rank is None]
    if missing:
        scores = dense.similarity_for(missing, queries[0], settings)
        for hit in pool:
            if hit.dense_rank is None:
                hit.dense_score = max(hit.dense_score, scores.get(hit.chunk_id, 0.0))

    ordered = sorted(pool, key=lambda h: (-h.dense_score, -h.fused_score))
    return _diversify(ordered, limit, settings.max_passages_per_document)
