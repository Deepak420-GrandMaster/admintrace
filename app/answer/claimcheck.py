"""What an answer is allowed to say, decided by the evidence it came from.

The model writes the explanation. The evidence decides what survives it.

Found in the live Antibes flow: asked how to validate a long-stay visa, the
local model answered that it must be validated "three to four months before
expiry" and, two sentences later, that "the clock starts the day you land".
Neither was true of validation. The first was a renewal deadline, lifted
from a préfecture renewal page that discovery had picked because its text
says "renouvellement de VLS-TS"; the second appeared in no evidence at all.
Provenance said, correctly, that the answer came from official pages. It had
no way to say that the answer misrepresented them.

This module is that way, and it spends no model call. Each sentence of the
finished answer is read as a claim and checked against the sentences of the
evidence, in tiers:

1. **Exact facts.** Durations (digits or words, English or French, ranges
   included), amounts, dates, URLs, e-mail addresses and phone numbers, named
   portals and authorities, and what a deadline is counted from — expiry or
   arrival. These are compared directly. A number that appears in the
   evidence only inside a different procedure's sentence is not support; it
   is the original bug.
2. **Procedure identity.** A claim that names a procedure other than the one
   asked about is rejected outright, and support may only come from evidence
   about a compatible procedure (see :mod:`app.answer.procedures`).
3. **Same subject.** Multilingual similarity, used only to confirm a claim and
   a sentence are about the same thing — never on its own to decide that a
   specific requirement is supported, because measured, it cannot tell
   validation from renewal.

Unsupported and contradicted claims are removed, never softened. If nothing
factual survives, the caller may regenerate once from the valid evidence;
if that fails too, the reader is told the answer could not be verified.
Nothing here ever returns blank text for a reader to stare at.
"""

from __future__ import annotations

import hashlib
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from datetime import date
from enum import Enum
from typing import Callable, Sequence
from urllib.parse import urlsplit

from app.answer import procedures as procedure_model
from app.sources.claims import _EFFECTIVE, _FRENCH_MONTHS, _UNTIL, parse_date
from app.sources.conflict import _CONDITIONS, _WORD_NUMBERS

#: Similarity at or above which a claim and a sentence share a subject.
#: Calibrated on this corpus: paraphrases across English and French scored
#: 0.66-0.72, unrelated sentences near 0.40. Deliberately the *weaker* test —
#: facts and procedure identity do the deciding.
SUBJECT_SIMILARITY = 0.55
#: A claim with no checkable fact needs this much before it counts as
#: supported by meaning alone. Between the two it is only partial.
SUPPORT_SIMILARITY = 0.70


class ClaimType(str, Enum):
    REQUIREMENT = "requirement"
    DEADLINE = "deadline"
    DATE = "date"
    FEE = "fee"
    ELIGIBILITY = "eligibility"
    PROCEDURE = "procedure"
    AUTHORITY = "authority"
    LOCATION = "location"
    DOCUMENT = "document"
    DURATION = "duration"
    CONDITION = "condition"
    EXCEPTION = "exception"
    PORTAL = "portal"
    CONTACT = "contact"
    #: Connective or meta text ("the page doesn't say how long this takes").
    #: Kept, and never counted as a claim.
    NON_FACTUAL = "non_factual"


class Support(str, Enum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    UNSUPPORTED = "unsupported"
    CONTRADICTED = "contradicted"


class Action(str, Enum):
    KEPT = "kept"
    REMOVED = "removed"
    URL_REMOVED = "url_removed"


#: Types whose partial support is not enough. Getting any of these slightly
#: wrong is how someone misses a deadline, pays the wrong amount, or brings
#: the wrong papers.
HIGH_RISK = frozenset({
    ClaimType.DEADLINE, ClaimType.DATE, ClaimType.FEE, ClaimType.DURATION,
    ClaimType.ELIGIBILITY, ClaimType.REQUIREMENT, ClaimType.DOCUMENT,
    ClaimType.CONDITION, ClaimType.EXCEPTION, ClaimType.AUTHORITY,
    ClaimType.CONTACT,
})


def _fold(text: str) -> str:
    """Lowercase, accents removed, hyphens kept — they matter in ranges."""
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    folded = folded.replace("’", "'").replace(" ", " ").replace(" ", " ")
    return " ".join(folded.split())


# ---------------------------------------------------------------- facts ----

_NUMBER_WORDS = dict(_WORD_NUMBERS)
_NUMBER_WORDS.update({"a": "1", "an": "1", "quatorze": "14", "seize": "16",
                      "fifteen": "15", "sixteen": "16", "fourteen": "14",
                      "eleven": "11", "twenty-four": "24", "vingt-quatre": "24"})
_NUM = (r"\d+(?:[.,]\d+)?|"
        + "|".join(re.escape(w) for w in sorted(_NUMBER_WORDS, key=len, reverse=True)))
_UNIT_CANON = {
    "mois": "month", "month": "month", "months": "month",
    "jour": "day", "jours": "day", "day": "day", "days": "day",
    "semaine": "week", "semaines": "week", "week": "week", "weeks": "week",
    "an": "year", "ans": "year", "annee": "year", "annees": "year",
    "year": "year", "years": "year",
    "heure": "hour", "heures": "hour", "hour": "hour", "hours": "hour",
}
_UNIT = "|".join(sorted(_UNIT_CANON, key=len, reverse=True))
_QUANTITY = re.compile(
    rf"\b({_NUM})(?:\s*(?:-|–|to|a|ou|or|and|et)\s*({_NUM}))?\s+({_UNIT})\b")

_THOUSANDS = r"\d{1,3}(?:[ .]\d{3})+(?:,\d+)?|\d+(?:[.,]\d+)?"
_AMOUNT_AFTER = re.compile(rf"({_THOUSANDS})\s*(?:€|eur\b|euros?\b)")
_AMOUNT_BEFORE = re.compile(rf"€\s*({_THOUSANDS})")

_MONTHS = "|".join(sorted(_FRENCH_MONTHS, key=len, reverse=True))
_DATE_DAY_MONTH = re.compile(rf"\b(\d{{1,2}})(?:er|st|nd|rd|th)?\s+({_MONTHS})\s+(\d{{4}})\b")
_DATE_MONTH_DAY = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")
_DATE_SLASH = re.compile(r"\b(\d{1,2}/\d{1,2}/\d{4})\b")
_DATE_ISO = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_MONTH_YEAR = re.compile(rf"\b({_MONTHS})\s+(\d{{4}})\b")

_URL = re.compile(r"https?://[^\s)>\]\"'«»]+", re.IGNORECASE)
_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_PHONE = re.compile(r"(?:\+33\s*|\b0)[1-9](?:[\s.]*\d{2}){4}\b")

#: Named bodies, portals and tests. Vocabulary: which names a reader may be
#: told, and which must therefore be found in the evidence before they are.
ENTITIES: dict[str, tuple[str, ...]] = {
    "ANEF": (r"\banef\b", r"administration numerique (?:pour les )?etrangers"),
    "CAF": (r"\bcaf\b", r"caisse d'allocations familiales"),
    "CROUS": (r"\bcrous\b",),
    "Parcoursup": (r"\bparcoursup\b",),
    "Préfecture": (r"\bprefecture\b", r"\bprefet\b", r"\bprefect\b"),
    "OFII": (r"\bofii\b",),
    "France Titres": (r"\bants\b", r"\bfrance titres\b"),
    "France Travail": (r"\bfrance travail\b", r"\bpole emploi\b"),
    "Campus France": (r"\bcampus france\b",),
    "consulate": (r"\bconsulat", r"\bconsulate\b", r"\bembass", r"\bambassade\b"),
    "TOEIC": (r"\btoeic\b",), "TOEFL": (r"\btoefl\b",), "IELTS": (r"\bielts\b",),
    "Duolingo": (r"\bduolingo\b",), "Cambridge": (r"\bcambridge\b",),
    "ECTS": (r"\bects\b",),
    "DSE": (r"\bdse\b", r"dossier social etudiant"),
    "Ameli": (r"\bameli\b", r"\bcpam\b"),
}
_ENTITY_PATTERNS = {name: tuple(re.compile(p) for p in pats)
                    for name, pats in ENTITIES.items()}

#: What a deadline is counted from. "Before expiry" and "after arrival" are
#: different anchors, and one stated as the other is the original bug.
ANCHORS: dict[str, tuple[str, ...]] = {
    "before_expiry": (
        r"\bbefore\b.{0,40}\b(?:expir|end of (?:its |the |your )?validity|runs out)",
        r"\bavant\b.{0,40}\b(?:expiration|fin de (?:sa |la |leur )?validite|echeance)",
    ),
    "after_arrival": (
        r"\b(?:after|upon|on|from|within)\b.{0,30}\b(?:arriv|landing|land\b|entry|entering)",
        r"\bday (?:you|they) (?:land|arrive)", r"\bclock starts\b",
        r"\b(?:apres|des|a compter de|suivant|dans les)\b.{0,30}\b(?:arrivee|entree|arrive)",
    ),
    "before_arrival": (
        r"\bbefore\b.{0,30}\b(?:you )?(?:arrive|travel|leave|departure)\b",
        r"\bavant\b.{0,30}\b(?:votre )?(?:arrivee|depart|voyage)\b",
    ),
}
_ANCHOR_PATTERNS = {name: tuple(re.compile(p) for p in pats)
                    for name, pats in ANCHORS.items()}

_META = re.compile(
    r"\b(?:does ?n[o']?t (?:say|explain|state|spell|specify|mention|detail|cover)|"
    r"do ?n[o']?t (?:say|explain|state|specify|mention|detail|cover)|"
    r"not (?:stated|specified|mentioned|explained)|no (?:other|further) (?:steps|details)|"
    r"ne (?:precise|precisent|dit|disent|detaille|detaillent|mentionne|mentionnent|indique|indiquent|couvre|couvrent) pas|"
    r"n'(?:indique|indiquent|explique|expliquent|evoque|evoquent) pas|"
    r"check it against|verifiez|if you(?:'re| are) unsure|en cas de doute)\b")

_FACTUAL_VERBS = re.compile(
    r"\b(?:must|need(?:s)? to|needed|required|require[sd]?|have to|has to|should|"
    r"mandatory|obligatoire|devez|doit|doivent|il faut|necessaire|"
    r"apply|submit|file|validate|renew|pay|bring|provide|register|book|log in|"
    r"sign in|go to|fill|upload|send|attach|present|sit|pass|take|"
    r"deposer|demander|valider|renouveler|payer|fournir|joindre|"
    r"prendre rendez vous|se connecter|envoyer|presenter|"
    r"eligible|entitled|allowed|qualif|accepted|admitted|admission|handles?|"
    r"responsible|processes|issued|valid for|valable|delivre|"
    # How something is done is a claim too: "the application is made online"
    # states a procedure, and was once let through unchecked for lacking a
    # modal verb.
    r"online|en ligne|in person|en personne|by post|par courrier|"
    r"is made|are made|is done|se fait|se font|s'effectue|s'effectuent)\b")

_DOCUMENT_WORDS = re.compile(
    r"\b(?:document|documents|passport|passeport|proof|justificatif|certificate|"
    r"certificat|photo|diploma|diplome|transcript|releve|attestation|bill|facture|"
    r"form|formulaire|copy|copie)\b")
_ELIGIBILITY_WORDS = re.compile(
    r"\b(?:eligib|entitled|qualif|must be (?:a|an|the)|you must hold|"
    r"etre titulaire|avoir la qualite|conditions? d'(?:eligibilite|admission)|"
    r"no (?:minimum|pre ?selection))")
_EXCEPTION_WORDS = re.compile(r"\b(?:except|unless|sauf|excepte|hormis|a l'exception)\b")
_CONDITION_WORDS = re.compile(r"\b(?:if|when|provided|si|lorsque|a condition)\b")
_REQUIREMENT_WORDS = re.compile(
    r"\b(?:must|need|required|require|have to|has to|mandatory|obligatoire|"
    r"devez|doit|doivent|il faut)\b")
_AUTHORITY_VERBS = re.compile(
    r"\b(?:handles?|responsible|in charge|decides|processes|competent|competente|"
    r"gere|traite|s'adresser)\b")
_LOCATION_WORDS = re.compile(
    r"\b(?:address|adresse|rue|avenue|boulevard|place|located|situe|cedex|"
    r"on[- ]site|campus)\b")
_DEADLINE_WORDS = re.compile(
    r"\b(?:before|after|within|by|deadline|no later|at least|avant|apres|"
    r"au plus tard|dans un delai|des|a compter|au moins)\b")


@dataclass(frozen=True)
class Facts:
    """Everything in a sentence that can be compared exactly."""

    quantities: frozenset = frozenset()
    amounts: frozenset = frozenset()
    dates: frozenset = frozenset()
    months: frozenset = frozenset()
    urls: tuple = ()
    emails: frozenset = frozenset()
    phones: frozenset = frozenset()
    entities: frozenset = frozenset()
    anchors: frozenset = frozenset()

    @property
    def hard(self) -> bool:
        return bool(self.quantities or self.amounts or self.dates or self.months
                    or self.emails or self.phones)


def _number(token: str) -> str:
    folded = _fold(token)
    return _NUMBER_WORDS.get(folded, folded.replace(",", "."))


def _amount(token: str) -> str:
    raw = token.replace(" ", "").replace(".", "") if re.search(r"\d[ .]\d{3}", token) \
        else token
    raw = raw.replace(",", ".")
    try:
        value = float(raw)
    except ValueError:
        return raw
    return str(int(value)) if value == int(value) else str(value)


def extract_facts(text: str) -> Facts:
    """Pull the checkable facts out of one sentence."""
    folded = _fold(text)
    quantities = set()
    for first, second, unit in _QUANTITY.findall(folded):
        canon = _UNIT_CANON[unit]
        factor, canon = (7, "day") if canon == "week" else (1, canon)
        for token in (first, second):
            if token:
                value = _number(token)
                try:
                    number = float(value) * factor
                except ValueError:
                    continue
                quantities.add((str(int(number)) if number == int(number)
                                else str(number), canon))
    amounts = {_amount(m) for m in _AMOUNT_AFTER.findall(folded)}
    amounts |= {_amount(m) for m in _AMOUNT_BEFORE.findall(folded)}

    dates, months = set(), set()
    for day, month, year in _DATE_DAY_MONTH.findall(folded):
        iso = parse_date(f"{day} {month} {year}")
        if iso:
            dates.add(iso)
    for month, day, year in _DATE_MONTH_DAY.findall(folded):
        iso = parse_date(f"{day} {month} {year}")
        if iso:
            dates.add(iso)
    for token in _DATE_SLASH.findall(folded) + _DATE_ISO.findall(folded):
        iso = parse_date(token)
        if iso:
            dates.add(iso)
    for month, year in _MONTH_YEAR.findall(folded):
        months.add((int(year), _FRENCH_MONTHS[month]))
    months |= {(int(d[:4]), int(d[5:7])) for d in dates}

    urls = tuple(u.rstrip(".,;:!?") for u in _URL.findall(text or ""))
    emails = {e.lower() for e in _EMAIL.findall(text or "")}
    phones = {re.sub(r"\D", "", p)[-9:] for p in _PHONE.findall(text or "")}

    entity_text = _fold(_URL.sub(" ", text or "")).replace("-", " ")
    entities = {name for name, pats in _ENTITY_PATTERNS.items()
                if any(p.search(entity_text) for p in pats)}
    anchors = {name for name, pats in _ANCHOR_PATTERNS.items()
               if any(p.search(entity_text) for p in pats)}
    return Facts(frozenset(quantities), frozenset(amounts), frozenset(dates),
                 frozenset(months), urls, frozenset(emails), frozenset(phones),
                 frozenset(entities), frozenset(anchors))


def classify_claim(text: str, facts: Facts | None = None) -> ClaimType:
    """What kind of claim a sentence makes, or that it makes none."""
    facts = facts or extract_facts(text)
    folded = _fold(text).replace("-", " ")
    if not (facts.hard or facts.urls or facts.entities or facts.anchors
            or _FACTUAL_VERBS.search(folded)):
        return ClaimType.NON_FACTUAL
    if _META.search(folded) and not (facts.hard or facts.anchors):
        return ClaimType.NON_FACTUAL
    if facts.amounts:
        return ClaimType.FEE
    if (facts.quantities or facts.anchors) and (facts.anchors or _DEADLINE_WORDS.search(folded)):
        return ClaimType.DEADLINE
    if facts.dates or facts.months:
        return ClaimType.DATE
    if facts.quantities:
        return ClaimType.DURATION
    if facts.emails or facts.phones:
        return ClaimType.CONTACT
    if facts.urls:
        return ClaimType.PORTAL
    if facts.entities and _AUTHORITY_VERBS.search(folded):
        return ClaimType.AUTHORITY
    if _EXCEPTION_WORDS.search(folded):
        return ClaimType.EXCEPTION
    if _ELIGIBILITY_WORDS.search(folded):
        return ClaimType.ELIGIBILITY
    if _DOCUMENT_WORDS.search(folded):
        return ClaimType.DOCUMENT
    if facts.entities & {"ANEF", "Parcoursup", "DSE"}:
        return ClaimType.PORTAL
    if _CONDITION_WORDS.search(folded) and _REQUIREMENT_WORDS.search(folded):
        return ClaimType.CONDITION
    if _REQUIREMENT_WORDS.search(folded):
        return ClaimType.REQUIREMENT
    if _LOCATION_WORDS.search(folded):
        return ClaimType.LOCATION
    return ClaimType.PROCEDURE


# ------------------------------------------------------------- evidence ----

_SENTENCE_BREAK = re.compile(r"(?<=[.!?;:])\s+(?=[\"“«(]?[A-ZÀ-ÖØ-Þ0-9])")


def sentences_of(text: str) -> list[str]:
    """Sentences, treating a line break as a boundary — menus and lists are."""
    out = []
    for line in (text or "").splitlines():
        line = line.strip(" \t-*•·")
        if not line:
            continue
        out.extend(part.strip() for part in _SENTENCE_BREAK.split(line) if part.strip())
    return out


@dataclass
class EvidenceText:
    """One page as the validator sees it."""

    source_id: str
    url: str
    text: str
    title: str = ""
    canonical_url: str = ""
    version_id: str = ""
    retrieved_at: str = ""
    jurisdiction_area: str = ""
    is_local: bool = False

    @property
    def procedures(self) -> frozenset[str]:
        return procedure_model.of_page(self.title, self.canonical_url or self.url)


@dataclass
class _Sentence:
    text: str
    evidence: EvidenceText
    procedures: frozenset
    facts: Facts
    folded: str


def from_live(items) -> list[EvidenceText]:
    """Adapt live-source evidence."""
    out = []
    for e in items or []:
        out.append(EvidenceText(
            source_id=e.source_id, url=e.url, text=e.text, title=e.title,
            canonical_url=e.canonical_url, version_id=e.version_id,
            retrieved_at=e.retrieved_at, jurisdiction_area=e.jurisdiction_area,
            is_local=bool(e.jurisdiction_area)))
    return out


def from_hits(hits) -> list[EvidenceText]:
    """Adapt corpus passages."""
    out = []
    for h in hits or []:
        meta = getattr(h, "metadata", {}) or {}
        out.append(EvidenceText(
            source_id=meta.get("fiche_id", "") or "service-public",
            url=meta.get("source_url", ""), text=getattr(h, "text", "") or "",
            title=meta.get("fiche_title_fr", ""),
            canonical_url=meta.get("source_url", ""),
            version_id=f"{meta.get('fiche_id', '')}@{meta.get('last_updated', '')}"))
    return out


def filter_by_procedure(evidence: Sequence, asked: frozenset[str]) -> tuple[list, list]:
    """Keep evidence whose page is about a compatible procedure.

    Applied *before* the model is shown anything. The validator would catch
    a renewal deadline presented as a validation timing afterwards; not
    offering the renewal page as evidence for a validation question means
    the model is never tempted in the first place.
    """
    kept, dropped = [], []
    for item in evidence:
        title = getattr(item, "title", "")
        url = getattr(item, "canonical_url", "") or getattr(item, "url", "")
        page = procedure_model.of_page(title, url)
        (kept if procedure_model.compatible(page, asked) else dropped).append(item)
    return kept, dropped


# ------------------------------------------------------------ similarity ----

_VECTORS: dict[str, list[float]] = {}


def embedding_similarity(claims: list[str], sentences: list[str]) -> list[list[float]]:
    """Cosine similarity with the multilingual embedder retrieval already uses.

    Evidence sentences recur across questions, so their vectors are cached for
    the life of the process: a page read for one question costs nothing the
    next time it is checked.
    """
    from app.ingest.embed import embed_texts

    wanted = [t for t in dict.fromkeys(claims + sentences) if t not in _VECTORS]
    if wanted:
        for text, vector in zip(wanted, embed_texts(wanted)):
            _VECTORS[text] = vector
        if len(_VECTORS) > 20000:
            _VECTORS.clear()
    import numpy as np

    a = np.array([_VECTORS[c] for c in claims]) if claims else np.zeros((0, 1))
    b = np.array([_VECTORS[s] for s in sentences]) if sentences else np.zeros((0, 1))
    if not len(a) or not len(b):
        return [[0.0] * len(sentences) for _ in claims]
    return (a @ b.T).tolist()


def lexical_similarity(claims: list[str], sentences: list[str]) -> list[list[float]]:
    """Shared-word overlap. A fallback when the embedder is unavailable.

    Weak across languages, and it knows it: used only so a missing model
    fails closed (claims read as unsupported) rather than crashing an answer.
    """
    def words(text):
        return {w for w in re.findall(r"[a-z0-9]{3,}", _fold(text).replace("-", " "))}
    cs, ss = [words(c) for c in claims], [words(s) for s in sentences]
    return [[(len(c & s) / min(len(c), len(s))) if c and s else 0.0 for s in ss]
            for c in cs]


# ------------------------------------------------------------ validation ----

@dataclass
class ClaimResult:
    claim_id: str
    text: str
    claim_type: str
    support: str = ""
    reason: str = ""
    action: str = Action.KEPT.value
    source_ids: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    versions: list[str] = field(default_factory=list)
    procedures: list[str] = field(default_factory=list)
    similarity: float = 0.0
    final_text: str = ""

    @property
    def factual(self) -> bool:
        return self.claim_type != ClaimType.NON_FACTUAL.value


@dataclass
class Validation:
    text: str
    claims: list[ClaimResult] = field(default_factory=list)
    asked_procedures: list[str] = field(default_factory=list)
    conflicting: bool = False
    needs_repair: bool = False
    elapsed_ms: float = 0.0
    similarity_backend: str = ""

    @property
    def factual(self) -> list[ClaimResult]:
        return [c for c in self.claims if c.factual]

    @property
    def generated(self) -> int:
        return len(self.factual)

    @property
    def supported(self) -> int:
        return sum(1 for c in self.factual if c.action != Action.REMOVED.value)

    @property
    def removed(self) -> int:
        return sum(1 for c in self.factual if c.action == Action.REMOVED.value)

    @property
    def contradicted(self) -> int:
        return sum(1 for c in self.factual if c.support == Support.CONTRADICTED.value)

    @property
    def supported_ratio(self) -> float:
        return round(self.supported / self.generated, 3) if self.generated else 1.0

    def source_urls(self) -> set[str]:
        return {u for c in self.factual if c.action != Action.REMOVED.value
                for u in c.source_urls}

    def as_dict(self) -> dict:
        return {
            "claims_generated": self.generated,
            "claims_supported": self.supported,
            "claims_removed": self.removed,
            "claims_contradicted": self.contradicted,
            "supported_claim_ratio": self.supported_ratio,
            "conflicting_sources": self.conflicting,
            "needs_repair": self.needs_repair,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "similarity_backend": self.similarity_backend,
            "asked_procedures": self.asked_procedures,
            "claims": [asdict(c) for c in self.claims],
        }


def claim_id_for(text: str) -> str:
    return "c_" + hashlib.sha256(_fold(text).encode("utf-8")).hexdigest()[:10]


def _url_verified(url: str, evidence: Sequence[EvidenceText]) -> bool:
    """A URL is verified only if the evidence itself contains or is it."""
    def norm(u: str) -> str:
        parts = urlsplit(u.strip())
        return f"{parts.netloc.lower().removeprefix('www.')}{parts.path.rstrip('/')}"
    wanted = norm(url)
    for item in evidence:
        for known in (item.url, item.canonical_url):
            if known and norm(known) == wanted:
                return True
        if url.rstrip("/") in (item.text or ""):
            return True
    return False


def _temporally_valid(sentence: str, on: date | None) -> tuple[bool, str]:
    if on is None:
        return True, ""
    starts = _EFFECTIVE.search(sentence or "")
    if starts:
        iso = parse_date(starts.group(1))
        if iso and date.fromisoformat(iso) > on:
            return False, f"not yet in force on {on.isoformat()} (from {iso})"
    ends = _UNTIL.search(sentence or "")
    if ends:
        iso = parse_date(ends.group(1))
        if iso and date.fromisoformat(iso) < on:
            return False, f"no longer in force on {on.isoformat()} (until {iso})"
    return True, ""


#: Sentences scoring within this of the best are equally strong support.
_TIE = 0.02


def _out_of_force(candidates: list[int], pool, sims, on) -> str:
    """Why the strongest support is not in force on the asked date, or "".

    Judged over every sentence tied for strongest, not only the first. Picking
    one arbitrarily among equals once let an undated page title stand in for
    the sentence that actually carried the rule — "à compter du 1er juillet"
    — and a rule not yet in force was stated as current.
    """
    if on is None or not candidates:
        return ""
    top = max(sims[i] for i in candidates)
    for i in candidates:
        if sims[i] >= top - _TIE:
            ok, why = _temporally_valid(pool[i].text, on)
            if not ok:
                return why
    return ""


def _conditional(text: str) -> bool:
    folded = _fold(text)
    return any(marker in folded for marker in _CONDITIONS)


def validate(answer: str, evidence: Sequence[EvidenceText], *, question: str,
             on: date | None = None, place=None,
             similarity: Callable | None = None) -> Validation:
    """Check every claim in ``answer`` against ``evidence``, and repair it."""
    started = time.perf_counter()
    asked = procedure_model.detect(question)

    # Evidence a reader may lean on: the right procedure, the right place.
    usable: list[EvidenceText] = []
    for item in evidence:
        if place is not None and getattr(place, "known", False) and item.is_local \
                and item.jurisdiction_area \
                and _fold(item.jurisdiction_area) != _fold(place.department.name):
            continue
        usable.append(item)

    all_sentences: list[_Sentence] = []
    for item in usable:
        page = item.procedures
        for sentence in [item.title, *sentences_of(item.text)]:
            if not sentence:
                continue
            own = procedure_model.detect(sentence)
            all_sentences.append(_Sentence(
                text=sentence, evidence=item, procedures=own or page,
                facts=extract_facts(sentence), folded=_fold(sentence)))

    units = _units(answer)
    claims: list[ClaimResult] = []
    checkable: list[tuple[int, int, ClaimResult, Facts, str]] = []
    for u_index, (_, sentences) in enumerate(units):
        for s_index, sentence in enumerate(sentences):
            facts = extract_facts(sentence)
            kind = classify_claim(sentence, facts)
            result = ClaimResult(claim_id=claim_id_for(sentence), text=sentence,
                                 claim_type=kind.value, final_text=sentence)
            claims.append(result)
            if kind is ClaimType.NON_FACTUAL:
                continue
            probe = _URL.sub(" ", sentence)
            checkable.append((u_index, s_index, result, facts, probe))

    backend = "none"
    matrix: list[list[float]] = []
    # Only sentences from compatible procedures are ever compared by meaning.
    # Incompatible ones are still searched for exact figures — that is how a
    # renewal deadline is recognised as a renewal deadline — but embedding
    # them bought nothing and was most of the cost: a renewal page is long.
    compared = [i for i, s in enumerate(all_sentences)
                if procedure_model.compatible(s.procedures, asked)]
    if checkable and compared:
        texts = [probe for *_, probe in checkable]
        pool = [all_sentences[i].text for i in compared]
        if similarity is not None:
            partial, backend = similarity(texts, pool), "injected"
        else:
            try:
                partial, backend = embedding_similarity(texts, pool), "embedding"
            except Exception:  # noqa: BLE001 - a missing model must fail closed
                partial, backend = lexical_similarity(texts, pool), "lexical"
        matrix = [[0.0] * len(all_sentences) for _ in checkable]
        for row, values in enumerate(partial):
            for column, value in zip(compared, values):
                matrix[row][column] = value

    conflicting = False
    for row, (_, _, result, facts, probe) in enumerate(checkable):
        sims = matrix[row] if matrix else [0.0] * len(all_sentences)
        verdict = _judge(result, facts, probe, asked, all_sentences, sims, on, usable)
        if verdict == "conflict":
            conflicting = True

    text = _rebuild(units, claims)
    validation = Validation(
        text=text, claims=claims, asked_procedures=sorted(asked),
        conflicting=conflicting, similarity_backend=backend)
    validation.needs_repair = validation.generated > 0 and validation.supported == 0
    if validation.needs_repair:
        validation.text = ""
    validation.elapsed_ms = (time.perf_counter() - started) * 1000
    return validation


def _judge(result: ClaimResult, facts: Facts, probe: str, asked: frozenset,
           pool: list[_Sentence], sims: list[float], on: date | None,
           evidence: Sequence[EvidenceText]) -> str:
    """Decide one claim. Mutates ``result``; returns "conflict" when relevant."""
    kind = ClaimType(result.claim_type)
    own = procedure_model.detect(probe)
    result.procedures = sorted(own)

    def settle(support: Support, reason: str, supporters=()):
        result.support = support.value
        result.reason = reason
        keep = support is Support.SUPPORTED or (
            support is Support.PARTIALLY_SUPPORTED and kind not in HIGH_RISK
            and not facts.hard)
        result.action = Action.KEPT.value if keep else Action.REMOVED.value
        for s in supporters:
            if s.evidence.source_id not in result.source_ids:
                result.source_ids.append(s.evidence.source_id)
            url = s.evidence.canonical_url or s.evidence.url
            if url and url not in result.source_urls:
                result.source_urls.append(url)
            if s.evidence.version_id and s.evidence.version_id not in result.versions:
                result.versions.append(s.evidence.version_id)

    # Procedure mixing: the claim itself names a procedure the reader did not
    # ask about. "Validate your VLS-TS by filing the renewal request" names
    # two; the reader asked about one.
    if asked and own and not own <= asked:
        settle(Support.CONTRADICTED,
               f"procedure_mix: claim is about {procedure_model.label(own - asked)}, "
               f"question is about {procedure_model.label(asked)}")
        return ""

    compatible = [i for i, s in enumerate(pool)
                  if procedure_model.compatible(s.procedures, asked)]

    # URLs are stripped rather than trusted, and the sentence judged without
    # them. A portal address the evidence never gave is an invented one.
    bad_urls = [u for u in facts.urls if not _url_verified(u, evidence)]

    # Named bodies and portals must be named by compatible evidence — not
    # inferred from which government domain a page happens to sit on.
    compatible_text = " ".join(pool[i].folded for i in compatible).replace("-", " ")
    missing = sorted(name for name in facts.entities
                     if not any(p.search(compatible_text)
                                for p in _ENTITY_PATTERNS[name]))
    if missing:
        settle(Support.UNSUPPORTED,
               f"entity_not_in_evidence: {', '.join(missing)}")
        return ""

    if facts.hard or facts.anchors:
        return _judge_facts(result, facts, asked, pool, sims, compatible, on,
                            settle, bad_urls)

    best = max(compatible, key=lambda i: sims[i], default=None)
    score = sims[best] if best is not None else 0.0
    result.similarity = round(score, 3)
    if best is None or score < SUBJECT_SIMILARITY:
        settle(Support.UNSUPPORTED, f"no evidence on this subject (best {score:.2f})")
        return ""
    why = _out_of_force([i for i in compatible if sims[i] >= SUBJECT_SIMILARITY],
                        pool, sims, on)
    if why:
        settle(Support.UNSUPPORTED, why)
        return ""
    support = (Support.SUPPORTED if score >= SUPPORT_SIMILARITY
               else Support.PARTIALLY_SUPPORTED)
    settle(support, f"matched by meaning ({score:.2f})", [pool[best]])
    _strip_urls(result, bad_urls)
    return ""


def _judge_facts(result, facts: Facts, asked, pool, sims, compatible, on,
                 settle, bad_urls) -> str:
    # A claim with a timing anchor but no figure ("the clock starts the day
    # you land") is judged on the anchor alone. It must be checked first:
    # with no figures to carry, every sentence vacuously "carries" them, and
    # the claim was once filed as a renewal figure it never contained.
    if facts.anchors and not facts.hard:
        return _judge_anchor(result, facts, pool, sims, compatible, settle,
                             bad_urls)

    def carries(sentence: _Sentence) -> bool:
        f = sentence.facts
        return (facts.quantities <= f.quantities and facts.amounts <= f.amounts
                and facts.dates <= f.dates and facts.months <= f.months
                and facts.emails <= f.emails and facts.phones <= f.phones)

    def anchored(sentence: _Sentence) -> bool:
        return not facts.anchors or bool(facts.anchors & sentence.facts.anchors)

    exact = [i for i, s in enumerate(pool) if carries(s)]
    good = [i for i in exact if i in compatible and anchored(pool[i])
            and sims[i] >= SUBJECT_SIMILARITY]
    if good:
        best = max(good, key=lambda i: sims[i])
        result.similarity = round(sims[best], 3)
        why = _out_of_force(good, pool, sims, on)
        if why:
            settle(Support.UNSUPPORTED, why)
            return ""
        # Another authoritative, same-subject, unconditional sentence giving a
        # different figure for the same unit is a conflict, not a detail.
        rivals = _rivals(facts, pool[best], pool, sims, compatible)
        if rivals:
            winner = _resolve(pool[best], rivals[0])
            if winner is None:
                settle(Support.CONTRADICTED,
                       "conflicting_sources: official sources give different "
                       "figures and neither clearly governs")
                return "conflict"
            if winner is not pool[best]:
                settle(Support.CONTRADICTED,
                       f"superseded by {winner.evidence.source_id}")
                return "conflict"
        settle(Support.SUPPORTED, "facts match the evidence exactly", [pool[best]])
        _strip_urls(result, bad_urls)
        return ""

    # The figure exists, but only in another procedure's sentence, or counted
    # from a different moment. This is precisely the Antibes bug.
    elsewhere = [i for i in exact if i not in compatible]
    if elsewhere:
        source = pool[elsewhere[0]]
        settle(Support.CONTRADICTED,
               f"procedure_mix: this figure belongs to "
               f"{procedure_model.label(source.procedures)} "
               f"({source.evidence.source_id}), question is about "
               f"{procedure_model.label(asked) or 'another procedure'}")
        return ""
    misanchored = [i for i in exact if i in compatible and not anchored(pool[i])]
    if misanchored:
        source = pool[misanchored[0]]
        settle(Support.CONTRADICTED,
               f"anchor_mismatch: evidence counts from "
               f"{', '.join(sorted(source.facts.anchors)) or 'something else'}, "
               f"claim counts from {', '.join(sorted(facts.anchors))}")
        return ""

    differing = [i for i in compatible if sims[i] >= SUBJECT_SIMILARITY
                 and _same_dimension(facts, pool[i].facts)]
    if differing:
        settle(Support.CONTRADICTED,
               f"value_mismatch: evidence on this subject states "
               f"{_describe(pool[differing[0]].facts)}")
        return ""
    settle(Support.UNSUPPORTED, "no evidence states these figures")
    return ""


def _judge_anchor(result, facts: Facts, pool, sims, compatible, settle,
                  bad_urls=()) -> str:
    """A timing claim with no figure: its anchor must be in the evidence."""
    same = [i for i in compatible if facts.anchors & pool[i].facts.anchors
            and sims[i] >= SUBJECT_SIMILARITY]
    if same:
        best = max(same, key=lambda i: sims[i])
        result.similarity = round(sims[best], 3)
        settle(Support.SUPPORTED, "timing matches the evidence", [pool[best]])
        # Every supported path strips unverified URLs; this one once did not,
        # and kept an invented portal address because the timing was right.
        _strip_urls(result, list(bad_urls))
        return ""
    other = [i for i, s in enumerate(pool)
             if facts.anchors & s.facts.anchors and i not in compatible]
    if other:
        settle(Support.CONTRADICTED,
               f"procedure_mix: this timing belongs to "
               f"{procedure_model.label(pool[other[0]].procedures)}")
        return ""
    settle(Support.UNSUPPORTED,
           f"timing ({', '.join(sorted(facts.anchors))}) is not stated in any evidence")
    return ""


def _same_dimension(claim: Facts, other: Facts) -> bool:
    units = {u for _, u in claim.quantities}
    return bool((units and units & {u for _, u in other.quantities})
                or (claim.amounts and other.amounts)
                or ((claim.dates or claim.months) and (other.dates or other.months)))


def _describe(facts: Facts) -> str:
    parts = [f"{v} {u}" for v, u in sorted(facts.quantities)]
    parts += [f"{a} EUR" for a in sorted(facts.amounts)]
    parts += sorted(facts.dates)
    return ", ".join(parts) or "different figures"


def _rivals(facts: Facts, chosen: _Sentence, pool, sims, compatible) -> list[_Sentence]:
    if _conditional(chosen.text) or not (facts.quantities or facts.amounts):
        return []
    units = {u for _, u in facts.quantities}
    out = []
    for i in compatible:
        other = pool[i]
        if other is chosen or other.evidence.source_id == chosen.evidence.source_id:
            continue
        if sims[i] < SUPPORT_SIMILARITY or _conditional(other.text):
            continue
        same_units = {q for q in other.facts.quantities if q[1] in units}
        if same_units and not facts.quantities <= other.facts.quantities:
            out.append(other)
        elif facts.amounts and other.facts.amounts and \
                not facts.amounts <= other.facts.amounts:
            out.append(other)
    return out


def _resolve(a: _Sentence, b: _Sentence) -> _Sentence | None:
    """Which of two conflicting statements governs, if either clearly does.

    The more local authority wins over the national one — a préfecture's own
    counter rule is more specific than the national page — and a statement
    with a later effective date wins over an earlier one. Otherwise, nothing
    is chosen: averaging two official figures is how a reader gets neither.
    """
    if a.evidence.is_local != b.evidence.is_local:
        return a if a.evidence.is_local else b
    starts_a, starts_b = _EFFECTIVE.search(a.text), _EFFECTIVE.search(b.text)
    if starts_a and starts_b:
        da, db = parse_date(starts_a.group(1)), parse_date(starts_b.group(1))
        if da and db and da != db:
            return a if da > db else b
    return None


def _strip_urls(result: ClaimResult, bad_urls: list[str]) -> None:
    if not bad_urls or result.action == Action.REMOVED.value:
        return
    text = result.final_text
    for url in bad_urls:
        text = text.replace(url, "")
    text = re.sub(r"\s+(?:at|via|on|sur|à|a|:)\s*(?=[.,;]|$)", "", text)
    text = re.sub(r"\(\s*\)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r"\s+([.,;:])", r"\1", text)
    result.final_text = text
    result.action = Action.URL_REMOVED.value
    result.reason = (result.reason + "; " if result.reason else "") + \
        "unverified URL removed"


# -------------------------------------------------------------- rebuild ----

_LIST_PREFIX = re.compile(r"^(\s*(?:[-*•]|\d+[.)])\s+|\s*#{1,6}\s+)")


def _units(answer: str) -> list[tuple[str, list[str]]]:
    """(prefix, sentences) per line; a blank line is a paragraph break."""
    out = []
    for line in (answer or "").splitlines():
        if not line.strip():
            out.append(("", []))
            continue
        match = _LIST_PREFIX.match(line)
        prefix = match.group(1) if match else ""
        body = line[len(prefix):].strip()
        if prefix.strip().startswith("#"):
            out.append((prefix, [body] if body else []))
            continue
        out.append((prefix, [p.strip() for p in _SENTENCE_BREAK.split(body) if p.strip()]))
    return out


def _rebuild(units, claims: list[ClaimResult]) -> str:
    """Reassemble what survived, keeping lists, headings and paragraphs.

    A heading is kept only if something under it survived, so removing a
    section's claims does not leave an orphaned title behind.
    """
    lines: list[str] = []
    cursor = 0
    pending_heading = None
    for prefix, sentences in units:
        if not sentences:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        kept = []
        for _ in sentences:
            claim = claims[cursor]
            cursor += 1
            if claim.action != Action.REMOVED.value:
                kept.append(claim.final_text)
        if prefix.strip().startswith("#"):
            pending_heading = f"{prefix}{' '.join(kept)}" if kept else None
            continue
        if kept:
            if pending_heading:
                if lines and lines[-1] != "":
                    lines.append("")
                lines.append(pending_heading)
                pending_heading = None
            lines.append(f"{prefix}{' '.join(kept)}")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines).strip()


# ---------------------------------------------------------------- repair ----

def check_and_repair(text: str, evidence: Sequence[EvidenceText], *,
                     question: str, on: date | None = None, place=None,
                     regenerate: Callable[[list[EvidenceText]], str] | None = None,
                     similarity: Callable | None = None
                     ) -> tuple[Validation, int]:
    """Validate an answer; if nothing factual survives, regenerate once.

    Returns the final validation and how many repair generations were spent
    (zero or one). The repair is given only procedure-compatible evidence and
    is validated again. It never runs twice: a second failure means the
    evidence cannot support an answer, and saying so is the correct outcome.
    """
    validation = validate(text, evidence, question=question, on=on,
                          place=place, similarity=similarity)
    if not validation.needs_repair or regenerate is None:
        return validation, 0

    usable, _ = filter_by_procedure(evidence, procedure_model.detect(question))
    if not usable:
        return validation, 0
    try:
        second = (regenerate(list(usable)) or "").strip()
    except Exception:  # noqa: BLE001 - a failed repair leaves the honest outcome
        return validation, 1
    if not second:
        return validation, 1
    repaired = validate(second, evidence, question=question, on=on,
                        place=place, similarity=similarity)
    # Every claim either attempt made stays on record, so the log shows what
    # the first draft got wrong, not only what the second got right.
    repaired.claims = validation.claims + repaired.claims
    repaired.elapsed_ms += validation.elapsed_ms
    repaired.needs_repair = not repaired.text
    return repaired, 1


def record(validation: Validation, *, path: str, settings=None) -> None:
    """Write the claim log. Types, verdicts, reasons, sources — never text.

    The reader was promised their question is not kept, and an answer's own
    sentences are close enough to the question to count. What a maintainer
    needs to find a pattern is the verdict and the reason, which are kept;
    the words are available in a bug report the reader chooses to send.
    """
    try:
        from app.sources import store

        store.audit(
            "answer.claims", settings, path=path,
            generated=validation.generated, supported=validation.supported,
            removed=validation.removed, contradicted=validation.contradicted,
            supported_ratio=validation.supported_ratio,
            conflicting=validation.conflicting,
            elapsed_ms=round(validation.elapsed_ms, 1),
            claims=[{"claim_id": c.claim_id, "claim_type": c.claim_type,
                     "result": c.support, "reason": c.reason, "action": c.action,
                     "sources": c.source_ids, "procedures": c.procedures}
                    for c in validation.factual])
    except Exception:  # noqa: BLE001 - logging must never break an answer
        pass
