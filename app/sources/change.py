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

    @property
    def is_substantive(self) -> bool:
        return self.change_type in (ChangeType.SUBSTANTIVE, ChangeType.CRITICAL)


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
        return report

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
    report.summary = ", ".join(parts)
    return report
