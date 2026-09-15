"""What every registered source is doing right now.

    uv run python -m app.source_report
    uv run python -m app.source_report --json

Reads only. This is the view a maintainer wants before deciding whether a
source is worth promoting, demoting or chasing — health, whether it is being
queried live, when it was last read, when it last changed, and when it is next
due.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.sources import store
from app.sources.gating import evaluate, mode_for
from app.sources.registry import load_registry


def describe(source, settings) -> dict:
    current = store.active(source.id, source.base_url, settings)
    history = store.history(source.id, source.base_url, settings)
    changed = next((v for v in reversed(history)
                    if v.change_type in ("substantive", "critical")), None)

    next_due = ""
    if current:
        try:
            when = datetime.fromisoformat(current.retrieved_at) \
                + timedelta(hours=source.refresh_hours)
            next_due = when.isoformat(timespec="minutes")
        except ValueError:
            next_due = ""

    gate = evaluate(source, settings)
    return {
        "id": source.id,
        "name": source.name,
        "authority": f"L{source.authority_level}",
        "type": source.source_type.value,
        "jurisdiction": (f"{source.jurisdiction.value}"
                         + (f"/{source.jurisdiction_area}"
                            if source.jurisdiction_area else "")),
        "health": source.health.value,
        "mode": mode_for(source, settings).value,
        "live_query": source.live_query_enabled,
        "parser_mode": "rendered" if source.render_enabled else "static",
        "last_checked": current.retrieved_at if current else "",
        "last_change": changed.retrieved_at if changed else "",
        "current_version": current.version_id if current else "",
        "versions": len(history),
        "refresh_priority": source.refresh_priority,
        "next_refresh": next_due,
        "due_now": store.due(source, settings),
        "gate_failures": [c.name for c in gate.failures],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.source_report",
                                     description="Status of every registered source.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    rows = [describe(s, settings) for s in load_registry()]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    print(f"{'SOURCE':<28} {'HEALTH':<18} {'MODE':<12} {'PARSER':<9} "
          f"{'JURISDICTION':<22} {'VER':<4} NEXT REFRESH")
    for row in rows:
        print(f"{row['id']:<28} {row['health']:<18} {row['mode']:<12} "
              f"{row['parser_mode']:<9} {row['jurisdiction']:<22} "
              f"{row['versions']:<4} "
              f"{(row['next_refresh'] or '—')[:16]}{'  (due)' if row['due_now'] else ''}")

    live = sum(1 for r in rows if r["mode"] == "live_query")
    cached = sum(1 for r in rows if r["mode"] == "cached_only")
    sync = sum(1 for r in rows if r["mode"] == "sync_only")
    down = sum(1 for r in rows if r["mode"] == "unavailable")
    print()
    print(f"{len(rows)} registered · live {live} · sync-only {sync} · "
          f"cached-only {cached} · unavailable {down}")
    changed = [r for r in rows if r["last_change"]]
    if changed:
        print(f"last substantive change: "
              + ", ".join(f"{r['id']} ({r['last_change'][:10]})" for r in changed[:5]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
