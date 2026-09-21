"""Whether a page would actually help with *this* question.

The relevance gate upstream asks a narrower question than it looks like it
asks: it scores how similar a passage is to the words in the query. Similar
is not the same as useful. "How do I get a free tram in Antibes" and a page
about free travel for Paris pensioners share almost every word — transport,
free, entitlement, city — and the second is an official, current, correct
page about somebody else.

So selection happens in two stages, and they are kept apart on purpose:

``candidates``  what retrieval found. Semantically close. May be about
                anything that shares vocabulary.
``selected``    what a reader is shown. Has to survive the questions below.

The test, in one sentence: *would a reasonable person use this page to work
out the answer to this question?* A page that fails is not a lead, a
near-miss, or a starting point. It is a page about a different subject, and
showing it under an answer lends it an authority it was just refused.

Every rejection carries a reason, because "these pages are unrelated" is a
bug report somebody will file and the reason is what makes it answerable.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.sources import jurisdiction


class Rejection(str, Enum):
    """Why a candidate did not become a selected source."""

    WRONG_ENTITY = "wrong_entity"
    WRONG_JURISDICTION = "wrong_jurisdiction"
    WRONG_PURPOSE = "wrong_purpose"
    WRONG_PAGE_TYPE = "wrong_page_type"
    STALE = "stale"
    LOW_AUTHORITY = "low_authority"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    DUPLICATE = "duplicate"
    IRRELEVANT = "irrelevant"


@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    detail: str = ""


@dataclass
class Selection:
    """What was found, what is shown, and why the rest is not."""

    candidates: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    rejected: list = field(default_factory=list)

    def reasons(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, verdict in self.rejected:
            counts[verdict.reason] = counts.get(verdict.reason, 0) + 1
        return counts

    def as_diagnostics(self) -> dict:
        """What a bug report needs in order to be actionable."""
        return {
            "candidate_count": len(self.candidates),
            "selected_count": len(self.selected),
            "rejected": [
                {"title": getattr(hit, "title", "") or "",
                 "url": getattr(hit, "url", "") or "",
                 "reason": verdict.reason, "detail": verdict.detail}
                for hit, verdict in self.rejected[:20]
            ],
            "reason_counts": self.reasons(),
        }


def _text_of(hit) -> str:
    meta = getattr(hit, "metadata", {}) or {}
    return " ".join(str(part) for part in (
        getattr(hit, "title", "") or "",
        meta.get("situation_fr", ""),
        meta.get("section_title_fr", ""),
        meta.get("theme", ""),
        meta.get("sub_theme", ""),
    ) if part)


def _places_named_in(text: str) -> set[str]:
    """Départements a page's own title or scope names, if any."""
    found = jurisdiction._scan_known(text, require_signal=True)
    if found is None or not found.known:
        return set()
    return {found.department.code}


def check(hit, *, place=None, purposes: tuple = ()) -> Verdict:
    """Would a reasonable person use this page to answer this question?

    Two failures are worth catching here because both produce official,
    accurate pages that are about somebody else:

    * a page scoped to a different place than the reader is in — the Paris
      senior travel page for a question about Antibes;
    * a page whose subject is a different entitlement that happens to share
      vocabulary — an RSA page surfacing because "free" and "entitlement"
      appear in a transport question.
    """
    text = _text_of(hit)

    if place is not None and getattr(place, "known", False):
        named = _places_named_in(text)
        if named and place.department.code not in named:
            return Verdict(False, Rejection.WRONG_JURISDICTION.value,
                           f"page is scoped to {', '.join(sorted(named))}, "
                           f"reader is in {place.department.code}")

    if purposes:
        from app.sources import purpose as purpose_table

        page_purposes = {p.id for p in purpose_table.detect(text)}
        if page_purposes and not page_purposes & {p for p in purposes}:
            return Verdict(False, Rejection.WRONG_PURPOSE.value,
                           f"page is about {', '.join(sorted(page_purposes))}, "
                           f"question is about {', '.join(sorted(purposes))}")

    return Verdict(True)


def is_source_materially_relevant(hit, *, place=None, purposes: tuple = ()) -> bool:
    """The gate in one call, for callers that do not need the reason."""
    return check(hit, place=place, purposes=purposes).ok


#: How many passages a single page may contribute.
#:
#: A fiche answers a question across its sections — the amount in one, the
#: deadline in another — so treating the whole page as one candidate drops
#: every section but the first. That is what refused "how much deposit can a
#: landlord ask for": the page was retrieved, the section stating the amount
#: was discarded as a duplicate of the section about getting it back, and the
#: gate correctly reported that the passages it had did not answer the
#: question. The cap still stops one page filling every slot.
PASSAGES_PER_PAGE = 3


def _page_of(hit) -> str:
    return (getattr(hit, "metadata", {}) or {}).get("fiche_id") or \
        getattr(hit, "url", "")


def _section_of(hit) -> str:
    """Which part of the page this is, as precisely as the metadata allows.

    Falling back to the chunk id keeps two passages distinct when a source
    carries no section metadata at all, so this can only ever be as strict as
    the page-level key it replaced.
    """
    metadata = getattr(hit, "metadata", {}) or {}
    for key in ("section_path", "section_title_fr"):
        if metadata.get(key):
            return f"{metadata[key]}|{metadata.get('situation_fr') or ''}"
    return metadata.get("chunk_id") or getattr(hit, "chunk_id", "") or ""


def select(candidates: list, *, place=None, purposes: tuple = ()) -> Selection:
    """Split what retrieval found into what may be shown and what may not."""
    chosen, refused, seen = [], [], set()
    per_page: dict[str, int] = {}
    for hit in candidates:
        page, section = _page_of(hit), _section_of(hit)
        if page and (page, section) in seen:
            refused.append((hit, Verdict(
                False, Rejection.DUPLICATE.value,
                "same section of the same page already selected")))
            continue
        if page and per_page.get(page, 0) >= PASSAGES_PER_PAGE:
            refused.append((hit, Verdict(
                False, Rejection.DUPLICATE.value,
                f"already showing {PASSAGES_PER_PAGE} passages from this page")))
            continue
        verdict = check(hit, place=place, purposes=purposes)
        if verdict.ok:
            chosen.append(hit)
            if page:
                seen.add((page, section))
                per_page[page] = per_page.get(page, 0) + 1
        else:
            refused.append((hit, verdict))
    return Selection(candidates=list(candidates), selected=chosen,
                     rejected=refused)
