"""Deciding whether a source may be queried live, and saying why not.

"Live query enabled" is a privilege, not a default. A source that is reachable
is not automatically a source worth reading at answer time: it may parse to
nothing useful, it may not state when it was last updated, it may be behind a
bot wall, or it may simply have nothing to do with the question being asked.

Turning it on for everything would be the easy mistake. It would mean every
question paid for network calls to sites that had nothing to contribute, and
it would put pages we have never successfully parsed in front of readers.

So a source is promoted through a gate with named checks, and a failure names
the check rather than saying no. Four outcomes:

* ``LIVE_QUERY`` — may be read at answer time;
* ``SYNC_ONLY``  — re-read on a schedule, not per question;
* ``CACHED_ONLY``— serve the stored version; do not go back to the site;
* ``UNAVAILABLE``— do not use as evidence at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.config import Settings, get_settings
from app.sources import store
from app.sources.registry import Health, Source, hostname_allowed


class QueryMode(str, Enum):
    LIVE_QUERY = "live_query"
    SYNC_ONLY = "sync_only"
    CACHED_ONLY = "cached_only"
    UNAVAILABLE = "unavailable"


@dataclass
class Check:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class GateResult:
    source_id: str
    mode: QueryMode
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return self.mode is QueryMode.LIVE_QUERY

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    def why(self) -> str:
        return "; ".join(f"{c.name}: {c.detail or 'failed'}" for c in self.failures)


def evaluate(source: Source, settings: Settings | None = None) -> GateResult:
    """Run every check, and let the worst failure choose the mode."""
    settings = settings or get_settings()
    checks: list[Check] = []

    checks.append(Check(
        "domain_verified", source.verified,
        "" if source.verified else "never confirmed against the live site"))

    extractable = source.health in (Health.HEALTHY, Health.HEALTHY_RENDERED,
                                    Health.CHANGED, Health.STALE)
    checks.append(Check(
        "extractable", extractable,
        "" if extractable else f"health is {source.health.value}"))

    # Security: https, and every domain it may answer on is one we listed.
    secure = source.base_url.startswith("https://") and all(
        hostname_allowed(source, domain) for domain in source.domains)
    checks.append(Check("security", secure,
                        "" if secure else "base_url or a domain fails validation"))

    authority = source.authority_level <= 2 and bool(source.name)
    checks.append(Check("authority", authority,
                        "" if authority else "no stated authority for anything"))

    relevant = bool(source.supported_topics or source.supported_entities)
    checks.append(Check(
        "relevance", relevant,
        "" if relevant else "declares no topic or entity, so nothing would route to it"))

    # Freshness metadata only works once something has actually been stored.
    stored = store.active(source.id, source.base_url, settings) is not None
    checks.append(Check(
        "freshness_metadata", stored,
        "" if stored else "no version stored yet; run sync_sources first"))

    # A block is deliberate on the site's part and is never routed around.
    if source.health is Health.BLOCKED:
        return GateResult(source.id, QueryMode.CACHED_ONLY
                          if stored else QueryMode.UNAVAILABLE, checks)
    if source.health in (Health.UNAVAILABLE, Health.PARSER_FAILURE):
        return GateResult(source.id, QueryMode.CACHED_ONLY
                          if stored else QueryMode.UNAVAILABLE, checks)
    if not source.verified:
        return GateResult(source.id, QueryMode.UNAVAILABLE, checks)
    if all(check.passed for check in checks):
        return GateResult(source.id, QueryMode.LIVE_QUERY, checks)
    # Verified and readable, but not ready to be read per question.
    return GateResult(source.id, QueryMode.SYNC_ONLY, checks)


def mode_for(source: Source, settings: Settings | None = None) -> QueryMode:
    """The mode a source is actually allowed to operate in right now.

    The registry flag is a permission, not a fact: a source marked
    ``live_query_enabled`` that has since gone dark drops back to its cached
    copy rather than being asked again on every question.
    """
    result = evaluate(source, settings)
    if result.mode is QueryMode.LIVE_QUERY and not source.live_query_enabled:
        # Eligible, but not switched on. Promotion is deliberate.
        return QueryMode.SYNC_ONLY
    return result.mode


def may_cite(source: Source, settings: Settings | None = None) -> bool:
    """Whether this source may appear in an answer at all, live or cached."""
    return mode_for(source, settings) in (QueryMode.LIVE_QUERY,
                                          QueryMode.SYNC_ONLY,
                                          QueryMode.CACHED_ONLY)
