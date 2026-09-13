"""BM25 keyword search over the French chunk text.

French administrative vocabulary is full of exact terms — récépissé, OFII,
attestation de dépôt, avis d'imposition, justificatif de domicile — where an
exact match is worth more than a semantic near-miss. Semantic search blurs
those together; this does not.

Accents are folded on both sides of the comparison, so someone typing
"recepisse" on a keyboard without accents finds the same passages as someone
typing "récépissé". Folding both sides preserves exact matching while removing
a barrier that has nothing to do with the question being asked.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from rank_bm25 import BM25Okapi

from app.config import Settings, get_settings
from app.ingest.embed import fetch_all, get_collection
from app.retrieval.types import Retrieved

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
# French elides constantly: l'OFII, d'imposition, qu'il. Treating the
# apostrophe as part of the word buries the term that matters inside a token
# nobody will ever search for, so it is a boundary.
_APOSTROPHE = re.compile(r"['’ʼ]")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def tokenize(text: str) -> list[str]:
    separated = _APOSTROPHE.sub(" ", fold(text))
    return [t for t in _TOKEN.findall(separated) if len(t) > 1]


@dataclass
class KeywordIndex:
    bm25: BM25Okapi
    ids: list[str]
    texts: list[str]
    metadatas: list[dict]

    def search(self, query: str, limit: int = 20) -> list[Retrieved]:
        tokens = tokenize(query)
        if not tokens:
            return []
        scores = self.bm25.get_scores(tokens)
        ordered = sorted(range(len(scores)), key=lambda i: -scores[i])[:limit]
        results = []
        for rank, index in enumerate(ordered, start=1):
            if scores[index] <= 0:
                break
            results.append(
                Retrieved(
                    chunk_id=self.ids[index],
                    text=self.texts[index],
                    metadata=self.metadatas[index],
                    keyword_score=float(scores[index]),
                    keyword_rank=rank,
                    sources=["keyword"],
                )
            )
        return results


@lru_cache(maxsize=1)
def _index(fingerprint: str) -> KeywordIndex:
    collection = get_collection()
    stored = fetch_all(collection, ["documents", "metadatas"])
    texts = stored["documents"]
    return KeywordIndex(
        bm25=BM25Okapi([tokenize(t) for t in texts]),
        ids=stored["ids"],
        texts=texts,
        metadatas=stored["metadatas"],
    )


def get_index(settings: Settings | None = None) -> KeywordIndex:
    """Build the keyword index once per process, keyed to collection size."""
    settings = settings or get_settings()
    collection = get_collection(settings)
    return _index(f"{settings.collection_name}:{collection.count()}")


def search(query: str, limit: int = 20,
           settings: Settings | None = None) -> list[Retrieved]:
    return get_index(settings).search(query, limit)
