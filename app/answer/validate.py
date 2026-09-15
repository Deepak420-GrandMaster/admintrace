"""The last check before an answer is shown.

Every earlier layer can be right and the result still be wrong in one specific
way: the evidence that arrived is not evidence for *this* question. A question
about one school answered from a government page, a Montpellier question
answered from the Rhône préfecture, a question about September answered from a
rule that stopped applying in June — each of these looks like a good answer and
is not one.

None of these are hypothetical failure modes. Every one of them is something
this system did at some point during its construction, and each was found by
looking at output rather than by reading code.

So the assembled evidence is checked against the question it is supposed to
answer, and a failed check downgrades the answer rather than being logged and
ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit


@dataclass
class Finding:
    check: str
    passed: bool
    detail: str = ""


@dataclass
class Validation:
    findings: list[Finding] = field(default_factory=list)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.passed]

    @property
    def ok(self) -> bool:
        return not self.failures

    def why(self) -> str:
        return "; ".join(f"{f.check}: {f.detail}" for f in self.failures)


def _host(url: str) -> str:
    return (urlsplit(url or "").hostname or "").lower()


def check_entity(evidence, *, entity_source_id: str) -> Finding:
    """A question about one body must be answered from that body.

    Supplementary government pages are fine alongside it; a government page
    *instead* of it is the failure — and it is the one that reads most like a
    real answer, because the page it cites is genuinely official.
    """
    if not entity_source_id:
        return Finding("entity", True, "no entity in play")
    from_entity = [e for e in evidence if e.source_id == entity_source_id]
    if not from_entity:
        cited = sorted({e.source_id for e in evidence})
        return Finding("entity", False,
                       f"asked about {entity_source_id}, cited {cited or 'nothing'}")
    return Finding("entity", True, f"{len(from_entity)} page(s) from the body itself")


def check_jurisdiction(evidence, *, expected_area: str) -> Finding:
    """A local answer must come from the reader's own local authority."""
    if not expected_area:
        return Finding("jurisdiction", True, "no place in play")
    local = [e for e in evidence if getattr(e, "jurisdiction_area", "")]
    wrong = [e for e in local
             if e.jurisdiction_area and e.jurisdiction_area != expected_area]
    if wrong:
        return Finding("jurisdiction", False,
                       f"reader is in {expected_area}, cited "
                       f"{sorted({e.jurisdiction_area for e in wrong})}")
    return Finding("jurisdiction", True, f"local evidence is {expected_area} or national")


def check_dates(claims, *, on: date) -> Finding:
    """Evidence must have been in force on the date being asked about."""
    if not claims:
        return Finding("effective_dates", True, "no dated claims in play")
    out_of_force = [c for c in claims if not c.applies_on(on)]
    if out_of_force:
        return Finding("effective_dates", False,
                       f"{len(out_of_force)} claim(s) not in force on {on}")
    return Finding("effective_dates", True, f"all claims in force on {on}")


def check_provenance(evidence) -> Finding:
    """Everything cited must be traceable to a stored version."""
    if not evidence:
        return Finding("provenance", True, "nothing cited")
    missing = [e for e in evidence
               if not (e.version_id and e.content_hash and e.canonical_url)]
    if missing:
        return Finding("provenance", False,
                       f"{len(missing)} page(s) without a version or hash")
    return Finding("provenance", True, f"{len(evidence)} page(s) fully traceable")


def check_freshness(evidence, *, allow_stale: bool = False) -> Finding:
    """Stale evidence may be used, but never labelled current."""
    from app.sources.store import Freshness
    stale = [e for e in evidence if e.freshness is Freshness.STALE]
    if stale and not allow_stale:
        return Finding("freshness", False,
                       f"{len(stale)} page(s) are stale and would read as current")
    return Finding("freshness", True, "no stale evidence presented as current")


def check_conflicts(conflicts) -> Finding:
    if conflicts:
        return Finding("conflicts", False,
                       f"{len(conflicts)} unresolved contradiction(s)")
    return Finding("conflicts", True, "no unresolved contradiction")


def validate(evidence, *, entity_source_id: str = "", expected_area: str = "",
             claims=None, on: date | None = None, conflicts=None,
             allow_stale: bool = False) -> Validation:
    """Run every check. A failure downgrades the answer; it is not advisory."""
    on = on or date.today()
    return Validation(findings=[
        check_provenance(evidence),
        check_entity(evidence, entity_source_id=entity_source_id),
        check_jurisdiction(evidence, expected_area=expected_area),
        check_dates(claims or [], on=on),
        check_freshness(evidence, allow_stale=allow_stale),
        check_conflicts(conflicts or []),
    ])
