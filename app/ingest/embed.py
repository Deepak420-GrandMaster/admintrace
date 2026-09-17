"""Embed chunks and write them to the vector store.

The embedding model runs locally and is multilingual by requirement, not by
preference: the corpus is French and roughly half the questions arrive in
English, so an English-first model would fail on exactly the vocabulary that
matters.

Indexing is resumable. Embedding tens of thousands of passages takes a while,
and a run that is interrupted should not start over.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
import threading
from functools import lru_cache
from typing import Callable, Iterable, Sequence

import chromadb

from app.config import Settings, get_settings
from app.ingest.chunk import Chunk


@dataclass
class IndexStats:
    embedded: int
    skipped: int
    total_in_collection: int
    seconds: float


#: Guards loading and using the model. Two threads calling it for the first
#: time both missed the cache and loaded it at once — the warm-up at startup
#: and the first question — and the process died mid-request with a leaked
#: semaphore: a native crash, not an exception anyone could catch. Encoding
#: from several threads at once is not safe on this backend either, and with
#: the queue running answers concurrently it would happen in normal use.
_LOCK = threading.RLock()


def _model(model_name: str, device: str | None, half: bool = False):
    with _LOCK:
        return _load_model(model_name, device, half)


@lru_cache(maxsize=4)
def _load_model(model_name: str, device: str | None, half: bool = False):
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, device=device)
    if half and device in {"mps", "cuda"}:
        # Halves memory and is measurably faster on this hardware. Embeddings
        # are compared by cosine similarity, where the precision loss is far
        # below anything that changes a ranking.
        model = model.half()
    return model


def _device() -> str | None:
    """Prefer the Apple GPU when present, otherwise let the library decide."""
    try:
        import torch

        if torch.backends.mps.is_available():
            return "mps"
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return None


def embed_texts(texts: Sequence[str], settings: Settings | None = None,
                batch_size: int | None = None) -> list[list[float]]:
    """Embed texts with the configured model, normalised for cosine similarity."""
    settings = settings or get_settings()
    if not texts:
        return []
    with _LOCK:
        model = _model(settings.embed_model, _device(), settings.embed_half_precision)
        vectors = model.encode(
            list(texts),
            batch_size=batch_size or settings.embed_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
    return [v.tolist() for v in vectors]


def embed_query(text: str, settings: Settings | None = None) -> list[float]:
    return embed_texts([text], settings)[0]


def get_client(settings: Settings | None = None) -> chromadb.ClientAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    return chromadb.PersistentClient(path=str(settings.chroma_dir))


def get_collection(settings: Settings | None = None):
    settings = settings or get_settings()
    client = get_client(settings)
    return client.get_or_create_collection(
        name=settings.collection_name,
        # Vectors are normalised, so cosine is the right measure here.
        metadata={"hnsw:space": "cosine"},
    )


def fetch_all(collection, include: list[str], page_size: int = 5000) -> dict:
    """Read a whole collection in pages.

    A single unbounded read fails once the collection is large: the query is
    built with one SQL variable per row and the database refuses it. Paging is
    not an optimisation here, it is the only thing that works.
    """
    ids: list[str] = []
    collected: dict[str, list] = {key: [] for key in include}
    offset = 0
    while True:
        page = collection.get(include=include, limit=page_size, offset=offset)
        page_ids = page["ids"]
        if not page_ids:
            break
        ids.extend(page_ids)
        for key in include:
            collected[key].extend(page.get(key) or [])
        offset += len(page_ids)
        if len(page_ids) < page_size:
            break
    return {"ids": ids, **collected}


def _batched(items: Sequence, size: int) -> Iterable[Sequence]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def batches_by_token_budget(
    chunks: Sequence[Chunk], token_budget: int, max_items: int
) -> list[list[Chunk]]:
    """Group chunks of similar length together.

    A transformer pads every sequence in a batch to the longest one in it, so a
    single long passage among short ones multiplies the work and the memory by
    the batch size. That is not a small inefficiency: with fixed-size batches
    this corpus exhausts GPU memory outright, because one 8,000-token passage
    drags thirty-one 165-token passages up to its own length.

    Sorting by length first means each batch is nearly uniform, so the budget
    below is a real bound on the work done per forward pass.
    """
    ordered = sorted(chunks, key=lambda c: c.token_count)
    batches: list[list[Chunk]] = []
    current: list[Chunk] = []
    longest = 0

    for chunk in ordered:
        candidate_longest = max(longest, chunk.token_count)
        if current and (
            (len(current) + 1) * candidate_longest > token_budget
            or len(current) >= max_items
        ):
            batches.append(current)
            current, longest = [chunk], chunk.token_count
        else:
            current.append(chunk)
            longest = candidate_longest

    if current:
        batches.append(current)
    return batches


def build_index(
    chunks: list[Chunk],
    settings: Settings | None = None,
    progress: Callable[[int, int], None] | None = None,
    refresh: bool = False,
) -> IndexStats:
    """Embed and store chunks, skipping any already present."""
    settings = settings or get_settings()
    started = time.time()
    collection = get_collection(settings)

    if refresh:
        client = get_client(settings)
        client.delete_collection(settings.collection_name)
        collection = get_collection(settings)
        known: set[str] = set()
    else:
        known = set(fetch_all(collection, [])["ids"])

    pending = [c for c in chunks if c.chunk_id not in known]
    embedded = 0

    for batch in batches_by_token_budget(
        pending, settings.embed_token_budget, settings.embed_batch_size
    ):
        vectors = embed_texts([c.text for c in batch], settings,
                              batch_size=len(batch))
        collection.add(
            ids=[c.chunk_id for c in batch],
            documents=[c.text for c in batch],
            embeddings=vectors,
            metadatas=[c.metadata() for c in batch],
        )
        embedded += len(batch)
        if progress:
            progress(embedded, len(pending))

    return IndexStats(
        embedded=embedded,
        skipped=len(chunks) - len(pending),
        total_in_collection=collection.count(),
        seconds=time.time() - started,
    )
