"""Which authority the reader is actually standing in front of.

French administrative procedure is national law applied locally. The rule
comes from the ministry; the counter that applies it, the appointment system
and the list of papers that counter wants come from a préfecture — and those
differ. Answering a "what do I need" question purely from the national page is
how somebody arrives with the wrong folder.

So a place has to become an authority before retrieval starts:

    "I live in Montpellier"  →  Hérault  →  Préfecture de l'Hérault

Two failures to avoid, and they are opposites. Guessing the département from a
city name that could be several places is one. Refusing to answer because
nobody typed a département number is the other — almost nobody knows theirs.
So: resolve where it is unambiguous, ask where it is not, and never invent.

Nothing about any procedure is encoded here. This module knows places and who
administers them; what they require comes from retrieved documents.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

JURISDICTIONS_PATH = Path(__file__).resolve().parent / "jurisdictions.yml"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.replace("-", " ").split())


@dataclass(frozen=True)
class Region:
    id: str
    name: str


@dataclass(frozen=True)
class Department:
    code: str
    id: str
    name: str
    region: str
    prefecture_source: str = ""
    prefecture_name: str = ""
    cities: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""


@dataclass(frozen=True)
class Place:
    """Where the reader is, as far as anyone can tell."""

    city: str = ""
    department: Department | None = None
    region: Region | None = None
    #: More than one département matched the place named.
    candidates: tuple[Department, ...] = field(default_factory=tuple)
    #: True when a place was named but could not be pinned to one authority.
    needs_clarification: bool = False

    @property
    def known(self) -> bool:
        return self.department is not None

    @property
    def authority_name(self) -> str:
        return self.department.prefecture_name if self.department else ""

    @property
    def authority_source_id(self) -> str:
        return self.department.prefecture_source if self.department else ""

    def label(self) -> str:
        """How to say where this is, to a reader."""
        if self.city and self.department:
            return f"{self.city} · {self.department.name}"
        return self.department.name if self.department else self.city


@lru_cache(maxsize=1)
def _tables() -> tuple[tuple[Region, ...], tuple[Department, ...]]:
    raw = yaml.safe_load(JURISDICTIONS_PATH.read_text(encoding="utf-8")) or {}
    regions = tuple(Region(id=str(r["id"]), name=str(r["name"]))
                    for r in raw.get("regions", []))
    departments = tuple(
        Department(
            code=str(d["code"]), id=str(d["id"]), name=str(d["name"]),
            region=str(d.get("region", "")),
            prefecture_source=str(d.get("prefecture_source", "")),
            prefecture_name=str(d.get("prefecture_name", "")),
            cities=tuple(str(c) for c in d.get("cities", ())),
            notes=str(d.get("notes", "")),
        )
        for d in raw.get("departments", [])
    )
    return regions, departments


def regions() -> tuple[Region, ...]:
    return _tables()[0]


def departments() -> tuple[Department, ...]:
    return _tables()[1]


def region_by_id(region_id: str) -> Region | None:
    return next((r for r in regions() if r.id == region_id), None)


def department_by_code(code: str) -> Department | None:
    code = (code or "").strip()
    return next((d for d in departments() if d.code == code), None)


def department_by_name(name: str) -> Department | None:
    wanted = _fold(name)
    return next((d for d in departments() if _fold(d.name) == wanted), None)


def departments_for_city(city: str) -> tuple[Department, ...]:
    """Every département that has a commune of this name.

    More than one is a real possibility in France and is why this returns a
    tuple: several communes share a name, and picking the biggest would be a
    guess with someone's appointment on it.
    """
    wanted = _fold(city)
    if not wanted:
        return ()
    return tuple(d for d in departments()
                 if any(_fold(c) == wanted for c in d.cities))


#: "in Montpellier", "à Lyon", "I live in Lille".
#:
#: The case-insensitive flag is scoped to the prepositions on purpose. Applied
#: to the whole pattern it also makes [A-Z] match lowercase, and the capture
#: then runs away down the rest of the sentence — "Montpellier and need to
#: renew my student residence permit" was read as a place name.
_PLACE_HINT = re.compile(
    r"(?i:\b(?:in|at|near|from|live in|living in|based in|"
    r"à|au|aux|dans|habite à|habite a|j'habite à|j'habite a|près de|pres de)\s+)"
    r"([A-ZÀ-ÖØ-Þ][\w'’\-]*(?:[ -][A-ZÀ-ÖØ-Þ][\w'’\-]*){0,3})"
)

#: A bare département number, which some readers do know.
_DEPT_CODE = re.compile(r"\b(?:d[ée]partement\s+)?(\d{2})\b")


def resolve(text: str, *, hint_department: str = "") -> Place:
    """Work out which authority a question falls under.

    ``hint_department`` is for callers that already know — the institution
    register, for instance, records the département of every school, so a
    question about a school in Montpellier does not need the reader to say so.
    """
    if hint_department:
        found = department_by_name(hint_department) or department_by_code(hint_department)
        if found:
            return Place(department=found, region=region_by_id(found.region))

    text = text or ""

    # A département named outright wins: it is already the answer.
    for department in departments():
        if _fold(department.name) in _fold(text):
            return Place(department=department,
                         region=region_by_id(department.region))

    for match in _PLACE_HINT.finditer(text):
        span = match.group(1).strip().rstrip(".,;:!?")
        # A capture may pick up a following proper noun ("Montpellier Business
        # School"); try the longest reading first, then shorter ones, so a
        # compound commune like Castelnau-le-Lez still resolves.
        words = span.split()
        for length in range(len(words), 0, -1):
            candidate = " ".join(words[:length])
            hits = departments_for_city(candidate)
            if len(hits) == 1:
                return Place(city=candidate, department=hits[0],
                             region=region_by_id(hits[0].region))
            if len(hits) > 1:
                return Place(city=candidate, candidates=hits,
                             needs_clarification=True)

    # A bare code, but only when the question is plainly about place.
    if re.search(r"\b(d[ée]partement|pr[ée]fecture)\b", text, re.IGNORECASE):
        code = _DEPT_CODE.search(text)
        if code:
            found = department_by_code(code.group(1))
            if found:
                return Place(department=found, region=region_by_id(found.region))

    return Place()


def city_named(text: str) -> str:
    """The place a reader named, whether or not we know who administers it."""
    for match in _PLACE_HINT.finditer(text or ""):
        return match.group(1).strip()
    return ""
