"""How much of Claré has actually been proven, and by what.

Every capability here is *derived* from evidence on disk — versions, claims,
audit records, registry health — and none of it is asserted in code. That is
the whole design. A hand-maintained scorecard drifts the moment someone is
optimistic in a hurry, and the failure mode is the worst one available: a team
believing a capability is proven because a file said so.

The distinction that keeps this honest is between a mechanism that works and a
mechanism that has met real data:

``PRODUCTION_VERIFIED``   implementation, automated test, *and* a successful
                          real-world observation.
``MECHANISM_VERIFIED``    implementation and automated test; the real world
                          has not yet produced an instance.
``FIXTURE_VERIFIED``      proven against synthetic data only.
``NOT_OBSERVED``          nothing has happened yet to observe.
``INSUFFICIENT_HISTORY``  the capability works; the archive is too shallow.
``NOT_CONFIGURED``        needs credentials or settings nobody has supplied.
``AUTHORITY_UNAVAILABLE`` the right body is known and cannot be reached.
``BLOCKED`` / ``FAILED``  refused by the source, or broken.

Change detection is a good example. It is tested both ways and wired into the
scheduler, and no registered source has actually changed since baselining. It
is therefore ``MECHANISM_VERIFIED``, not ``PRODUCTION_VERIFIED``, and stays
that way until a real change lands in the audit log.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from app.config import Settings, get_settings
from app.sources import claims as claim_store
from app.sources import store
from app.sources.gating import QueryMode, mode_for
from app.sources.jurisdiction import departments
from app.sources.registry import Health, JurisdictionLevel, load_registry


class Verification(str, Enum):
    PRODUCTION_VERIFIED = "production_verified"
    MECHANISM_VERIFIED = "mechanism_verified"
    FIXTURE_VERIFIED = "fixture_verified"
    NOT_OBSERVED = "not_observed"
    INSUFFICIENT_HISTORY = "insufficient_history"
    NOT_CONFIGURED = "not_configured"
    AUTHORITY_UNAVAILABLE = "authority_unavailable"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass
class Capability:
    name: str
    status: Verification
    evidence: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def is_production(self) -> bool:
        return self.status is Verification.PRODUCTION_VERIFIED


def _audit_rows(settings: Settings) -> list[dict]:
    path = store.root(settings) / "audit.jsonl"
    if not path.exists():
        return []
    rows = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return rows


def history_metrics(settings: Settings | None = None) -> dict:
    """Version depth per source, and how far back the archive actually goes."""
    settings = settings or get_settings()
    per_source: dict[str, dict] = {}
    now = datetime.now(tz=timezone.utc)

    for source in load_registry():
        # Every page stored for this source, not only its entry point — a
        # metric that counts the homepage alone reports an archive far
        # shallower than the one that exists.
        versions = []
        source_dir = store.root(settings) / "versions" / source.id
        if source_dir.is_dir():
            for page_dir in source_dir.iterdir():
                if not page_dir.is_dir():
                    continue
                for path in sorted(page_dir.glob("*.json")):
                    try:
                        versions.append(store.Version(
                            **json.loads(path.read_text(encoding="utf-8"))))
                    except (OSError, ValueError, TypeError):
                        continue
        versions.sort(key=lambda v: v.retrieved_at)
        if not versions:
            per_source[source.id] = {"version_count": 0, "days_of_history": 0,
                                     "oldest_version": "", "newest_version": "",
                                     "last_change": ""}
            continue
        oldest, newest = versions[0], versions[-1]
        try:
            days = (now - datetime.fromisoformat(oldest.retrieved_at)).days
        except ValueError:
            days = 0
        changed = next((v for v in reversed(versions)
                        if v.change_type in ("substantive", "critical")), None)
        per_source[source.id] = {
            "version_count": len(versions),
            "days_of_history": days,
            "oldest_version": oldest.retrieved_at,
            "newest_version": newest.retrieved_at,
            "last_change": changed.retrieved_at if changed else "",
        }
    return per_source


def claim_metrics(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    active = stale = dated = 0
    for source in load_registry():
        current = store.active(source.id, source.base_url, settings)
        if current is None:
            continue
        for item in claim_store.load(source.id, current.version_id, settings):
            if item.is_current:
                active += 1
            else:
                stale += 1
            if item.effective_from:
                dated += 1
    return {"active": active, "stale": stale, "with_effective_date": dated}


def live_conflicts(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    path = settings.data_dir / "sources" / "conflicts.jsonl"
    if not path.exists():
        return 0
    try:
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines()
                   if line.strip())
    except OSError:
        return 0


def assess(settings: Settings | None = None) -> dict[str, Capability]:
    """The scorecard, computed from what is actually on disk."""
    settings = settings or get_settings()
    sources = load_registry()
    rows = _audit_rows(settings)
    history = history_metrics(settings)
    claims = claim_metrics(settings)

    live = [s for s in sources if mode_for(s, settings) is QueryMode.LIVE_QUERY]
    rendered = [s for s in live if s.health is Health.HEALTHY_RENDERED]
    local = [s for s in live if s.jurisdiction is JurisdictionLevel.DEPARTMENT]
    institution = [s for s in live if s.jurisdiction is JurisdictionLevel.INSTITUTION]
    national = [s for s in live if s.jurisdiction is JurisdictionLevel.NATIONAL]
    blocked = [s for s in sources if s.health is Health.BLOCKED]
    unavailable = [s for s in sources if s.health is Health.UNAVAILABLE]

    real_changes = [r for r in rows
                    if r.get("event") == "version.activated"
                    and r.get("change") in ("substantive", "critical")]
    deepest = max((m["days_of_history"] for m in history.values()), default=0)
    most_versions = max((m["version_count"] for m in history.values()), default=0)

    def cap(name, status, evidence, **detail):
        return Capability(name=name, status=status, evidence=evidence, detail=detail)

    out: dict[str, Capability] = {}

    out["national_routing"] = cap(
        "National source routing",
        Verification.PRODUCTION_VERIFIED if national else Verification.NOT_OBSERVED,
        f"{len(national)} national sources answering live",
        sources=[s.id for s in national])

    out["local_jurisdiction"] = cap(
        "Local jurisdiction routing",
        Verification.PRODUCTION_VERIFIED if local else Verification.NOT_OBSERVED,
        f"{len(local)} préfectures live, {len(departments())} départements mapped",
        sources=[s.id for s in local])

    out["institution_routing"] = cap(
        "Institution routing",
        Verification.PRODUCTION_VERIFIED if institution else Verification.NOT_OBSERVED,
        f"{len(institution)} institutions answering live",
        sources=[s.id for s in institution])

    out["live_retrieval"] = cap(
        "Live retrieval",
        Verification.PRODUCTION_VERIFIED if live else Verification.NOT_OBSERVED,
        f"{len(live)} sources live-query enabled")

    out["rendered_retrieval"] = cap(
        "Rendered retrieval",
        Verification.PRODUCTION_VERIFIED if rendered else Verification.NOT_OBSERVED,
        f"{len(rendered)} sources readable only after rendering",
        sources=[s.id for s in rendered])

    fresh = [s for s in sources if s.is_usable and not store.due(s, settings)]
    out["freshness"] = cap(
        "Source freshness",
        Verification.PRODUCTION_VERIFIED if fresh else Verification.NOT_OBSERVED,
        f"{len(fresh)} sources within their own refresh window")

    out["claim_provenance"] = cap(
        "Claim provenance",
        Verification.PRODUCTION_VERIFIED if claims["active"] else Verification.NOT_OBSERVED,
        f"{claims['active']} claims carrying source, version and jurisdiction",
        **claims)

    out["effective_dates"] = cap(
        "Effective dates",
        Verification.PRODUCTION_VERIFIED if claims["with_effective_date"]
        else Verification.MECHANISM_VERIFIED,
        f"{claims['with_effective_date']} live claims state a start date"
        if claims["with_effective_date"]
        else "parsing and enforcement tested; no live page has stated one yet")

    # Works, but the archive is one day deep. Saying otherwise would imply an
    # archive that does not exist.
    enough = (deepest >= settings.minimum_historical_days
              and most_versions >= settings.minimum_historical_versions)
    out["historical_retrieval"] = cap(
        "Historical retrieval",
        Verification.PRODUCTION_VERIFIED if enough else Verification.INSUFFICIENT_HISTORY,
        f"deepest archive {deepest}d / {most_versions} versions; "
        f"needs {settings.minimum_historical_days}d / "
        f"{settings.minimum_historical_versions} versions",
        days=deepest, versions=most_versions)

    conflicts = live_conflicts(settings)
    out["conflict_detection"] = cap(
        "Conflict detection",
        Verification.PRODUCTION_VERIFIED if conflicts else Verification.MECHANISM_VERIFIED,
        f"{conflicts} live conflicts recorded" if conflicts
        else "classification tested both ways; no live conflict observed yet",
        live=conflicts)

    out["change_detection"] = cap(
        "Change detection",
        Verification.PRODUCTION_VERIFIED if real_changes else Verification.MECHANISM_VERIFIED,
        f"{len(real_changes)} real changes detected" if real_changes
        else "tested cosmetic and critical; no registered source has changed "
             "since baselining",
        live=len(real_changes))

    out["smtp"] = cap(
        "Email delivery",
        Verification.NOT_CONFIGURED if not settings.email_configured
        else Verification.MECHANISM_VERIFIED,
        "no SMTP host or recipient configured; nothing has been sent"
        if not settings.email_configured
        else "configured; run `python -m app.test_email --send` to prove it")

    caf = next((s for s in sources if s.id == "caf"), None)
    out["caf"] = cap(
        "CAF current verification",
        Verification.BLOCKED if caf and caf.health is Health.BLOCKED
        else Verification.PRODUCTION_VERIFIED if caf and caf.is_usable
        else Verification.AUTHORITY_UNAVAILABLE,
        "authority known; current source behind bot protection and not bypassed"
        if caf and caf.health is Health.BLOCKED else "")

    out["blocked_sources"] = cap(
        "Blocked sources", Verification.BLOCKED if blocked else Verification.NOT_OBSERVED,
        ", ".join(s.id for s in blocked) or "none",
        sources=[s.id for s in blocked])

    out["failed_sources"] = cap(
        "Unreachable sources",
        Verification.FAILED if unavailable else Verification.NOT_OBSERVED,
        ", ".join(s.id for s in unavailable) or "none",
        sources=[s.id for s in unavailable])

    return out


def write_scorecard(settings: Settings | None = None) -> Path:
    """Persist the derived status so nobody has to remember it.

    Machine-readable on purpose: a claim about readiness should be checkable
    by a script, not by asking whoever last touched the code.
    """
    settings = settings or get_settings()
    capabilities = assess(settings)
    payload = {
        "generated_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "note": ("Derived from data on disk, never hand-edited. "
                 "production_verified requires a real-world observation, not "
                 "only a passing test."),
        "capabilities": {key: asdict(cap) | {"status": cap.status.value}
                         for key, cap in capabilities.items()},
    }
    path = settings.data_dir / "production_status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path
