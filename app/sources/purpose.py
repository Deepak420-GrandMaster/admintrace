"""What the reader is trying to do, and what a page exists to say.

Ranking pages by how much they resemble the question sounds right and is not.
A question about admission and a news item reporting last year's admission
round use the same words; a marketing page titled "Why choose us" uses more of
them than the page that actually lists the requirements.

So two things are modelled separately:

* **purpose** — what the reader wants (admission, tuition, a deadline);
* **page type** — what the page is for (a procedure, a portal, a press
  release).

A page answers well when its type is one the purpose is served by. Everything
else ranks below, and news and marketing rank below that again — unless the
question is about what changed recently, which is the one case where a dated
announcement is exactly the right document.

Both tables live in ``purposes.yml``; nothing about French administration is
encoded here in code.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml

PURPOSES_PATH = Path(__file__).resolve().parent / "purposes.yml"

#: Page kinds that report an event rather than state a rule.
EDITORIAL_TYPES = frozenset({"news", "event", "marketing"})

#: Used when nothing matches, so an unclassified page is neither promoted
#: nor buried.
UNKNOWN_TYPE = "unknown"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


@dataclass(frozen=True)
class Purpose:
    id: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    page_types: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PageTypeRule:
    id: str
    paths: tuple[str, ...] = field(default_factory=tuple)
    titles: tuple[str, ...] = field(default_factory=tuple)


@lru_cache(maxsize=1)
def _tables() -> tuple[tuple[Purpose, ...], tuple[PageTypeRule, ...]]:
    raw = yaml.safe_load(PURPOSES_PATH.read_text(encoding="utf-8")) or {}
    purposes = tuple(
        Purpose(id=key,
                keywords=tuple(_fold(k) for k in (row or {}).get("keywords", ())),
                page_types=tuple((row or {}).get("page_types", ())))
        for key, row in (raw.get("purposes") or {}).items()
    )
    types = tuple(
        PageTypeRule(id=key,
                     paths=tuple(_fold(p) for p in (row or {}).get("paths", ())),
                     titles=tuple(_fold(t) for t in (row or {}).get("titles", ())))
        for key, row in (raw.get("page_types") or {}).items()
    )
    return purposes, types


def purposes() -> tuple[Purpose, ...]:
    return _tables()[0]


def page_type_rules() -> tuple[PageTypeRule, ...]:
    return _tables()[1]


def by_id(purpose_id: str) -> Purpose | None:
    return next((p for p in purposes() if p.id == purpose_id), None)


def detect(question: str) -> tuple[Purpose, ...]:
    """Which purposes this question serves, strongest first.

    More than one is normal and useful: "how much does it cost to apply" is
    both tuition and admission, and pages for either are worth reading.
    """
    folded = _fold(question)
    words = set(re.findall(r"[a-z0-9]{3,}", folded.replace("-", " ")))
    scored: list[tuple[int, Purpose]] = []
    for purpose in purposes():
        hits = sum(1 for keyword in purpose.keywords
                   if keyword in words or (" " in keyword and keyword in folded)
                   or (len(keyword) > 6 and keyword in folded))
        if hits:
            scored.append((hits, purpose))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    return tuple(purpose for _hits, purpose in scored)


def classify(url: str, title: str = "", anchor: str = "") -> str:
    """What kind of page this is, from its path and what it calls itself."""
    path = _fold(urlsplit(url).path)
    label = _fold(f"{title} {anchor}")
    for rule in page_type_rules():
        if any(fragment in path for fragment in rule.paths):
            return rule.id
    for rule in page_type_rules():
        if any(fragment and fragment in label for fragment in rule.titles):
            return rule.id
    return UNKNOWN_TYPE


def is_editorial(page_type: str) -> bool:
    return page_type in EDITORIAL_TYPES


def keyword_hits(text: str, found: tuple[Purpose, ...]) -> int:
    """How many purpose keywords this text carries."""
    folded = _fold(text).replace("-", " ").replace("/", " ").replace("_", " ")
    words = set(re.findall(r"[a-z0-9]{3,}", folded))
    return sum(1 for purpose in found for keyword in purpose.keywords
               if keyword in words)


def serves(page_type: str, found: tuple[Purpose, ...]) -> bool:
    """Whether a page of this kind is the sort that answers these purposes."""
    return any(page_type in purpose.page_types for purpose in found)
