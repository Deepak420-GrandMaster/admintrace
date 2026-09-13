"""Detect whether a question was asked in English or French.

Written by hand rather than taken from a library. Discriminating between two
known languages is a much smaller problem than identifying one among a hundred,
and the questions here are short, which is exactly where general-purpose
detectors are least reliable. Doing it here also keeps the decision inspectable
in the debug panel instead of opaque.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Function words are the signal: they are frequent, short, and rarely shared.
FRENCH_MARKERS = frozenset("""
je j me moi mon ma mes mien nous notre nos vous votre vos il elle ils elles
le la les un une des du de au aux ce cet cette ces
est sont suis etes ai as avons avez ont etait
et ou mais donc car ne pas plus jamais rien
pour dans avec sans sur sous chez vers apres avant depuis pendant
que qui quoi dont ou quel quelle quels quelles comment pourquoi combien quand
puis dois doit peut peux faut faire demander obtenir
mois annee jour delai droit titre sejour etranger demarche
""".split())

ENGLISH_MARKERS = frozenset("""
i me my mine we our us you your he she it they them their
the a an this that these those
is are am was were be been do does did have has had
and or but so because not never nothing
for in with without on under at to from after before since during
what which who whom whose how why when where much many long
can could should would will need must get apply
month year day deadline right permit stay foreigner process
""".split())

_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)
# Characters that essentially only appear in French among this pair.
_FRENCH_LETTERS = set("àâäçéèêëîïôöùûüÿœ")


@dataclass(frozen=True)
class Detection:
    language: str  # "en" or "fr"
    confidence: float
    french_hits: int
    english_hits: int
    accented: int

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6


def _fold(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word.lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def detect(text: str) -> Detection:
    """Decide the language of a question. Mixed input resolves to English."""
    words = _WORD.findall(text.lower())
    french = sum(1 for w in words if _fold(w) in FRENCH_MARKERS)
    english = sum(1 for w in words if w in ENGLISH_MARKERS)
    accented = sum(1 for c in text.lower() if c in _FRENCH_LETTERS)

    # An accented character is strong evidence, but a single one should not
    # outweigh a sentence of English function words.
    french_score = french + min(accented, 3) * 0.75
    english_score = float(english)

    total = french_score + english_score
    if total == 0:
        # No usable signal: a bare term such as "récépissé" or "CAF".
        language = "fr" if accented else "en"
        return Detection(language, 0.0, french, english, accented)

    if french_score > english_score:
        return Detection("fr", french_score / total, french, english, accented)
    # Ties resolve to English, as specified for mixed input.
    return Detection("en", english_score / total, french, english, accented)
