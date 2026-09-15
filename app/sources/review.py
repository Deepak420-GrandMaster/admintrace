"""The queue of things a person still has to look at.

Change detection and conflict classification produce findings, and a finding
nobody reads is a finding that did not happen. So anything needing judgement is
written to a directory a maintainer can list, with a status they can move:

    data/review/changes/CHANGE-<id>.json
    data/review/conflicts/CONFLICT-<id>.json

Nothing here resolves anything automatically, and nothing is hidden once
written. A conflict between two official sources is exactly the sort of thing
that should require a human to have looked at it.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings

OPEN = "open"
REVIEWED = "reviewed"
RESOLVED = "resolved"
STATUSES = (OPEN, REVIEWED, RESOLVED)


def root(kind: str, settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.data_dir / "review" / kind
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def record_change(*, source_id: str, url: str, old_version: str,
                  new_version: str, change_type: str, severity: str,
                  summary: str, before_after: str = "",
                  affected_topics: list[str] | None = None,
                  affected_claims: list[str] | None = None,
                  cache_invalidated: bool = False,
                  notified: bool | None = None,
                  settings: Settings | None = None) -> Path:
    """File a real source change for review."""
    settings = settings or get_settings()
    change_id = f"CHANGE-{new_version}"
    record = {
        "change_id": change_id,
        "source": source_id,
        "url": url,
        "old_version": old_version,
        "new_version": new_version,
        "detected_at": _stamp(),
        "change_type": change_type,
        "severity": severity,
        "summary": summary,
        "before_after": before_after,
        "affected_topics": affected_topics or [],
        "affected_claims": affected_claims or [],
        "cache_invalidated": cache_invalidated,
        "notified": notified,
        "revalidation_status": "pending",
        "review_status": OPEN,
        "reviewed_at": "",
        "note": "",
    }
    path = root("changes", settings) / f"{change_id}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def record_conflict(conflict, *, settings: Settings | None = None) -> Path:
    """File a real contradiction between two official sources for review."""
    settings = settings or get_settings()
    payload = asdict(conflict) if is_dataclass(conflict) else dict(conflict)
    payload.update({
        "review_status": OPEN,
        "reviewed_at": "",
        "note": "",
        "recommended_resolution": payload.get("prefer", ""),
        "recommended_reason": payload.get("prefer_reason", ""),
    })
    path = root("conflicts", settings) / f"{payload['conflict_id']}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return path


def listing(kind: str, status: str | None = None,
            settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    rows = []
    for path in sorted(root(kind, settings).glob("*.json"), reverse=True):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if status and record.get("review_status") != status:
            continue
        rows.append(record)
    return rows


def set_status(kind: str, identifier: str, status: str, *, note: str = "",
               settings: Settings | None = None) -> bool:
    if status not in STATUSES:
        raise ValueError(f"{status!r} is not one of {', '.join(STATUSES)}")
    settings = settings or get_settings()
    path = root(kind, settings) / f"{identifier}.json"
    if not path.exists():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    record["review_status"] = status
    record["reviewed_at"] = _stamp()
    if note:
        record["note"] = note
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return True
