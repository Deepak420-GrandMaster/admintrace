"""When two official sources say different things.

Most of the time they do not actually disagree. A préfecture asking for
everything the ministry asks for *plus* a local form is not contradicting the
ministry; it is the same rule with a local addition, and reporting that as a
conflict would teach a reader to distrust both pages for no reason.

So difference is classified before it is ever called a conflict:

* ``COMPATIBLE``    — different subjects, or the same thing said twice;
* ``ADDITIVE``      — one asks for everything the other does, and more;
* ``CONDITIONAL``   — they apply to different people, cases or dates;
* ``CONTRADICTORY`` — both cannot be true for the same person on the same day.

Only the last is a conflict. When one is found, the resolution order is
deliberate and is *not* "whichever page was updated most recently": a national
page refreshed yesterday does not override what the counter in front of you
requires. Jurisdiction and specificity come first, dates after.

Nothing here decides what a requirement is. It compares two pieces of text
that official bodies published and says how they relate.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path

from app.config import Settings, get_settings
from app.sources.claims import Claim

#: Words that replace rather than add: "instead of", "no longer", "au lieu de".
_REPLACES = ("instead of", "rather than", "no longer", "not accepted",
             "is not required", "au lieu de", "en remplacement", "ne sont plus",
             "n'est plus", "n est plus", "ne sont pas accept", "non accept",
             "n'est pas requis", "supprim", "remplace par", "remplacé par")

#: Words that scope a statement to a case, so two can hold at once.
_CONDITIONS = ("si vous", "si le", "si la", "dans le cas", "pour les",
               "lorsque", "sauf", "except", "if you", "if the", "unless",
               "in the case", "for students", "pour les étudiants",
               "ressortissant", "selon")

#: Numbers as official pages actually write them. A validity period spelled
#: out in words is the same requirement as one in digits, and comparing only
#: digits missed every French page that spells its periods out.
_WORD_NUMBERS = {
    "un": "1", "une": "1", "deux": "2", "trois": "3", "quatre": "4",
    "cinq": "5", "six": "6", "sept": "7", "huit": "8", "neuf": "9",
    "dix": "10", "onze": "11", "douze": "12", "quinze": "15", "dix-huit": "18",
    "vingt": "20", "trente": "30",
    "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10",
    "twelve": "12", "eighteen": "18", "twenty": "20", "thirty": "30",
}

_UNITS = (r"mois|jour[s]?|semaine[s]?|an[s]?|ann[ée]e[s]?|heure[s]?|"
          r"month[s]?|day[s]?|week[s]?|year[s]?|hour[s]?|"
          r"euro[s]?|%|pages?|exemplaire[s]?|copie[s]?")

#: A measurement with a unit, which is where real contradictions hide.
_QUANTITY = re.compile(
    r"(\d+(?:[.,]\d+)?|" + "|".join(sorted(_WORD_NUMBERS, key=len, reverse=True))
    + r")\s*(" + _UNITS + r")",
    re.IGNORECASE,
)

_STOP = frozenset({
    "les", "des", "une", "der", "the", "and", "for", "you", "your", "with",
    "that", "this", "sont", "est", "vous", "pour", "dans", "avec", "doit",
    "must", "should", "will", "être", "etre", "avoir", "plus", "tout", "tous",
})


class Classification(str, Enum):
    COMPATIBLE = "compatible"
    ADDITIVE = "additive"
    CONDITIONAL = "conditional"
    CONTRADICTORY = "contradictory"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.split())


def _content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]{4,}", _fold(text)) if w not in _STOP}


def _quantities(text: str) -> set[tuple[str, str]]:
    """(amount, unit) pairs, normalised.

    A decimal comma and a decimal point become one thing, and so does a period
    spelled in words versus the same period in digits — the same requirement
    written two ways is not two requirements.
    """
    out = set()
    for amount, unit in _QUANTITY.findall(text or ""):
        folded = _fold(amount)
        value = _WORD_NUMBERS.get(folded, folded.replace(",", "."))
        out.add((value, _fold(unit).rstrip("s")))
    return out


def same_subject(a: str, b: str, threshold: float = 0.28) -> bool:
    """Whether two statements are even about the same thing."""
    words_a, words_b = _content_words(a), _content_words(b)
    if not words_a or not words_b:
        return False
    overlap = len(words_a & words_b) / min(len(words_a), len(words_b))
    return overlap >= threshold


def classify(a: Claim, b: Claim) -> Classification:
    """How two claims relate. Only CONTRADICTORY is a conflict."""
    text_a, text_b = a.text, b.text
    if not same_subject(text_a, text_b):
        return Classification.COMPATIBLE

    folded_a, folded_b = _fold(text_a), _fold(text_b)

    # Explicitly replacing something is the clearest contradiction there is.
    if any(marker in folded_a or marker in folded_b for marker in _REPLACES):
        return Classification.CONTRADICTORY

    # The same measure with two different values, for the same subject.
    quantities_a, quantities_b = _quantities(text_a), _quantities(text_b)
    shared_units = {unit for _amount, unit in quantities_a} & \
                   {unit for _amount, unit in quantities_b}
    for unit in shared_units:
        values_a = {amount for amount, u in quantities_a if u == unit}
        values_b = {amount for amount, u in quantities_b if u == unit}
        if values_a and values_b and not (values_a & values_b):
            return Classification.CONTRADICTORY

    # Scoped to different cases, so both can hold.
    if any(marker in folded_a for marker in _CONDITIONS) != \
            any(marker in folded_b for marker in _CONDITIONS):
        return Classification.CONDITIONAL

    # One asks for everything the other does, and more.
    words_a, words_b = _content_words(text_a), _content_words(text_b)
    if words_a < words_b or words_b < words_a:
        return Classification.ADDITIVE

    return Classification.COMPATIBLE


# ----------------------------------------------------------- the record ----

@dataclass
class Conflict:
    conflict_id: str
    claim_a: str
    claim_b: str
    claim_a_id: str = ""
    claim_b_id: str = ""
    source_a: str = ""
    source_b: str = ""
    jurisdiction_a: str = ""
    jurisdiction_b: str = ""
    effective_a: str = ""
    effective_b: str = ""
    classification: str = Classification.CONTRADICTORY.value
    severity: str = Severity.MEDIUM.value
    detected_at: str = ""
    #: Which claim the resolution order picks, and on what grounds.
    prefer: str = ""
    prefer_reason: str = ""


def _conflict_id(a: Claim, b: Claim) -> str:
    material = "|".join(sorted([a.claim_id, b.claim_id]))
    return "CONF-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


#: Lower wins. A counter you stand in front of outranks a national summary.
_JURISDICTION_RANK = {"institution": 0, "city": 1, "department": 2,
                      "regional": 3, "national": 4}


def resolve(a: Claim, b: Claim, *, on: date | None = None) -> tuple[Claim, str]:
    """Which claim to follow, and why.

    Order is deliberate. "The newer page" is last, not first: a national page
    refreshed yesterday does not override what the préfecture handling your
    file actually requires.
    """
    on = on or datetime.now(tz=timezone.utc).date()

    # 1. Does one of them even apply on the date in question?
    applies_a, applies_b = a.applies_on(on), b.applies_on(on)
    if applies_a != applies_b:
        winner = a if applies_a else b
        return winner, "the other is not in force on this date"

    # 2. Jurisdiction: the more specific authority.
    rank_a = _JURISDICTION_RANK.get(a.jurisdiction, 9)
    rank_b = _JURISDICTION_RANK.get(b.jurisdiction, 9)
    if rank_a != rank_b:
        winner = a if rank_a < rank_b else b
        return winner, f"{winner.jurisdiction} authority is more specific"

    # 3. Specificity of the statement itself.
    if len(_content_words(a.text)) != len(_content_words(b.text)):
        winner = a if len(_content_words(a.text)) > len(_content_words(b.text)) else b
        return winner, "states the requirement more specifically"

    # 4. The later effective date, where both state one.
    if a.effective_from and b.effective_from and a.effective_from != b.effective_from:
        winner = a if a.effective_from > b.effective_from else b
        return winner, "takes effect later"

    # 5. The body with more authority over the subject.
    if a.authority_level != b.authority_level:
        winner = a if a.authority_level < b.authority_level else b
        return winner, "higher authority for this topic"

    # 6. Only now: whichever was read most recently.
    winner = a if a.retrieved_at >= b.retrieved_at else b
    return winner, "read more recently"


def detect(claims_a: list[Claim], claims_b: list[Claim], *,
           on: date | None = None, limit: int = 20) -> list[Conflict]:
    """Every genuine contradiction between two sets of claims."""
    stamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    found: list[Conflict] = []
    for a in claims_a:
        for b in claims_b:
            if a.source_id == b.source_id:
                continue
            classification = classify(a, b)
            if classification is not Classification.CONTRADICTORY:
                continue
            winner, reason = resolve(a, b, on=on)
            found.append(Conflict(
                conflict_id=_conflict_id(a, b),
                claim_a=a.text, claim_b=b.text,
                claim_a_id=a.claim_id, claim_b_id=b.claim_id,
                source_a=a.source_id, source_b=b.source_id,
                jurisdiction_a=a.jurisdiction, jurisdiction_b=b.jurisdiction,
                effective_a=a.effective_from, effective_b=b.effective_from,
                classification=classification.value,
                severity=(Severity.HIGH.value
                          if min(a.authority_level, b.authority_level) == 1
                          else Severity.MEDIUM.value),
                detected_at=stamp,
                prefer=winner.source_id, prefer_reason=reason,
            ))
            if len(found) >= limit:
                return found
    return found


def relationship(claims_a: list[Claim], claims_b: list[Claim]) -> Classification:
    """How two sets relate overall — the answer's headline, not a per-pair one."""
    seen = {classify(a, b) for a in claims_a for b in claims_b
            if a.source_id != b.source_id}
    for level in (Classification.CONTRADICTORY, Classification.CONDITIONAL,
                  Classification.ADDITIVE):
        if level in seen:
            return level
    return Classification.COMPATIBLE


def save(conflicts: list[Conflict], settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.data_dir / "sources" / "conflicts.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        for conflict in conflicts:
            log.write(json.dumps(asdict(conflict), ensure_ascii=False) + "\n")
    return path
