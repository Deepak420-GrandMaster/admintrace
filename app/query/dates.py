"""When the reader means.

Most questions are about now and say nothing about it. A few are not — "what
was the rule in June", "has this changed since last year", "does the September
rule still apply" — and answering those from the current page is wrong in a
way that is invisible to the reader, because the answer looks exactly like a
right one.

So the date a question is about is extracted before retrieval, and defaults to
today when the question does not say. Nothing here reconstructs history from
memory: a historical date only tells the source store *which stored version*
to read. If no version covers that date, the honest answer is that we cannot
verify what the rule was then.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

_MONTHS = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12,
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}

#: "in June 2026", "en juin 2026", "depuis juillet 2026".
_MONTH_YEAR = re.compile(
    r"\b(?:in|en|since|depuis|from|de|du|for|pour|avant|before|apres|after)?\s*"
    r"([a-z]+)\s+((?:19|20)\d{2})\b")

_YEAR_ONLY = re.compile(r"\b(?:in|en|year|annee)\s+((?:19|20)\d{2})\b")
_ISO = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")

_BEFORE = ("before", "avant", "prior to", "up to", "jusqu")
_AFTER = ("after", "apres", "since", "depuis", "from", "a partir de")
_NOW = ("now", "currently", "current", "today", "maintenant", "actuellement",
        "aujourd'hui", "aujourd hui", "actuel", "actuelle")
_CHANGED = ("changed", "change", "changes", "changé", "changement", "modifi",
            "nouveau", "new rule", "updated", "mise a jour")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(folded.replace("'", " ").split())


@dataclass(frozen=True)
class AskedAbout:
    """The moment a question is about."""

    #: The date to read the sources as of.
    on: date
    #: True when the reader named a past date rather than meaning now.
    historical: bool = False
    #: True when they are asking what changed, not what the rule is.
    about_change: bool = False
    #: The words that carried the date, for explaining the reading back.
    phrase: str = ""

    @property
    def is_now(self) -> bool:
        return not self.historical


def today() -> date:
    return datetime.now(tz=timezone.utc).date()


def parse(question: str, *, now: date | None = None) -> AskedAbout:
    """The date a question is about. Today, unless it says otherwise."""
    now = now or today()
    folded = _fold(question)
    about_change = any(word in folded for word in _CHANGED)

    iso = _ISO.search(question or "")
    if iso:
        try:
            when = date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
            return AskedAbout(on=when, historical=when < now,
                              about_change=about_change, phrase=iso.group(0))
        except ValueError:
            pass

    for match in _MONTH_YEAR.finditer(folded):
        month = _MONTHS.get(match.group(1))
        if not month:
            continue
        year = int(match.group(2))
        # The matched span carries its own preposition ("before july 2026"),
        # so looking only at the text in front of it misses the word entirely.
        context = match.group(0) + " " + folded[:match.start()][-24:]
        before = any(word in context for word in _BEFORE)
        if before:
            # "before July 2026" means the last day the old rule held.
            when = date(year, month, 1) - timedelta(days=1)
        else:
            # A month named without a day means the end of it, which is the
            # reading that covers the whole month the reader asked about.
            nxt = date(year + (month == 12), (month % 12) + 1, 1)
            when = min(nxt - timedelta(days=1), now) if year <= now.year else nxt - timedelta(days=1)
        return AskedAbout(on=when, historical=when < now,
                          about_change=about_change, phrase=match.group(0).strip())

    year_only = _YEAR_ONLY.search(folded)
    if year_only:
        year = int(year_only.group(1))
        when = date(year, 12, 31)
        if when > now:
            when = now
        return AskedAbout(on=when, historical=when < now,
                          about_change=about_change, phrase=year_only.group(0))

    if "last year" in folded or "annee derniere" in folded:
        return AskedAbout(on=date(now.year - 1, 12, 31), historical=True,
                          about_change=about_change, phrase="last year")

    return AskedAbout(on=now, historical=False, about_change=about_change,
                      phrase="now" if any(w in folded for w in _NOW) else "")
