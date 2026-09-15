"""Versions of what a source said, and when we last checked.

Every fetch that produces a usable page becomes a version, keyed by the page
and stamped with a content hash. Versions are kept, so a bad one can be rolled
back and a reader can be told which wording an answer was drawn from.

The rule that matters most is the one about *not* writing: a fetch that comes
back as an error page, a consent wall, a captcha or an empty JavaScript shell
parses perfectly well and says nothing. Activating it would silently replace a
good answer with no answer. So a version is only activated if it is usable,
and the previous one stands otherwise.

    data/sources/versions/<source>/<page>/<version>.json   the versions
    data/sources/index.json                                 active pointers
    data/sources/audit.jsonl                                every decision
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path

from app.config import Settings, get_settings
from app.sources.change import ChangeReport, ChangeType, compare
from app.sources.extract import Page


class Freshness(str, Enum):
    #: Verified against the live site during this request.
    LIVE_VERIFIED = "live_verified"
    #: Stored, and inside the source's freshness window.
    FRESH = "fresh"
    #: Stored, but older than the source's freshness window.
    STALE = "stale"
    #: Never successfully retrieved.
    UNKNOWN = "unknown"
    #: Known, but the last attempt to reach it failed.
    UNAVAILABLE = "unavailable"


@dataclass
class Version:
    source_id: str
    url: str
    canonical_url: str
    version_id: str
    retrieved_at: str
    content_hash: str
    title: str
    text: str
    language: str = ""
    updated: str = ""
    previous_version: str | None = None
    change_type: str = ChangeType.NEW.value
    change_summary: str = ""
    word_count: int = 0
    headings: list[str] = field(default_factory=list)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _stamp() -> str:
    return _now().isoformat(timespec="seconds")


def _page_key(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:20]


def root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).data_dir / "sources"


def _versions_dir(settings: Settings, source_id: str, url: str) -> Path:
    path = root(settings) / "versions" / source_id / _page_key(url)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path(settings: Settings) -> Path:
    root(settings).mkdir(parents=True, exist_ok=True)
    return root(settings) / "index.json"


def _load_index(settings: Settings) -> dict:
    path = _index_path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_index(settings: Settings, index: dict) -> None:
    try:
        _index_path(settings).write_text(
            json.dumps(index, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def audit(event: str, settings: Settings | None = None, **fields) -> None:
    """Append-only record of every decision this layer made."""
    settings = settings or get_settings()
    root(settings).mkdir(parents=True, exist_ok=True)
    row = {"at": _stamp(), "event": event, **fields}
    try:
        with (root(settings) / "audit.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def active(source_id: str, url: str, settings: Settings | None = None) -> Version | None:
    """The version currently answering for this page."""
    settings = settings or get_settings()
    entry = _load_index(settings).get(f"{source_id}:{_page_key(url)}")
    if not entry:
        return None
    path = _versions_dir(settings, source_id, url) / f"{entry['version_id']}.json"
    try:
        return Version(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def history(source_id: str, url: str, settings: Settings | None = None) -> list[Version]:
    settings = settings or get_settings()
    out = []
    for path in sorted(_versions_dir(settings, source_id, url).glob("*.json")):
        try:
            out.append(Version(**json.loads(path.read_text(encoding="utf-8"))))
        except (OSError, ValueError, TypeError):
            continue
    return sorted(out, key=lambda v: v.retrieved_at)


def freshness(source_id: str, url: str, freshness_hours: int,
              settings: Settings | None = None) -> Freshness:
    current = active(source_id, url, settings)
    if current is None:
        return Freshness.UNKNOWN
    try:
        age = _now() - datetime.fromisoformat(current.retrieved_at)
    except ValueError:
        return Freshness.UNKNOWN
    return Freshness.FRESH if age <= timedelta(hours=freshness_hours) else Freshness.STALE


def record(source_id: str, url: str, page: Page, content_hash: str, *,
           settings: Settings | None = None,
           activate: bool = True) -> tuple[Version | None, ChangeReport]:
    """Store a fetched page as a version, and decide whether it takes over.

    Returns ``(None, report)`` when the page was refused as unusable — the
    previous version keeps answering and the refusal is written to the audit
    log rather than swallowed.
    """
    settings = settings or get_settings()
    previous = active(source_id, url, settings)

    if not page.is_usable:
        audit("version.refused", settings, source=source_id, url=url,
              reason="page had no usable content",
              words=page.word_count, title=page.title[:120],
              kept_version=previous.version_id if previous else None)
        return None, ChangeReport(change_type=ChangeType.NONE,
                                  summary="refused: no usable content")

    report = compare(previous.text if previous else "", page.text,
                     old_title=previous.title if previous else "",
                     new_title=page.title)

    if previous is not None and previous.content_hash == content_hash:
        audit("version.unchanged", settings, source=source_id, url=url,
              version=previous.version_id)
        return previous, ChangeReport(change_type=ChangeType.NONE,
                                      summary="identical content")

    version = Version(
        source_id=source_id,
        url=url,
        canonical_url=page.canonical_url or url,
        version_id=f"{_now().strftime('%Y%m%dT%H%M%SZ')}-{content_hash[:8]}",
        retrieved_at=_stamp(),
        content_hash=content_hash,
        title=page.title,
        text=page.text,
        language=page.language,
        updated=page.updated,
        previous_version=previous.version_id if previous else None,
        change_type=report.change_type.value,
        change_summary=report.summary,
        word_count=page.word_count,
        headings=page.headings,
    )

    path = _versions_dir(settings, source_id, url) / f"{version.version_id}.json"
    try:
        path.write_text(json.dumps(asdict(version), ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError as exc:
        audit("version.write_failed", settings, source=source_id, url=url, error=str(exc))
        return None, report

    if activate:
        _activate(settings, source_id, url, version, report)
    else:
        audit("version.stored_only", settings, source=source_id, url=url,
              version=version.version_id, change=report.change_type.value)
    return version, report


def due(source, settings: Settings | None = None) -> bool:
    """Whether this source is past its own refresh interval.

    Interval comes from the source's importance, not from one number for the
    whole registry: an immigration portal going out of date matters in hours,
    a glossary of terms in weeks.
    """
    settings = settings or get_settings()
    current = active(source.id, source.base_url, settings)
    if current is None:
        return True
    try:
        age = _now() - datetime.fromisoformat(current.retrieved_at)
    except ValueError:
        return True
    return age > timedelta(hours=source.refresh_hours)


def _invalidation_path(settings: Settings) -> Path:
    root(settings).mkdir(parents=True, exist_ok=True)
    return root(settings) / "invalidations.json"


def mark_topics_stale(topics: list[str], source_id: str, version_id: str,
                      settings: Settings | None = None) -> None:
    """Record that work resting on these topics can no longer be trusted.

    An answer knows which source versions supported it. This file is the other
    half: when a source moves on, the topics it covered are stamped, and
    anything cached against an older stamp is regenerated rather than served.
    """
    if not topics:
        return
    settings = settings or get_settings()
    path = _invalidation_path(settings)
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        current = {}
    stamp = _stamp()
    for topic in topics:
        current[topic] = {"at": stamp, "source": source_id, "version": version_id}
    try:
        path.write_text(json.dumps(current, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    except OSError:
        pass


def topic_invalidated_at(topic: str, settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    try:
        return json.loads(_invalidation_path(settings).read_text(encoding="utf-8")) \
            .get(topic, {}).get("at", "")
    except (OSError, ValueError):
        return ""


def answer_is_current(source_versions: dict[str, str],
                      settings: Settings | None = None) -> bool:
    """Whether every version an answer rested on is still the active one.

    ``source_versions`` maps ``"<source_id>|<url>"`` to the version id the
    answer used. A single moved page makes the whole answer stale: it was
    assembled from a set of statements, and one of them has changed.
    """
    settings = settings or get_settings()
    for key, version_id in (source_versions or {}).items():
        source_id, _, url = key.partition("|")
        current = active(source_id, url, settings)
        if current is None or current.version_id != version_id:
            return False
    return True


def _activate(settings: Settings, source_id: str, url: str, version: Version,
              report: ChangeReport) -> None:
    index = _load_index(settings)
    index[f"{source_id}:{_page_key(url)}"] = {
        "source_id": source_id, "url": url,
        "canonical_url": version.canonical_url,
        "version_id": version.version_id,
        "retrieved_at": version.retrieved_at,
        "content_hash": version.content_hash,
    }
    _save_index(settings, index)
    audit("version.activated", settings, source=source_id, url=url,
          version=version.version_id, previous=version.previous_version,
          change=report.change_type.value, severity=report.severity.value,
          categories=report.categories[:5],
          affected_topics=report.affected_topics,
          summary=report.summary)
    if report.is_substantive:
        invalidate(source_id, url, settings)
        mark_topics_stale(report.affected_topics, source_id,
                          version.version_id, settings)
        if report.needs_attention:
            # Loud in the log, so a scheduled run can surface it without a
            # person reading every line.
            audit("source.change_needs_review", settings, source=source_id,
                  url=url, severity=report.severity.value,
                  categories=report.categories[:5], summary=report.summary,
                  version=version.version_id)


def rollback(source_id: str, url: str, settings: Settings | None = None) -> Version | None:
    """Put the previous version back in charge."""
    settings = settings or get_settings()
    versions = history(source_id, url, settings)
    if len(versions) < 2:
        return None
    target = versions[-2]
    index = _load_index(settings)
    index[f"{source_id}:{_page_key(url)}"] = {
        "source_id": source_id, "url": url,
        "canonical_url": target.canonical_url,
        "version_id": target.version_id,
        "retrieved_at": target.retrieved_at,
        "content_hash": target.content_hash,
    }
    _save_index(settings, index)
    audit("version.rolled_back", settings, source=source_id, url=url,
          version=target.version_id)
    invalidate(source_id, url, settings)
    return target


def invalidate(source_id: str, url: str, settings: Settings | None = None) -> None:
    """Drop cached work derived from this page.

    A substantive change means every answer built on the old wording is now
    wrong, and the fetch cache would keep serving it.
    """
    settings = settings or get_settings()
    from app.sources.fetch import _cache_file  # local: avoids an import cycle
    try:
        _cache_file(settings, url).unlink(missing_ok=True)
    except OSError:
        pass
    # The answerability verdict cache is keyed on question plus passages, so a
    # changed page must not keep its old verdict.
    verdicts = settings.cache_dir / "answerability.json"
    try:
        if verdicts.exists():
            verdicts.unlink()
    except OSError:
        pass
    audit("cache.invalidated", settings, source=source_id, url=url)
