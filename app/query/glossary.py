"""Load the French/English administrative glossary and apply it.

Serves three purposes: expanding an English query with the French terms an
official document would actually use, supplying tooltips, and backing the
glossary tab.

Entries record where their French text came from. An explanation taken from
the published corpus is marked with its identifier; one written for this
project is marked as written. They are never presented as the same thing.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

GLOSSARY_PATH = Path(__file__).resolve().parent / "glossary.json"


@dataclass(frozen=True)
class Term:
    fr: str
    en: str
    explanation_en: str
    explanation_fr: str
    aliases_en: tuple[str, ...]
    source: str
    definition_id: str | None = None

    @property
    def is_official(self) -> bool:
        return self.source != "authored"

    @property
    def provenance(self) -> str:
        if self.is_official:
            return f"Definition published by service-public.gouv.fr ({self.definition_id})"
        return "Plain-language description written for Claré"


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


@lru_cache(maxsize=1)
def load() -> tuple[Term, ...]:
    raw = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    return tuple(
        Term(
            fr=fr,
            en=entry["en"],
            explanation_en=entry["explanation_en"],
            explanation_fr=entry["explanation_fr"],
            aliases_en=tuple(entry.get("aliases_en", ())),
            source=entry.get("source", "authored"),
            definition_id=entry.get("definition_id"),
        )
        for fr, entry in sorted(raw.items())
    )


@lru_cache(maxsize=1)
def _alias_index() -> list[tuple[str, Term]]:
    """Aliases longest first, so a specific phrase wins over a shorter one."""
    pairs: list[tuple[str, Term]] = []
    for term in load():
        for alias in {*term.aliases_en, term.fr, term.en}:
            pairs.append((_fold(alias), term))
    return sorted(pairs, key=lambda pair: -len(pair[0]))


def find_terms(text: str) -> list[Term]:
    """Glossary terms the text refers to, whichever language it used."""
    folded = _fold(text)
    found: list[Term] = []
    seen: set[str] = set()
    for alias, term in _alias_index():
        if term.fr in seen or len(alias) < 3:
            continue
        if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", folded):
            found.append(term)
            seen.add(term.fr)
    return found


def expand(text: str) -> tuple[str, list[Term]]:
    """Append the French vocabulary a source document would use.

    Retrieval searches a French corpus. A question asked in English rarely
    contains the word the document is written around, and the exact French
    term is precisely what keyword search needs.
    """
    terms = find_terms(text)
    if not terms:
        return text, []
    additions = " ".join(term.fr for term in terms if _fold(term.fr) not in _fold(text))
    return (f"{text} {additions}".strip() if additions else text), terms
