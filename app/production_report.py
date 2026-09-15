"""What Claré can actually be trusted to do today.

    uv run python -m app.production_report
    uv run python -m app.production_report --json
    uv run python -m app.production_report --write   # data/production_status.json

Every line is derived from data on disk. Nothing here is a claim somebody
typed, which is the point: a readiness report that can be edited by hand
becomes a readiness report that is wrong.

The column that matters is the status, and the distinction it protects is
between "this works" and "this has met the real world". A capability with a
passing test and no real-world instance is ``mechanism_verified``, and stays
that way until one arrives.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from app.config import get_settings
from app.sources import store
from app.sources.gating import QueryMode, mode_for
from app.sources.jurisdiction import departments
from app.sources.registry import Health, load_registry
from app.status import Verification, assess, claim_metrics, history_metrics, \
    live_conflicts, write_scorecard

_MARK = {
    Verification.PRODUCTION_VERIFIED: "✓",
    Verification.MECHANISM_VERIFIED: "~",
    Verification.FIXTURE_VERIFIED: "~",
    Verification.INSUFFICIENT_HISTORY: "~",
    Verification.NOT_OBSERVED: "·",
    Verification.NOT_CONFIGURED: "·",
    Verification.AUTHORITY_UNAVAILABLE: "!",
    Verification.BLOCKED: "!",
    Verification.FAILED: "!",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.production_report",
        description="What is proven, what is only tested, and what is blocked.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="also write data/production_status.json")
    args = parser.parse_args(argv)

    settings = get_settings()
    capabilities = assess(settings)
    sources = load_registry()
    history = history_metrics(settings)
    claims = claim_metrics(settings)

    if args.json:
        print(json.dumps(
            {key: asdict(cap) | {"status": cap.status.value}
             for key, cap in capabilities.items()},
            ensure_ascii=False, indent=2))
        if args.write:
            write_scorecard(settings)
        return 0

    citable = [s for s in sources if s.is_usable]
    live = [s for s in sources if mode_for(s, settings) is QueryMode.LIVE_QUERY]
    blocked = [s for s in sources if s.health is Health.BLOCKED]
    down = [s for s in sources if s.health is Health.UNAVAILABLE]
    stale = [s for s in citable if store.due(s, settings)]

    print("CLARÉ PRODUCTION STATUS")
    print()
    print("Official sources")
    print(f"  {len(sources)} registered · {len(citable)} citable · "
          f"{len(live)} live-query enabled")
    print(f"  {len(citable) - len(stale)} current · {len(stale)} due a refresh · "
          f"{len(down)} unreachable · {len(blocked)} blocked")
    print(f"  {len(departments())} départements mapped to a préfecture")
    print()

    depths = sorted(((m["version_count"], m["days_of_history"], key)
                     for key, m in history.items() if m["version_count"]),
                    reverse=True)
    print("Historical depth")
    for count, days, key in depths[:6]:
        print(f"  {key:<28} {count} version(s) · {days}d")
    if not depths:
        print("  (nothing stored yet)")
    print()

    print("Claims")
    print(f"  {claims['active']} active · {claims['stale']} pending review · "
          f"{claims['with_effective_date']} carry a start date")
    print()

    print("Observed in the real world")
    print(f"  source changes: {capabilities['change_detection'].detail.get('live', 0)}")
    print(f"  conflicts:      {live_conflicts(settings)}")
    print()

    print("Capabilities")
    for key, cap in capabilities.items():
        print(f"  {_MARK.get(cap.status, '?')} {cap.name:<30} "
              f"{cap.status.value:<22} {cap.evidence[:56]}")
    print()

    blockers = [c for c in capabilities.values()
                if c.status in (Verification.BLOCKED, Verification.FAILED,
                                Verification.AUTHORITY_UNAVAILABLE)]
    if blockers:
        print("Current blockers")
        for cap in blockers:
            print(f"  ! {cap.name}: {cap.evidence}")
        print()

    proven = sum(1 for c in capabilities.values() if c.is_production)
    print(f"{proven} of {len(capabilities)} capabilities have met real data. "
          f"The rest are tested, not proven.")

    if args.write:
        print(f"scorecard written to {write_scorecard(settings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
