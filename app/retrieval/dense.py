"""Vector search over the embedded corpus."""

from __future__ import annotations

from app.config import Settings, get_settings
from app.ingest.embed import embed_query, get_collection
from app.retrieval.types import Retrieved


def search(query: str, limit: int = 20,
           settings: Settings | None = None) -> list[Retrieved]:
    settings = settings or get_settings()
    collection = get_collection(settings)
    if collection.count() == 0:
        return []

    vector = embed_query(query, settings)
    response = collection.query(
        query_embeddings=[vector],
        n_results=min(limit, collection.count()),
        include=["documents", "metadatas", "distances"],
    )

    results: list[Retrieved] = []
    ids = response["ids"][0]
    for rank, chunk_id in enumerate(ids, start=1):
        # Chroma reports cosine distance; similarity is its complement.
        distance = response["distances"][0][rank - 1]
        results.append(
            Retrieved(
                chunk_id=chunk_id,
                text=response["documents"][0][rank - 1],
                metadata=response["metadatas"][0][rank - 1],
                dense_score=max(0.0, 1.0 - float(distance)),
                dense_rank=rank,
                sources=["dense"],
            )
        )
    return results


def similarity_for(chunk_ids: list[str], query: str,
                   settings: Settings | None = None) -> dict[str, float]:
    """Cosine similarity for specific chunks.

    Needed for candidates that only keyword search found: the gate judges
    everything on the same interpretable scale, so a passage must not skip it
    merely because of which retriever surfaced it.
    """
    settings = settings or get_settings()
    if not chunk_ids:
        return {}
    collection = get_collection(settings)
    stored = collection.get(ids=chunk_ids, include=["embeddings"])
    vector = embed_query(query, settings)
    scores: dict[str, float] = {}
    for chunk_id, embedding in zip(stored["ids"], stored["embeddings"]):
        # Both sides are normalised, so the dot product is the cosine.
        scores[chunk_id] = max(0.0, sum(a * b for a, b in zip(vector, embedding)))
    return scores
