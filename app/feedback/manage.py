"""The bug queue, from a terminal.

    uv run python -m app.feedback.manage list
    uv run python -m app.feedback.manage list --status open
    uv run python -m app.feedback.manage show BUG-20260914-001
    uv run python -m app.feedback.manage status BUG-20260914-001 in_progress
    uv run python -m app.feedback.manage status BUG-20260914-001 resolved --note "fixed in …"
    uv run python -m app.feedback.manage archive-resolved

Deliberately a CLI and not a page in the app. The queue is a maintainer's
concern, and putting it behind a tab would mean a stranger's bug report was
one click away from anyone who opened the site.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.feedback import store


def _row(record: dict) -> str:
    return (f"{record.get('id', '?'):<20} "
            f"{record.get('status', '?'):<12} "
            f"{(record.get('severity') or '-'):<10} "
            f"{(record.get('category') or '-'):<14} "
            f"{' '.join((record.get('ai_summary') or record.get('user_report') or '').split())[:58]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.feedback.manage",
                                     description="Inspect and move bug reports.")
    sub = parser.add_subparsers(dest="command", required=True)

    listing_cmd = sub.add_parser("list", help="show the queue")
    listing_cmd.add_argument("--status", choices=store.STATUSES)
    listing_cmd.add_argument("--json", action="store_true")

    show = sub.add_parser("show", help="print one report as JSON")
    show.add_argument("bug_id")

    status = sub.add_parser("status", help="move a report to another status")
    status.add_argument("bug_id")
    status.add_argument("status", choices=store.STATUSES)
    status.add_argument("--note", default=None, help="resolution note")

    sub.add_parser("archive-resolved", help="move every resolved report to archived")

    args = parser.parse_args(argv)

    if args.command == "list":
        rows = store.listing(args.status)
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        elif not rows:
            print("No reports." if not args.status else f"No {args.status} reports.")
        else:
            print(f"{'ID':<20} {'STATUS':<12} {'SEVERITY':<10} {'CATEGORY':<14} SUMMARY")
            for record in rows:
                print(_row(record))
        return 0

    if args.command == "show":
        record = store.read(args.bug_id)
        if record is None:
            print(f"No such bug: {args.bug_id}", file=sys.stderr)
            return 1
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        try:
            path = store.set_status(args.bug_id, args.status, resolution=args.note)
        except FileNotFoundError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"{args.bug_id} → {args.status}  ({path})")
        return 0

    moved = store.archive_resolved()
    print(f"Archived {len(moved)}: {', '.join(moved)}" if moved
          else "Nothing resolved to archive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
