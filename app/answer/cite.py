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

import re
from dataclasses import dataclass

from app.retrieval.types import Retrieved

# The chunker renders an official online service as its title on one line and
# its address on the next. That is the pattern picked up here.
_BARE_URL = re.compile(r"^(https?://\S+)$")

# Only addresses inside the official corpus are ever offered. Nothing is
# constructed, guessed, or completed from a domain name.
_TRUSTED_HOSTS = (
    ".gouv.fr", ".service-public.fr", ".ameli.fr", ".caf.fr", ".urssaf.fr",
    ".impots.gouv.fr", ".interieur.gouv.fr", ".education.fr", ".france.fr",
)


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
    excerpt: str = ""

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
                excerpt=_excerpt(hit.text),
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


def _excerpt(text: str, limit: int = 420) -> str:
    """The passage itself, minus the header lines the chunker prefixes.

    Shown when a source card is opened, so the reader can check the answer
    against the actual wording without leaving the page.
    """
    body = text.split("\n\n", 1)[-1].strip()
    if len(body) <= limit:
        return body
    cut = body[:limit]
    stop = max(cut.rfind(". "), cut.rfind("\n"))
    return (cut[:stop + 1] if stop > limit * 0.5 else cut).rstrip() + "…"


@dataclass(frozen=True)
class ServiceLink:
    """An official online service named in a retrieved passage."""

    title: str
    url: str
    fiche_id: str


def _is_official(url: str) -> bool:
    host = url.split("/")[2].lower() if "://" in url else ""
    return host.endswith(".gouv.fr") or any(
        host == suffix.lstrip(".") or host.endswith(suffix)
        for suffix in _TRUSTED_HOSTS
    )


def service_links(hits: list[Retrieved], limit: int = 3) -> list[ServiceLink]:
    """The actual pages where the procedure is carried out.

    People do not want to be told a service exists; they want to be taken to
    it. These addresses are read straight out of the retrieved passages, never
    assembled, and only official hosts are offered — a link is an instruction
    to go somewhere, and a wrong one sends someone to a place that may be
    happy to take their money or their passport number.
    """
    found: list[ServiceLink] = []
    seen: set[str] = set()

    for hit in hits:
        lines = [line.strip() for line in hit.text.splitlines()]
        for index, line in enumerate(lines):
            match = _BARE_URL.match(line)
            if not match:
                continue
            url = match.group(1).rstrip(".,;)")
            if url in seen or not _is_official(url):
                continue
            # The line above is the service's own name.
            title = ""
            for candidate in reversed(lines[max(0, index - 3):index]):
                if candidate and not _BARE_URL.match(candidate):
                    title = candidate
                    break
            if not title:
                continue
            seen.add(url)
            found.append(ServiceLink(
                title=title, url=url,
                fiche_id=hit.metadata.get("fiche_id", ""),
            ))
            if len(found) >= limit:
                return found
    return found
