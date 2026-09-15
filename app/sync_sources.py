"""Re-read every live source, version what changed, and say what it means.

    uv run python -m app.sync_sources --dry-run    # fetch, compare, report
    uv run python -m app.sync_sources              # and store the result
    uv run python -m app.sync_sources --source mbs

A dry run touches nothing: it fetches, compares against the active version and
prints what it would do. A real run stores the new version, activates it only
if it parsed to something usable, invalidates work derived from the old
wording when the change is substantive, and writes the whole decision to the
audit log.

A fetch that fails, or comes back as an application shell with no readable
text, never replaces a good version. Losing a working answer to a site's bad
afternoon is a worse outcome than serving one that is a few days old.
"""

from __future__ import annotations

import argparse
import json
import sys

from app.config import get_settings
from app.sources import store
from app.sources.change import ChangeType
from app.sources.extract import extract
from app.sources.fetch import fetch
from app.sources.live import candidate_pages
from app.sources.registry import load_registry


def sync_source(source, settings, *, dry_run: bool, pages: int) -> list[dict]:
    rows: list[dict] = []
    targets = list(dict.fromkeys([*source.entry_points, source.base_url]))[:pages]

    for url in targets:
        row = {"source": source.id, "url": url, "status": 0,
               "change": ChangeType.NONE.value, "summary": "", "action": "",
               "version": "", "error": ""}
        result = fetch(url, settings=settings, expect=source)
        row["status"] = result.status
        if not result.ok:
            row["error"] = result.error or f"http {result.status}"
            row["action"] = "kept previous version"
            rows.append(row)
            store.audit("sync.fetch_failed", settings, source=source.id,
                        url=url, error=row["error"], dry_run=dry_run)
            continue

        page = extract(result.body, result.final_url)
        current = store.active(source.id, url, settings)

        if dry_run:
            from app.sources.change import compare
            report = compare(current.text if current else "", page.text,
                             old_title=current.title if current else "",
                             new_title=page.title)
            row["change"] = report.change_type.value
            row["summary"] = report.summary
            row["action"] = ("would refuse: no usable content" if not page.is_usable
                             else "would activate" if report.change_type is not ChangeType.NONE
                             else "no change")
            rows.append(row)
            continue

        version, report = store.record(source.id, url, page, result.content_hash,
                                       settings=settings)
        row["change"] = report.change_type.value
        row["summary"] = report.summary
        if version is None:
            row["action"] = "refused: no usable content, previous version kept"
        else:
            row["version"] = version.version_id
            row["action"] = ("activated" if report.change_type is not ChangeType.NONE
                             else "unchanged")
            if report.is_substantive:
                row["action"] += " · derived caches invalidated"
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.sync_sources",
                                     description="Re-read live sources and version what changed.")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and compare, but change nothing")
    parser.add_argument("--source", help="only this source id")
    parser.add_argument("--pages", type=int, default=2,
                        help="entry pages per source (default 2)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = [s for s in load_registry() if s.live_query_enabled and s.verified]
    if args.source:
        sources = [s for s in sources if s.id == args.source]
        if not sources:
            print(f"no verified live source with id {args.source!r}", file=sys.stderr)
            return 1
    if not sources:
        print("no source has live querying enabled and verified; nothing to sync.")
        return 0

    rows: list[dict] = []
    for source in sources:
        rows.extend(sync_source(source, settings, dry_run=args.dry_run,
                                pages=args.pages))

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        mode = "DRY RUN — nothing written" if args.dry_run else "SYNC"
        print(f"{mode}\n")
        print(f"{'SOURCE':<12} {'CHANGE':<12} {'ACTION':<44} URL")
        for row in rows:
            print(f"{row['source']:<12} {row['change']:<12} "
                  f"{(row['action'] or row['error'])[:44]:<44} {row['url'][:60]}")
        changed = [r for r in rows if r["change"] in ("substantive", "critical")]
        print(f"\n{len(rows)} page(s) · {len(changed)} substantive change(s)")
        for row in changed:
            print(f"  ! {row['source']} {row['url']}\n    {row['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
