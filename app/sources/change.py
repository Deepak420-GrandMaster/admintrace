"""Telling an administrative change from a redesign.

Official sites change constantly and almost none of it matters: a rotating
banner, a reordered menu, a new tracking script. Treating every byte
difference as news would re-index the corpus daily and cry wolf at a
maintainer until they stopped reading the alerts.

What matters is the part a reader would act on — which documents, which
conditions, which deadline, where to apply. So the comparison is not over the
markup or even the whole text, but over the *signals*: the lines that carry
administrative meaning, and every number and date on the page.

Nothing in this module states what any of those values should be. It only
notices that they are no longer what they were.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum

#: Words that mark a line as something a reader acts on. Both languages,
#: because the sources are French and the readers frequently are not.
_SIGNAL_WORDS = (
    "document", "documents", "justificatif", "justificatifs", "pièce", "pièces",
    "condition", "conditions", "éligib", "eligib", "requis", "required",
    "obligatoire", "mandatory", "délai", "deadline", "date limite",
    "tarif", "tarifs", "frais", "fee", "fees", "coût", "cost", "montant",
    "procédure", "procedure", "démarche", "demarche", "étape", "step",
    "candidature", "admission", "inscription", "apply", "application",
    "dossier", "formulaire", "form", "rendez-vous", "appointment",
    "contact", "téléphone", "adresse", "address",
)

_NUMBERS = re.compile(r"\b\d[\d\s.,/-]*\b")
_URLS = re.compile(r"https?://[^\s\"'<>]+")


class ChangeType(str, Enum):
    NONE = "none"
    COSMETIC = "cosmetic"
    SUBSTANTIVE = "substantive"
    CRITICAL = "critical"
    NEW = "new"


class Severity(str, Enum):
    """How much a maintainer should care, in the order they should care."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


#: What kind of administrative thing changed, and the words that betray it.
#: The words are matched against the lines that were added or removed, never
#: against the whole page: a site that merely mentions "deadline" somewhere
#: has not changed a deadline.
CATEGORY_WORDS: dict[str, tuple[str, ...]] = {
    "eligibility": ("eligib", "éligib", "condition", "qui peut", "who can",
                    "reserve", "réservé", "ouvert aux", "open to", "critere",
                    "critère"),
    "required_document": ("document", "justificatif", "piece", "pièce",
                          "attestation", "formulaire", "cerfa", "certificat",
                          "copie", "originaux"),
    "deadline": ("delai", "délai", "deadline", "date limite", "avant le",
                 "jusqu'au", "cloture", "clôture", "echeance", "échéance"),
    "fee": ("tarif", "frais", "fee", "cost", "cout", "coût", "montant",
            "gratuit", "payant", "prix"),
    "procedure": ("procedure", "procédure", "demarche", "démarche", "etape",
                  "étape", "step", "comment faire", "how to"),
    "application_portal": ("teleservice", "téléservice", "portail", "portal",
                           "en ligne", "online", "compte", "connexion", "login"),
    "contact": ("contact", "telephone", "téléphone", "courriel", "email",
                "guichet"),
    "address": ("adresse", "address", "situe", "situé", "rue", "avenue"),
    "jurisdiction": ("prefecture", "préfecture", "departement", "département",
                     "commune", "mairie", "consulat", "competent", "compétent"),
    "student_rule": ("etudiant", "étudiant", "crous", "universite", "université",
                     "campus", "scolarite", "scolarité", "admission"),
    "immigration_rule": ("sejour", "séjour", "visa", "etranger", "étranger",
                         "titre de sejour", "titre de séjour", "naturalisation",
                         "anef", "recepisse", "récépissé"),
    "housing_rule": ("logement", "bail", "loyer", "caution", "apl",
                     "allocation logement", "residence", "résidence"),
    "tax_rule": ("impot", "impôt", "fiscal", "declaration", "déclaration",
                 "revenus"),
    "health_rule": ("sante", "santé", "assurance maladie", "securite sociale",
                    "sécurité sociale", "carte vitale", "mutuelle"),
    "employment_rule": ("emploi", "travail", "contrat", "cdi", "cdd",
                        "alternance", "salaire", "chomage", "chômage"),
}

#: Severity per category. Anything that changes who qualifies, by when, or how
#: much it costs can send someone to a counter with the wrong expectation.
CATEGORY_SEVERITY: dict[str, Severity] = {
    "eligibility": Severity.CRITICAL,
    "deadline": Severity.CRITICAL,
    "fee": Severity.CRITICAL,
    "procedure": Severity.CRITICAL,
    "immigration_rule": Severity.CRITICAL,
    "required_document": Severity.HIGH,
    "application_portal": Severity.HIGH,
    "jurisdiction": Severity.HIGH,
    "student_rule": Severity.HIGH,
    "housing_rule": Severity.HIGH,
    "tax_rule": Severity.HIGH,
    "health_rule": Severity.HIGH,
    "employment_rule": Severity.HIGH,
    "contact": Severity.MEDIUM,
    "address": Severity.MEDIUM,
    "cosmetic": Severity.LOW,
}

#: Which parts of the product a change to this category makes doubtful. Used
#: to invalidate derived work without a developer listing it by hand.
CATEGORY_TOPICS: dict[str, tuple[str, ...]] = {
    "eligibility": ("admission", "residence_permit", "housing", "health", "work"),
    "required_document": ("documents", "admission", "residence_permit", "housing"),
    "deadline": ("deadline", "admission", "taxes"),
    "fee": ("tuition", "taxes"),
    "procedure": ("admission", "residence_permit", "documents"),
    "application_portal": ("admission", "residence_permit"),
    "jurisdiction": ("residence_permit", "documents"),
    "student_rule": ("admission", "programmes", "housing"),
    "immigration_rule": ("residence_permit", "documents"),
    "housing_rule": ("housing",),
    "tax_rule": ("taxes",),
    "health_rule": ("health",),
    "employment_rule": ("work",),
    "contact": ("contact",),
    "address": ("contact",),
}


#: A change in any of these is worth waking someone for.
_CRITICAL_WORDS = ("document", "justificatif", "pièce", "condition", "éligib",
                   "eligib", "délai", "deadline", "date limite", "obligatoire",
                   "required", "admission", "candidature")


@dataclass
class ChangeReport:
    change_type: ChangeType = ChangeType.NONE
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    numbers_changed: bool = False
    links_changed: bool = False
    title_changed: bool = False
    summary: str = ""
    #: What kind of administrative thing changed, most confident first.
    categories: list[str] = field(default_factory=list)
    severity: Severity = Severity.LOW
    #: Product areas this change makes doubtful.
    affected_topics: list[str] = field(default_factory=list)

    @property
    def is_substantive(self) -> bool:
        return self.change_type in (ChangeType.SUBSTANTIVE, ChangeType.CRITICAL)

    @property
    def needs_attention(self) -> bool:
        """Whether a person should be told, rather than only the log."""
        return self.severity in (Severity.CRITICAL, Severity.HIGH)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.split())


def signal_lines(text: str) -> set[str]:
    """The lines a reader would act on, folded so formatting is not news."""
    out = set()
    for line in (text or "").splitlines():
        folded = _fold(line)
        if len(folded.split()) < 3:
            continue
        if any(word in folded for word in (_fold(w) for w in _SIGNAL_WORDS)):
            out.add(folded)
    return out


def numbers_in(text: str) -> set[str]:
    return {" ".join(match.split()) for match in _NUMBERS.findall(text or "")}


def categorise(lines: list[str]) -> list[str]:
    """Which administrative categories these changed lines touch."""
    folded = _fold(" ".join(lines))
    hits = [(sum(1 for word in words if _fold(word) in folded), name)
            for name, words in CATEGORY_WORDS.items()]
    return [name for count, name in sorted(hits, reverse=True) if count]


def severity_of(categories: list[str]) -> Severity:
    order = [Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM, Severity.LOW]
    found = [CATEGORY_SEVERITY.get(name, Severity.LOW) for name in categories]
    for level in order:
        if level in found:
            return level
    return Severity.LOW


def topics_for(categories: list[str]) -> list[str]:
    out: list[str] = []
    for name in categories:
        for topic in CATEGORY_TOPICS.get(name, ()):
            if topic not in out:
                out.append(topic)
    return out


def compare(old_text: str, new_text: str, *, old_title: str = "",
            new_title: str = "") -> ChangeReport:
    """What changed between two versions of the same page."""
    if not old_text:
        return ChangeReport(change_type=ChangeType.NEW, summary="first version")
    if _fold(old_text) == _fold(new_text) and old_title == new_title:
        return ChangeReport(change_type=ChangeType.NONE, summary="identical")

    old_signals, new_signals = signal_lines(old_text), signal_lines(new_text)
    added = sorted(new_signals - old_signals)
    removed = sorted(old_signals - new_signals)

    report = ChangeReport(
        added=added[:20],
        removed=removed[:20],
        numbers_changed=numbers_in(old_text) != numbers_in(new_text),
        links_changed=set(_URLS.findall(old_text)) != set(_URLS.findall(new_text)),
        title_changed=bool(old_title) and old_title != new_title,
    )

    if not added and not removed and not report.title_changed:
        report.change_type = ChangeType.COSMETIC
        report.summary = "wording or layout only; nothing a reader acts on changed"
        report.severity = Severity.LOW
        report.categories = ["cosmetic"]
        return report

    report.categories = categorise(added + removed)
    report.severity = severity_of(report.categories)
    report.affected_topics = topics_for(report.categories)

    touched = " ".join(added + removed)
    critical = any(_fold(word) in _fold(touched) for word in _CRITICAL_WORDS)
    report.change_type = ChangeType.CRITICAL if critical else ChangeType.SUBSTANTIVE
    parts = []
    if added:
        parts.append(f"{len(added)} line(s) added")
    if removed:
        parts.append(f"{len(removed)} line(s) removed")
    if report.title_changed:
        parts.append("title changed")
    if report.numbers_changed:
        parts.append("numbers changed")
    if report.categories:
        parts.append("touches " + ", ".join(report.categories[:3]))
    report.summary = ", ".join(parts)
    return report
