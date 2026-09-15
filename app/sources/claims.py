"""What a page actually asserts, and everything needed to stand behind it.

A source version is a document. A *claim* is one thing that document says
which somebody could act on — a required paper, a condition, a deadline. The
distinction matters because documents and requirements change at different
rates: a site can be redesigned weekly without a single requirement moving,
and a single sentence can change while the page looks identical.

Answers are assembled from claims, so a claim carries everything needed to
defend it later: which body said it, in which version of which page, under
which jurisdiction, and — where the page says so — from what date it applies.

Two dates that are routinely confused and are not the same thing:

* ``published_at`` / ``updated_at`` — when the page was written;
* ``effective_from`` — when the rule it describes starts to apply.

A rule published in June and effective in July is not the rule in June. Only
the second date decides whether a claim applies to the date being asked about,
and when a page does not state one, this records that it does not rather than
substituting the first.

Nothing here decides what any requirement *is*. Extraction selects sentences;
the wording stays the source's own.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings

#: Sentences that assert something a reader acts on. Deliberately the same
#: family of words the change detector uses, so "what changed" and "what is
#: claimed" cannot drift apart.
_CLAIM_WORDS = (
    "document", "documents", "justificatif", "justificatifs", "piece", "pièce",
    "pieces", "pièces", "attestation", "formulaire", "cerfa", "certificat",
    "condition", "conditions", "eligib", "éligib", "requis", "required",
    "obligatoire", "mandatory", "must", "devez", "doit", "faut",
    "delai", "délai", "deadline", "date limite", "avant le", "jusqu'au",
    "tarif", "frais", "fee", "cost", "cout", "coût", "montant", "gratuit",
    "procedure", "procédure", "demarche", "démarche", "etape", "étape",
    "candidature", "admission", "inscription", "apply", "application",
    "rendez-vous", "appointment", "en ligne", "online", "teleservice",
    "téléservice", "portail",
)

#: "à compter du 1er juillet 2026", "applicable au 01/07/2026",
#: "effective from 1 July 2026", "depuis le 1er janvier".
_EFFECTIVE = re.compile(
    r"(?:à\s+compter\s+du|a\s+compter\s+du|applicable\s+(?:au|à\s+partir\s+du)|"
    r"en\s+vigueur\s+(?:au|à\s+compter\s+du)|depuis\s+le|effective\s+(?:from|on)|"
    r"as\s+of|with\s+effect\s+from|starting)\s+"
    r"(\d{1,2}(?:er)?\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

_UNTIL = re.compile(
    r"(?:jusqu'au|jusqu’au|until|up\s+to|valable\s+jusqu'au)\s+"
    r"(\d{1,2}(?:er)?\s+\w+\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

_FRENCH_MONTHS = {
    "janvier": 1, "fevrier": 2, "février": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "aout": 8, "août": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "decembre": 12, "décembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


class Currency(str):
    """Marker type for readability in signatures."""


#: A claim's standing relative to the source it came from.
CURRENT = "current"
STALE_PENDING_REVIEW = "stale_pending_review"
SUPERSEDED = "superseded"


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.split())


def parse_date(text: str) -> str:
    """An ISO date from the ways a French official page writes one, or ""."""
    value = (text or "").strip().lower().replace("1er", "1")
    iso = re.match(r"^(\d{4})-(\d{2})-(\d{2})$", value)
    if iso:
        return value
    slash = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", value)
    if slash:
        day, month, year = (int(p) for p in slash.groups())
        try:
            return date(year, month, day).isoformat()
        except ValueError:
            return ""
    words = re.match(r"^(\d{1,2})\s+([a-zà-öø-þ]+)\s+(\d{4})$", _fold(value))
    if words:
        day, month_name, year = words.group(1), words.group(2), words.group(3)
        month = _FRENCH_MONTHS.get(month_name)
        if month:
            try:
                return date(int(year), month, int(day)).isoformat()
            except ValueError:
                return ""
    return ""


@dataclass
class Claim:
    """One thing a source says, with everything needed to stand behind it."""

    claim_id: str
    text: str
    source_id: str
    version_id: str
    url: str
    authority_level: int = 2
    jurisdiction: str = "national"
    jurisdiction_area: str = ""
    topics: list[str] = field(default_factory=list)
    #: When the rule starts to apply. Empty means the page does not say, which
    #: is different from "applies always" and is recorded as unknown.
    effective_from: str = ""
    effective_until: str = ""
    #: When the page itself was published or last updated, if it says.
    published_at: str = ""
    retrieved_at: str = ""
    confidence: str = "medium"
    status: str = CURRENT

    def applies_on(self, when: date | None = None) -> bool:
        """Whether this claim is in force on a given date.

        A claim with no stated start is treated as in force — most pages never
        state one — but a claim with a start in the future is not, which is
        the whole point of keeping the two dates apart.
        """
        when = when or datetime.now(tz=timezone.utc).date()
        if self.effective_from:
            try:
                if date.fromisoformat(self.effective_from) > when:
                    return False
            except ValueError:
                pass
        if self.effective_until:
            try:
                if date.fromisoformat(self.effective_until) < when:
                    return False
            except ValueError:
                pass
        return True

    @property
    def is_current(self) -> bool:
        return self.status == CURRENT


def claim_id_for(source_id: str, text: str) -> str:
    """Stable across versions: the same sentence keeps the same id.

    That is what lets a change be seen as "this requirement moved" rather than
    as an unrelated claim appearing and another disappearing.
    """
    material = f"{source_id}|{_fold(text)}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def extract(text: str, *, source_id: str, version_id: str, url: str,
            authority_level: int = 2, jurisdiction: str = "national",
            jurisdiction_area: str = "", topics: list[str] | None = None,
            published_at: str = "", retrieved_at: str = "",
            limit: int = 40) -> list[Claim]:
    """The sentences in this page that assert something actionable.

    Selection only. The wording stays the source's own, so a claim can be
    shown back to a reader as what the page says rather than as a paraphrase
    nobody published.
    """
    found: list[Claim] = []
    seen: set[str] = set()

    for line in (text or "").splitlines():
        sentence = " ".join(line.split())
        if len(sentence.split()) < 5 or len(sentence) > 400:
            continue
        folded = _fold(sentence)
        if not any(_fold(word) in folded for word in _CLAIM_WORDS):
            continue

        identifier = claim_id_for(source_id, sentence)
        if identifier in seen:
            continue
        seen.add(identifier)

        effective = _EFFECTIVE.search(sentence)
        until = _UNTIL.search(sentence)
        found.append(Claim(
            claim_id=identifier,
            text=sentence,
            source_id=source_id,
            version_id=version_id,
            url=url,
            authority_level=authority_level,
            jurisdiction=jurisdiction,
            jurisdiction_area=jurisdiction_area,
            topics=list(topics or []),
            effective_from=parse_date(effective.group(1)) if effective else "",
            effective_until=parse_date(until.group(1)) if until else "",
            published_at=published_at,
            retrieved_at=retrieved_at,
            confidence="high" if authority_level == 1 else "medium",
        ))
        if len(found) >= limit:
            break
    return found


# ------------------------------------------------------------------ store --

def _dir(settings: Settings, source_id: str) -> Path:
    path = (settings or get_settings()).data_dir / "sources" / "claims" / source_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save(claims: list[Claim], source_id: str, version_id: str,
         settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = _dir(settings, source_id) / f"{version_id}.json"
    path.write_text(json.dumps([asdict(c) for c in claims],
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(source_id: str, version_id: str,
         settings: Settings | None = None) -> list[Claim]:
    settings = settings or get_settings()
    path = _dir(settings, source_id) / f"{version_id}.json"
    try:
        return [Claim(**row) for row in
                json.loads(path.read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def mark_stale(source_id: str, version_id: str, claim_ids: list[str],
               settings: Settings | None = None) -> int:
    """Flag claims as needing review after their source moved underneath them."""
    claims = load(source_id, version_id, settings)
    if not claims:
        return 0
    wanted = set(claim_ids)
    touched = 0
    for claim in claims:
        if claim.claim_id in wanted and claim.status == CURRENT:
            claim.status = STALE_PENDING_REVIEW
            touched += 1
    if touched:
        save(claims, source_id, version_id, settings)
    return touched


def diff(old: list[Claim], new: list[Claim]) -> tuple[list[Claim], list[Claim], list[Claim]]:
    """(added, removed, kept) between two versions of the same page."""
    old_by_id = {c.claim_id: c for c in old}
    new_by_id = {c.claim_id: c for c in new}
    added = [c for i, c in new_by_id.items() if i not in old_by_id]
    removed = [c for i, c in old_by_id.items() if i not in new_by_id]
    kept = [c for i, c in new_by_id.items() if i in old_by_id]
    return added, removed, kept
