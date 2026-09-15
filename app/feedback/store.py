"""Where a bug report lives, and how it moves.

One directory per report, named by an id a person can say out loud
(``BUG-20260914-001``), under the status it is currently in::

    data/bugs/open/BUG-20260914-001/report.json
    data/bugs/in_progress/…
    data/bugs/resolved/…
    data/bugs/archived/…

The status is the directory, not a field inside the file, so a maintainer can
see the state of the queue with ``ls`` and change it with ``mv`` if they would
rather not use the CLI. The JSON carries a ``status`` key as well, kept in step
whenever this module moves a report, because the file has to make sense on its
own once it has been copied out or attached to an email.

Nothing secret is ever written here. What the reporter typed, what they were
asking at the time, and which pages the answer used — that is the whole of it.
The environment block is model names and thresholds; there is no path through
this module that can reach an API key, a token or a cookie.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings

#: The queue, in the order work moves through it.
STATUSES = ("open", "in_progress", "resolved", "archived")

_ID = re.compile(r"^BUG-(\d{8})-(\d{3})$")


class UnknownStatus(ValueError):
    """Raised for a status outside :data:`STATUSES`."""


def _check(status: str) -> str:
    if status not in STATUSES:
        raise UnknownStatus(
            f"{status!r} is not a bug status; expected one of {', '.join(STATUSES)}")
    return status


def root(settings: Settings | None = None) -> Path:
    return (settings or get_settings()).bugs_dir


def status_dir(status: str, settings: Settings | None = None) -> Path:
    return root(settings) / _check(status)


def ensure_dirs(settings: Settings | None = None) -> None:
    for status in STATUSES:
        status_dir(status, settings).mkdir(parents=True, exist_ok=True)


def _same_day_ids(settings: Settings, stamp: str) -> set[str]:
    seen: set[str] = set()
    for status in STATUSES:
        directory = status_dir(status, settings)
        if not directory.exists():
            continue
        for child in directory.iterdir():
            if child.is_dir() and child.name.startswith(f"BUG-{stamp}-"):
                seen.add(child.name)
    return seen


def claim_id(settings: Settings | None = None, *, now: datetime | None = None
             ) -> tuple[str, Path]:
    """Reserve the next free id for today and create its directory.

    The directory is created with ``exist_ok=False``, so two reports submitted
    in the same second cannot be handed the same id: the loser of the race
    sees the directory already exists and takes the next number.
    """
    settings = settings or get_settings()
    ensure_dirs(settings)
    stamp = (now or datetime.now(tz=timezone.utc)).strftime("%Y%m%d")
    taken = _same_day_ids(settings, stamp)

    number = len(taken) + 1
    while number < 1000:
        bug_id = f"BUG-{stamp}-{number:03d}"
        if bug_id not in taken:
            directory = status_dir("open", settings) / bug_id
            try:
                directory.mkdir(parents=False, exist_ok=False)
                return bug_id, directory
            except FileExistsError:
                pass
        number += 1
    raise RuntimeError(f"no free bug id left for {stamp}")


def find(bug_id: str, settings: Settings | None = None) -> tuple[str, Path] | None:
    """Locate a report by id, whatever status it currently sits in."""
    settings = settings or get_settings()
    if not _ID.match(bug_id):
        return None
    for status in STATUSES:
        directory = status_dir(status, settings) / bug_id
        if directory.is_dir():
            return status, directory
    return None


def read(bug_id: str, settings: Settings | None = None) -> dict | None:
    located = find(bug_id, settings)
    if located is None:
        return None
    path = located[1] / "report.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def listing(status: str | None = None, settings: Settings | None = None) -> list[dict]:
    """Every report, newest id first, optionally narrowed to one status."""
    settings = settings or get_settings()
    wanted = (_check(status),) if status else STATUSES
    rows: list[dict] = []
    for state in wanted:
        directory = status_dir(state, settings)
        if not directory.exists():
            continue
        for child in sorted(directory.iterdir(), reverse=True):
            if not child.is_dir():
                continue
            try:
                record = json.loads((child / "report.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            record["status"] = state
            rows.append(record)
    rows.sort(key=lambda r: r.get("id", ""), reverse=True)
    return rows


def set_status(bug_id: str, status: str, settings: Settings | None = None,
               *, resolution: str | None = None) -> Path:
    """Move a report into another status, keeping the JSON in step."""
    settings = settings or get_settings()
    _check(status)
    located = find(bug_id, settings)
    if located is None:
        raise FileNotFoundError(f"no such bug: {bug_id}")

    current, directory = located
    if current == status and resolution is None:
        return directory

    target = status_dir(status, settings) / bug_id
    if current != status:
        target.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(target)
    else:
        target = directory

    path = target / "report.json"
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return target
    record["status"] = status
    if resolution is not None:
        record["resolution"] = resolution
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return target


def archive_resolved(settings: Settings | None = None) -> list[str]:
    """Clear the resolved queue into the archive. Returns what moved."""
    settings = settings or get_settings()
    moved = []
    for record in listing("resolved", settings):
        bug_id = record.get("id", "")
        if bug_id:
            set_status(bug_id, "archived", settings)
            moved.append(bug_id)
    return moved


def write(record: dict, directory: Path) -> Path:
    path = directory / "report.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path
