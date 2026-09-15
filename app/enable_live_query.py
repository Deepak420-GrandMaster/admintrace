"""Promote a source to live querying, once it has earned it.

    uv run python -m app.enable_live_query               # show what is eligible
    uv run python -m app.enable_live_query --source mbs  # explain one
    uv run python -m app.enable_live_query --apply       # switch on the eligible

Live querying is a permission granted per source after it has passed every
check in :mod:`app.sources.gating` — verified domain, extractable content,
security, stated authority, declared topics, working freshness metadata.
Switching them all on at once would put pages we have never parsed in front of
readers and make every question pay for network calls to sites with nothing to
contribute.

Without ``--apply`` this changes nothing; it reports.
"""

from __future__ import annotations

import argparse
import re
import sys

from app.config import get_settings
from app.sources.gating import QueryMode, evaluate
from app.sources.registry import REGISTRY_PATH, load_registry


def _set_flag(source_ids: list[str], value: bool) -> int:
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    changed = 0
    for source_id in source_ids:
        marker = f"  - id: {source_id}\n"
        start = text.find(marker)
        if start == -1:
            continue
        end = text.find("\n  - id:", start + 1)
        end = len(text) if end == -1 else end
        block = text[start:end]
        updated = re.sub(r"    live_query_enabled: (?:true|false)",
                         f"    live_query_enabled: {'true' if value else 'false'}",
                         block)
        if updated != block:
            text = text[:start] + updated + text[end:]
            changed += 1
    if changed:
        REGISTRY_PATH.write_text(text, encoding="utf-8")
    return changed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.enable_live_query",
        description="Promote sources to live querying once they pass the gate.")
    parser.add_argument("--source", help="explain one source")
    parser.add_argument("--apply", action="store_true",
                        help="switch on every eligible source")
    parser.add_argument("--disable", metavar="ID",
                        help="turn live querying off for one source")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = load_registry()

    if args.disable:
        count = _set_flag([args.disable], False)
        print(f"live querying disabled for {args.disable}"
              if count else f"no source with id {args.disable!r}")
        return 0 if count else 1

    if args.source:
        sources = [s for s in sources if s.id == args.source]
        if not sources:
            print(f"no source with id {args.source!r}", file=sys.stderr)
            return 1

    eligible: list[str] = []
    print(f"{'SOURCE':<28} {'GATE':<14} {'ENABLED':<9} DETAIL")
    for source in sources:
        result = evaluate(source, settings)
        if result.mode is QueryMode.LIVE_QUERY and not source.live_query_enabled:
            eligible.append(source.id)
        detail = result.why() if result.failures else (
            "already on" if source.live_query_enabled else "eligible; not switched on")
        print(f"{source.id:<28} {result.mode.value:<14} "
              f"{str(source.live_query_enabled):<9} {detail[:60]}")

    print()
    if not eligible:
        print("nothing new is eligible.")
        return 0
    print(f"eligible and not yet on: {', '.join(eligible)}")
    if args.apply:
        print(f"switched on {_set_flag(eligible, True)} source(s)")
    else:
        print("run again with --apply to switch them on")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
