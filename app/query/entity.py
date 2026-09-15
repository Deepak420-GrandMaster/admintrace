"""Working out who "this school" is.

A question like *how do I get admission to this school* has no answer on its
own. It has an answer only once *this school* points at something, and the
thing it points at was usually named a turn or two earlier.

Two failures matter here and they are opposites:

* guessing — deciding the question is about whichever institution was
  mentioned most recently and answering confidently about the wrong one;
* answering anyway — ignoring the reference, retrieving on the leftover words
  ("admission", "school"), and handing back generic public-administration
  pages that were never about this institution at all.

The second is the one this codebase was doing. It is worse than it looks,
because the pages returned are genuinely official, so nothing about the reply
signals that it missed the question.

So the resolver only ever does three things: find an institution named
outright, carry one forward from earlier in the conversation, or say it does
not know and that someone should be asked. It never picks between candidates.

Nothing here encodes a fact about any institution. Names, acronyms and
official links come from the register in :mod:`app.directory.universities`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.directory.universities import Institution, search

# A reference that cannot stand on its own. Matched whole-word, and kept to
# phrases that genuinely depend on earlier context: "the school" does, "a
# school" does not.
_DEMONSTRATIVE = re.compile(
    r"\b("
    r"this|that|the|my|our|its|their"
    r")\s+("
    r"school|university|college|institution|campus|faculty|business\s+school"
    r"|grande\s+ecole|grande\s+école"
    r")\b"
    r"|\b("
    r"cette|cet|ce|ma|mon|notre|leur|la|le|l'|l’"
    r")\s?("
    r"ecole|école|universite|université|etablissement|établissement"
    r"|fac|faculte|faculté|campus"
    r")\b",
    re.IGNORECASE,
)

# A run of capitalised words, which is how an institution is written when it
# is named outright. Accented capitals included, because "École" is one.
_PROPER = re.compile(r"\b[A-ZÀ-ÖØ-Þ][\w’'\-]*(?:\s+[A-ZÀ-ÖØ-Þ&][\w’'\-]*)+")

# A bare acronym: HEC, MBS, INSA. Two to six letters so it does not swallow
# ordinary shouting or a stray "OK".
_ACRONYM = re.compile(r"\b[A-Z]{2,6}\b")

# Capitalised words that start a sentence or name a country rather than an
# institution. Without this, "How" and "France" are searched as institutions.
_STOPWORDS = frozenset({
    "how", "what", "where", "when", "which", "who", "why", "do", "does", "did",
    "can", "could", "should", "is", "are", "was", "i", "my", "the", "a", "an",
    "france", "french", "paris", "comment", "que", "quoi", "ou", "où", "quand",
    "quel", "quelle", "est", "sont", "je", "mon", "ma", "le", "la", "les",
    "un", "une", "des", "et", "pour", "dans", "avec",
})


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.split())


@dataclass(frozen=True)
class Resolution:
    """Who the question is about, and how sure we are."""

    #: The institution the question is about, once it is known.
    institution: Institution | None = None
    #: The words in the question that pointed at it ("this school", "MBS").
    surface: str = ""
    #: True when the institution came from an earlier turn, not this question.
    from_context: bool = False
    #: True when the question depends on an institution nobody has named yet.
    needs_clarification: bool = False
    #: Distinct institutions matched, when a named mention was ambiguous.
    candidates: tuple[Institution, ...] = field(default_factory=tuple)

    @property
    def is_about_institution(self) -> bool:
        """Whether this question is about a named body at all.

        True while we are still asking which one, because the question is
        about an institution either way — we just do not know which.
        """
        return self.institution is not None or self.needs_clarification


def _distinct(hits: list[Institution]) -> list[Institution]:
    """One entry per institution name; the register lists campuses separately."""
    seen: set[str] = set()
    out: list[Institution] = []
    for hit in hits:
        key = _fold(hit.name)
        if key not in seen:
            seen.add(key)
            out.append(hit)
    return out


def _accept(span: str, hit: Institution) -> bool:
    """Only take a match tight enough to be the thing that was named.

    ``search`` is deliberately generous — it has to find Sorbonne from
    "sorbonne". Here a loose match is a wrong answer about a real school, so
    the span has to be the name, the acronym, or an unambiguous prefix of a
    multi-word name.
    """
    needle, name, acronym = _fold(span), _fold(hit.name), _fold(hit.acronym)
    if not needle:
        return False
    if needle == name or (acronym and needle == acronym):
        return True
    return len(needle.split()) >= 2 and name.startswith(needle)


def named_institutions(text: str) -> list[Institution]:
    """Institutions named outright in this text, best match first."""
    text = text or ""
    spans: list[str] = []
    for match in _PROPER.finditer(text):
        span = match.group(0).strip()
        if _fold(span) not in _STOPWORDS:
            spans.append(span)
    for match in _ACRONYM.finditer(text):
        span = match.group(0)
        if _fold(span) not in _STOPWORDS:
            spans.append(span)

    # Longest first: "Montpellier Business School" should win over "Montpellier".
    spans.sort(key=len, reverse=True)

    found: list[Institution] = []
    for span in spans:
        for hit in search(span, limit=6):
            if _accept(span, hit):
                found.append(hit)
        if found:
            break
    return _distinct(found)


def mentions_institution_reference(text: str) -> bool:
    """Whether the text leans on an institution it does not name."""
    return bool(_DEMONSTRATIVE.search(text or ""))


def resolve(question: str, history: list[str] | None = None) -> Resolution:
    """Decide which institution a question is about.

    ``history`` is the earlier questions in the conversation, oldest first.
    A named institution in the current question always wins over one carried
    forward, because naming something is how you change the subject.
    """
    question = question or ""

    here = named_institutions(question)
    if here:
        return Resolution(institution=here[0], surface=here[0].name,
                          candidates=tuple(here))

    if not mentions_institution_reference(question):
        return Resolution()

    surface = ""
    match = _DEMONSTRATIVE.search(question)
    if match:
        surface = " ".join(match.group(0).split())

    # Nothing named here, and the question needs one: look back, most recent
    # first, and take the last institution actually named by the person.
    for earlier in reversed(list(history or [])):
        carried = named_institutions(earlier)
        if carried:
            return Resolution(institution=carried[0], surface=surface,
                              from_context=True, candidates=tuple(carried))

    return Resolution(surface=surface, needs_clarification=True)
