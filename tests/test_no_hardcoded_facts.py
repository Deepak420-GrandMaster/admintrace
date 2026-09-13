"""The rule the whole project rests on: no administrative fact lives in code.

Deadlines, fees, hour limits, document lists and eligibility conditions must
reach a user only from a retrieved source document. If one of them is ever
written into source, a prompt, or a test, this fails.
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEARCHED = ["app", "eval"]

# A monetary amount stated in source would be a fee we invented.
AMOUNT = re.compile(r"\d\s*(?:€|EUR\b|euros?\b)", re.IGNORECASE)

# A duration stated in French is a deadline we invented.
DURATION = re.compile(
    r"\b\d+\s*(?:mois|jours?|semaines?|ans?|années?|heures?)\b", re.IGNORECASE
)


def python_files():
    for directory in SEARCHED:
        yield from (PROJECT_ROOT / directory).rglob("*.py")


def test_no_monetary_amounts_in_source():
    offenders = []
    for path in python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if AMOUNT.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "monetary amounts must come from retrieved content:\n" + "\n".join(offenders)


def test_no_french_durations_in_source():
    offenders = []
    for path in python_files():
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if DURATION.search(line):
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{number}: {line.strip()}")
    assert not offenders, "deadlines must come from retrieved content:\n" + "\n".join(offenders)
