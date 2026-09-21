"""One command that says whether AdminTrace is well.

    uv run python -m app.healthcheck
    uv run python -m app.healthcheck --json
    uv run python -m app.healthcheck --write     # data/health/latest.json + history

Reads only — it never fetches, syncs or changes a source. It is safe to run
from a cron job every few minutes, and it is meant to be: the point is a
snapshot cheap enough to take often, so a trend is visible before somebody
notices a symptom.

Exit code is the useful part for a scheduler: non-zero when something needs a
person, so the scheduler's own failure notification does the alerting and
there is no second alerting system to keep alive.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.sources import incidents, review, store
from app.sources.gating import QueryMode, mode_for
from app.sources.registry import Health, load_registry
from app.status import Verification, assess, claim_metrics, history_metrics, \
    live_conflicts


def snapshot(settings) -> dict:
    sources = load_registry()
    capabilities = assess(settings)
    history = history_metrics(settings)

    citable = [s for s in sources if s.is_usable]
    live = [s for s in sources if mode_for(s, settings) is QueryMode.LIVE_QUERY]
    blocked = [s for s in sources if s.health is Health.BLOCKED]
    down = [s for s in sources if s.health is Health.UNAVAILABLE]
    due = [s for s in citable if store.due(s, settings)]

    changes = review.listing("changes", review.OPEN, settings)
    conflicts = review.listing("conflicts", review.OPEN, settings)
    open_incidents = incidents.open_incidents(settings)

    critical = [c for c in changes if c.get("severity") == "critical"]
    high = [c for c in changes if c.get("severity") == "high"]

    return {
        "checked_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "sources": {
            "registered": len(sources), "citable": len(citable),
            "live": len(live), "blocked": [s.id for s in blocked],
            "unavailable": [s.id for s in down],
        },
        "freshness": {"current": len(citable) - len(due),
                      "due": [s.id for s in due]},
        "review": {"critical": len(critical), "high": len(high),
                   "changes_open": len(changes), "conflicts_open": len(conflicts)},
        "incidents": {"open": len(open_incidents),
                      "detail": [{"source": i["source"], "kind": i["kind"],
                                  "count": i["occurrence_count"],
                                  "since": i["first_seen"]}
                                 for i in open_incidents]},
        "history": {key: {"versions": m["version_count"],
                          "days": m["days_of_history"], "depth": m["depth"]}
                    for key, m in history.items() if m["version_count"]},
        "claims": claim_metrics(settings),
        "conflicts_recorded": live_conflicts(settings),
        "scorecard": {key: cap.status.value for key, cap in capabilities.items()},
        "needs_attention": bool(critical or conflicts or open_incidents),
    }


def _prune_history(settings, keep_days: int = 30) -> int:
    """Health snapshots are for spotting trends, not for keeping forever."""
    directory = settings.data_dir / "health" / "history"
    if not directory.exists():
        return 0
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=keep_days)
    removed = 0
    for path in directory.glob("*.json"):
        try:
            when = datetime.fromisoformat(path.stem.replace("_", ":"))
        except ValueError:
            continue
        if when < cutoff:
            try:
                path.unlink()
                removed += 1
            except OSError:
                pass
    return removed


def write(settings, payload: dict) -> tuple:
    root = settings.data_dir / "health"
    (root / "history").mkdir(parents=True, exist_ok=True)
    latest = root / "latest.json"
    latest.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                      encoding="utf-8")
    stamp = payload["checked_at"].replace(":", "_")
    archived = root / "history" / f"{stamp}.json"
    archived.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    _prune_history(settings)
    return latest, archived


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.healthcheck",
                                     description="Is AdminTrace well? Reads only.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="save to data/health/latest.json and the history")
    args = parser.parse_args(argv)

    settings = get_settings()
    payload = snapshot(settings)

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        s, f, r, i = (payload["sources"], payload["freshness"],
                      payload["review"], payload["incidents"])
        print("ADMINTRACE HEALTH\n")
        print("Sources")
        print(f"  {s['citable']}/{s['registered']} citable · {s['live']} live")
        print(f"  {len(s['blocked'])} blocked{': ' + ', '.join(s['blocked']) if s['blocked'] else ''}")
        print(f"  {len(s['unavailable'])} unavailable"
              f"{': ' + ', '.join(s['unavailable']) if s['unavailable'] else ''}")
        print()
        print("Freshness")
        print(f"  {f['current']} current · {len(f['due'])} due a refresh")
        print()
        print("Review")
        print(f"  {r['critical']} critical · {r['high']} high · "
              f"{r['conflicts_open']} conflict(s)")
        print()
        print("History")
        deep = sorted(payload["history"].items(),
                      key=lambda kv: -kv[1]["versions"])[:5]
        for key, row in deep:
            print(f"  {key:<28} {row['versions']:>2} versions · "
                  f"{row['days']}d · {row['depth']}")
        if not deep:
            print("  nothing stored yet")
        print()
        print("Incidents")
        for row in i["detail"]:
            print(f"  {row['source']:<26} {row['kind']:<16} ×{row['count']} "
                  f"since {row['since'][:16]}")
        if not i["detail"]:
            print("  none open")
        print()
        proven = sum(1 for v in payload["scorecard"].values()
                     if v == Verification.PRODUCTION_VERIFIED.value)
        print(f"Scorecard: {proven}/{len(payload['scorecard'])} capabilities "
              f"have met real data")
        print(f"SMTP: {payload['scorecard'].get('smtp')}")
        print()
        print("NEEDS ATTENTION" if payload["needs_attention"] else "Nothing needs a person right now.")

    if args.write:
        latest, archived = write(settings, payload)
        if not args.json:
            print(f"\nwritten to {latest}")

    return 1 if payload["needs_attention"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
