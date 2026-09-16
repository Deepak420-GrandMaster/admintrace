"""Which administrative procedure a text is about.

Kept deterministic on purpose. Measured with the multilingual embedder
already used for retrieval, an English claim about *validating* a VLS-TS
scored 0.715 against "Je valide mon VLS-TS" and 0.665 against a sentence
about *renewing* one — a margin nobody should rest an appointment on.
Similarity says both sentences are about the VLS-TS, which is true and is
exactly the problem. Procedure identity is decided here from the words that
name the procedure, and similarity is only ever asked the easier question of
whether two sentences share a subject.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROCEDURES_PATH = Path(__file__).resolve().parent / "procedures.yml"


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.replace("-", " ").replace("’", "'").split())


@dataclass(frozen=True)
class Procedure:
    id: str
    label: str
    groups: tuple[tuple[re.Pattern, ...], ...]

    def matches(self, folded: str) -> bool:
        return all(any(p.search(folded) for p in group) for group in self.groups)


@lru_cache(maxsize=1)
def procedures() -> tuple[Procedure, ...]:
    raw = yaml.safe_load(PROCEDURES_PATH.read_text(encoding="utf-8")) or {}
    out = []
    for pid, row in (raw.get("procedures") or {}).items():
        groups = tuple(tuple(re.compile(p) for p in group)
                       for group in row.get("all_of", []))
        out.append(Procedure(id=str(pid), label=str(row.get("label", pid)),
                             groups=groups))
    return tuple(out)


def detect(text: str) -> frozenset[str]:
    """Every procedure this text names.

    Validation needs a validation *verb*, not merely the visa's name. That is
    what keeps "renouvellement de VLS-TS" a renewal and nothing else, and it
    is why "la fin de validité" — a noun about expiry — names no procedure.
    A sentence that genuinely names two procedures returns both, which is how
    a claim that blends them is caught.
    """
    folded = fold(text)
    if not folded:
        return frozenset()
    found = {p.id for p in procedures() if p.matches(folded)}
    return frozenset(found)


def of_page(title: str, url: str) -> frozenset[str]:
    """The procedure a page is *about*, from its own title and address.

    A page's title and path are the publisher's statement of what it covers,
    and they are more reliable than its body, which routinely mentions
    neighbouring procedures in passing.
    """
    path = re.sub(r"https?://[^/]+", "", url or "")
    path = re.sub(r"[/_#?=&.]+", " ", path)
    return detect(f"{title or ''} {path}")


def compatible(text_procedures: frozenset[str], asked: frozenset[str]) -> bool:
    """Whether evidence about some procedures can speak to the asked ones.

    Unknown on either side is compatible — most text names no procedure at
    all, and treating silence as a mismatch would reject every general
    sentence. Only two *named*, disjoint procedures are incompatible.
    """
    if not text_procedures or not asked:
        return True
    return bool(text_procedures & asked)


def label(procedure_ids) -> str:
    names = {p.id: p.label for p in procedures()}
    return ", ".join(names.get(p, p) for p in sorted(procedure_ids))
