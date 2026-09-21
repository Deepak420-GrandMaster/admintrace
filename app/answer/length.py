"""How long an answer should be, and whether it was.

AdminTrace's job is to make somebody read less, not to demonstrate that it read a
lot. More sources must never mean a longer answer — the extra sources are so
the relevant part can be *found*, not so all of them can be summarised.

Length is judged against what the question actually is. "What is the deposit
called?" and "How do I apply, and by when?" are not the same ask, and one
ceiling for both either truncates the second or licenses padding in the first.

This measures and reports. It never truncates: cutting an answer at a word
count is how a condition that changes who qualifies gets dropped, and a short
wrong answer is worse than a long right one.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum


class AnswerClass(str, Enum):
    MICRO = "micro"
    STANDARD = "standard"
    PROCEDURAL = "procedural"
    COMPLEX = "complex"


#: (floor, ceiling) in words. The ceiling is the number that matters.
BANDS = {
    AnswerClass.MICRO: (20, 60),
    AnswerClass.STANDARD: (50, 120),
    AnswerClass.PROCEDURAL: (80, 180),
    AnswerClass.COMPLEX: (150, 300),
}

#: A question asking for a sequence, not a fact.
_PROCEDURAL = ("how do i", "how can i", "how to", "what do i need", "steps",
               "procedure", "apply", "renew", "register", "comment", "quelles",
               "demarche", "démarche", "renouveler", "postuler", "candidater")

#: A question that spans several procedures, bodies or dates at once.
_COMPLEX = (" and ", " or ", "deadline", "timeline", "calendar", "both",
            "difference between", "as well as", "et aussi", "ainsi que",
            "international student", "plus", "also")

#: A question with one short answer.
_MICRO = ("what is", "what's", "is it", "are there", "can i", "do i need",
          "when is", "where is", "how much", "qu'est-ce", "est-ce que",
          "combien", "quand", "ou est")


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


def classify(question: str) -> AnswerClass:
    """What sort of answer this question deserves."""
    folded = _fold(question)
    procedural = any(word in folded for word in _PROCEDURAL)
    complex_ = sum(1 for word in _COMPLEX if word in folded) >= 2

    if procedural and complex_:
        return AnswerClass.COMPLEX
    if procedural:
        return AnswerClass.PROCEDURAL
    if any(folded.startswith(word) or f" {word}" in folded for word in _MICRO):
        return AnswerClass.MICRO
    return AnswerClass.STANDARD


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


@dataclass
class LengthCheck:
    answer_class: AnswerClass
    words: int
    floor: int
    ceiling: int

    @property
    def over(self) -> bool:
        return self.words > self.ceiling

    @property
    def ok(self) -> bool:
        return not self.over

    def describe(self) -> str:
        verdict = "over" if self.over else "within"
        return (f"{self.words} words, {verdict} the "
                f"{self.answer_class.value} ceiling of {self.ceiling}")


def check(question: str, answer: str) -> LengthCheck:
    answer_class = classify(question)
    floor, ceiling = BANDS[answer_class]
    return LengthCheck(answer_class=answer_class, words=word_count(answer),
                       floor=floor, ceiling=ceiling)
