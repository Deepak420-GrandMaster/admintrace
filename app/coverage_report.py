"""Where AdminTrace is actually strong, and where it is still guessing.

    uv run python -m app.coverage_report
    uv run python -m app.coverage_report --json

A count of registered sources says nothing useful. What matters is whether the
bodies that *decide* a given topic can be read — and, for topics administered
locally, whether the local authority for a given place can be read too.

This prints that, per topic, so a gap is visible before a reader finds it.
"""

from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.sources.gating import QueryMode, mode_for
from app.sources.jurisdiction import departments
from app.sources.registry import JurisdictionLevel, load_registry
from app.sources.route import topics


def _sources_for(topic, settings):
    """Sources that declare themselves relevant to this topic."""
    wanted = {p.replace("_", "-") for p in topic.purposes}
    out = []
    for source in load_registry():
        topics_here = {t.lower() for t in source.supported_topics}
        if topics_here & wanted or any(
                w in t for w in wanted for t in topics_here):
            out.append(source)
    return out


def assess(topic, settings) -> dict:
    sources = _sources_for(topic, settings)
    by_mode = {s.id: mode_for(s, settings) for s in sources}

    national = [s for s in sources
                if s.jurisdiction is JurisdictionLevel.NATIONAL]
    local = [s for s in sources
             if s.jurisdiction is JurisdictionLevel.DEPARTMENT]
    institution = [s for s in sources
                   if s.jurisdiction is JurisdictionLevel.INSTITUTION]

    live = [s.id for s in sources if by_mode[s.id] is QueryMode.LIVE_QUERY]
    cached = [s.id for s in sources if by_mode[s.id] is QueryMode.CACHED_ONLY]
    down = [s.id for s in sources if by_mode[s.id] is QueryMode.UNAVAILABLE]

    needs_local = "local_authority" in topic.preferred

    # A body that *decides* this topic and cannot be read is a gap, however
    # many other sources are up. Counting the topic complete because something
    # else answered is how a reader gets a plausible answer from the wrong
    # authority — CAF decides housing benefit, and CAF is behind a bot wall.
    deciders_down = [s.id for s in sources
                     if s.authority_level == 1
                     and by_mode[s.id] is QueryMode.UNAVAILABLE]

    local_ok = not needs_local or any(
        by_mode[s.id] is QueryMode.LIVE_QUERY for s in local)
    complete = bool(live) and local_ok and not deciders_down
    partial = bool(live or cached) and not complete

    return {
        "topic": topic.id,
        "national": [s.id for s in national],
        "local": [s.id for s in local],
        "institution": [s.id for s in institution],
        "live_capable": live,
        "cached_only": cached,
        "unavailable": down,
        "needs_local_authority": needs_local,
        "deciders_unreadable": deciders_down,
        "status": "complete" if complete else ("partial" if partial else "missing"),
        "note": topic.note,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.coverage_report",
                                     description="Source coverage, per administrative topic.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    rows = [assess(t, settings) for t in topics()]

    if args.json:
        print(json.dumps({"topics": rows,
                          "departments": [d.name for d in departments()]},
                         ensure_ascii=False, indent=2))
        return 0

    print(f"{'TOPIC':<20} {'STATUS':<10} {'LIVE':<4} {'NAT':<4} {'LOC':<4} "
          f"{'INST':<5} READABLE NOW")
    for row in rows:
        print(f"{row['topic']:<20} {row['status']:<10} "
              f"{len(row['live_capable']):<4} {len(row['national']):<4} "
              f"{len(row['local']):<4} {len(row['institution']):<5} "
              f"{', '.join(row['live_capable'])[:44] or '—'}")
        if row["deciders_unreadable"]:
            print(f"{'':<20} ⚠ decides this topic and cannot be read: "
                  f"{', '.join(row['deciders_unreadable'])}")
        elif row["unavailable"]:
            print(f"{'':<20} unavailable: {', '.join(row['unavailable'])}")

    complete = [r["topic"] for r in rows if r["status"] == "complete"]
    partial = [r["topic"] for r in rows if r["status"] == "partial"]
    missing = [r["topic"] for r in rows if r["status"] == "missing"]
    print()
    print(f"complete {len(complete)} · partial {len(partial)} · missing {len(missing)}")
    if partial:
        print("partial: " + ", ".join(partial))
    if missing:
        print("missing: " + ", ".join(missing))
    print()
    print(f"local authorities registered: {len(departments())} "
          f"({', '.join(d.name for d in departments())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
