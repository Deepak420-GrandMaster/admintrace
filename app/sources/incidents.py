"""What has been going wrong with a source, and for how long.

A source that fails once is noise. A source that has failed every hour for two
days is a decision — and the difference is invisible if each failure simply
overwrites the last. So failures accumulate into an incident with a first
sighting, a latest sighting and a count, and an incident is *resolved* rather
than deleted when the source recovers.

That resolution matters as much as the failure. CAF is blocked today; when its
bot wall comes down, the system should notice and say so, rather than staying
marked blocked because nobody re-read the note.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings

#: The kinds of failure worth telling apart. A timeout and a bot wall need
#: completely different responses, and "error" tells you neither.
KINDS = ("http_error", "timeout", "dns", "forbidden", "bot_wall",
         "parser_failure", "render_failure", "redirect_change",
         "domain_change", "unknown")

OPEN = "open"
RESOLVED = "resolved"


def _path(settings: Settings) -> Path:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    path = settings.data_dir / "sources" / "incidents.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load(settings: Settings) -> dict:
    path = _path(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(settings: Settings, data: dict) -> None:
    try:
        _path(settings).write_text(json.dumps(data, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    except OSError:
        pass


def classify(error: str, status: int = 0) -> str:
    """What kind of failure this is, from what the fetcher reported."""
    text = (error or "").lower()
    if "bot protection" in text or "perfdrive" in text or "captcha" in text:
        return "bot_wall"
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "nodename" in text or "dns" in text or "name or service" in text:
        return "dns"
    if "redirect to" in text:
        return "redirect_change"
    if "no usable content" in text or "parsed to nothing" in text:
        return "parser_failure"
    if "render" in text:
        return "render_failure"
    if status == 403 or "403" in text:
        return "forbidden"
    if status >= 400:
        return "http_error"
    return "unknown"


def record(source_id: str, *, kind: str, detail: str = "", status: int = 0,
           settings: Settings | None = None) -> dict:
    """Note a failure, accumulating rather than overwriting."""
    settings = settings or get_settings()
    data = _load(settings)
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    key = f"{source_id}:{kind}"

    existing = data.get(key)
    if existing and existing.get("current_status") == OPEN:
        existing["last_seen"] = now
        existing["occurrence_count"] = existing.get("occurrence_count", 1) + 1
        existing["detail"] = detail or existing.get("detail", "")
    else:
        data[key] = {
            "source": source_id, "kind": kind, "detail": detail,
            "status": status, "first_seen": now, "last_seen": now,
            "occurrence_count": 1, "current_status": OPEN, "resolved_at": "",
        }
    _save(settings, data)
    return data[key]


def resolve(source_id: str, settings: Settings | None = None) -> list[dict]:
    """Close every open incident for a source that is healthy again."""
    settings = settings or get_settings()
    data = _load(settings)
    now = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    recovered = []
    for key, incident in data.items():
        if incident.get("source") == source_id and \
                incident.get("current_status") == OPEN:
            incident["current_status"] = RESOLVED
            incident["resolved_at"] = now
            recovered.append(incident)
    if recovered:
        _save(settings, data)
    return recovered


def open_incidents(settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    return [i for i in _load(settings).values()
            if i.get("current_status") == OPEN]


def history(source_id: str | None = None,
            settings: Settings | None = None) -> list[dict]:
    settings = settings or get_settings()
    rows = list(_load(settings).values())
    if source_id:
        rows = [r for r in rows if r.get("source") == source_id]
    return sorted(rows, key=lambda r: r.get("last_seen", ""), reverse=True)
