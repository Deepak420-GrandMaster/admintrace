"""Format citations.

Citations are built here rather than asked of the model, because a citation is
the one part of an answer that must be exactly right and a model can get a URL
or a date subtly wrong. Every citation carries the French title of the source,
its address, and the date it was last updated.

Stating the source and its update date is a condition of the licence the data
is published under. It is also how a reader decides whether to trust what they
just read, so it is never dropped.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.retrieval.types import Retrieved


@dataclass(frozen=True)
class Citation:
    """One source, ready to display."""

    fiche_id: str
    title_fr: str
    url: str
    last_updated: str
    last_updated_is_plausible: bool
    situation_fr: str
    section_title_fr: str
    score: float

    @property
    def updated_label(self) -> str:
        if not self.last_updated:
            return "update date unavailable"
        if not self.last_updated_is_plausible:
            # The published value is shown as published, and marked, rather
            # than hidden or quietly rewritten into something plausible.
            return f"last updated {self.last_updated} (date as published; appears mistyped)"
        return f"last updated {self.last_updated}"

    @property
    def scope_label(self) -> str:
        """Which branch of the fiche this came from, when it has branches."""
        return self.situation_fr


def build(hits: list[Retrieved]) -> list[Citation]:
    """One citation per source document, keeping its best-scoring passage."""
    best: dict[str, Retrieved] = {}
    for hit in hits:
        key = hit.metadata.get("fiche_id", "")
        existing = best.get(key)
        if existing is None or hit.dense_score > existing.dense_score:
            best[key] = hit

    citations = []
    for hit in sorted(best.values(), key=lambda h: -h.dense_score):
        meta = hit.metadata
        citations.append(
            Citation(
                fiche_id=meta.get("fiche_id", ""),
                title_fr=meta.get("fiche_title_fr", ""),
                url=meta.get("source_url", ""),
                last_updated=meta.get("last_updated", ""),
                last_updated_is_plausible=bool(
                    meta.get("last_updated_is_plausible", True)
                ),
                situation_fr=meta.get("situation_fr", ""),
                section_title_fr=meta.get("section_title_fr", ""),
                score=hit.dense_score,
            )
        )
    return citations


def as_passages(hits: list[Retrieved]) -> str:
    """The retrieved text, labelled so the model can attribute what it uses."""
    blocks = []
    for index, hit in enumerate(hits, start=1):
        meta = hit.metadata
        label = f"[{index}] {meta.get('fiche_title_fr', '')} ({meta.get('fiche_id', '')})"
        if meta.get("situation_fr"):
            label += f" — situation: {meta['situation_fr']}"
        blocks.append(f"{label}\n{hit.text}")
    return "\n\n---\n\n".join(blocks)
