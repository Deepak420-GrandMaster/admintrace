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
from app.sources import claims as claim_store
from app.sources import store
from app.sources.gating import evaluate, mode_for
from app.sources.registry import Health, load_registry

#: Why a source cannot currently be read, in words rather than a state name.
_ACCESS_REASON = {
    Health.BLOCKED: "bot protection; not bypassed",
    Health.PARSER_FAILURE: "fetched, but nothing readable came out",
    Health.UNAVAILABLE: "could not be reached",
    Health.UNVERIFIED: "never checked against the live site",
}


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
    claims = claim_store.load(source.id, current.version_id, settings) if current else []
    stale_claims = [c for c in claims if not c.is_current]
    dated = [c for c in claims if c.effective_from]

    conflicts = 0
    conflict_path = settings.data_dir / "sources" / "conflicts.jsonl"
    if conflict_path.exists():
        try:
            for line in conflict_path.read_text(encoding="utf-8").splitlines():
                if line.strip() and f'"{source.id}"' in line:
                    conflicts += 1
        except OSError:
            pass

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
        "access_reason": _ACCESS_REASON.get(source.health, ""),
        "claims": len(claims),
        "claims_stale": len(stale_claims),
        "claims_dated": len(dated),
        "conflicts": conflicts,
        "currentness": ("current" if current and not stale_claims
                        else "pending review" if stale_claims else "unknown"),
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
          f"{'CLAIMS':<7} {'CONF':<5} {'CURRENTNESS':<14} NEXT REFRESH")
    for row in rows:
        claims = f"{row['claims']}" + (f"/{row['claims_stale']}!"
                                       if row["claims_stale"] else "")
        print(f"{row['id']:<28} {row['health']:<18} {row['mode']:<12} "
              f"{row['parser_mode']:<9} {claims:<7} {row['conflicts']:<5} "
              f"{row['currentness']:<14} "
              f"{(row['next_refresh'] or '—')[:16]}{'  (due)' if row['due_now'] else ''}")
        if row["access_reason"]:
            print(f"{'':<28} {row['access_reason']}")

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
