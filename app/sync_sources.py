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
from app.feedback import mail
from app.sources import store
from app.sources.change import ChangeType
from app.sources.extract import extract
from app.sources.fetch import fetch
from app.sources.registry import Health, load_registry


def _read(url, source, settings):
    """Static HTML, then a render only if there was nothing readable in it."""
    result = fetch(url, settings=settings, expect=source)
    page = extract(result.body, result.final_url) if result.ok else None
    if source.render_enabled and (page is None or not page.is_usable):
        from app.sources.render import available, render
        if available():
            rendered = render(url, source=source, settings=settings)
            if rendered.ok:
                candidate = extract(rendered.body, rendered.final_url)
                if candidate.is_usable:
                    return rendered, candidate, True
    return result, page, False


def sync_source(source, settings, *, dry_run: bool, pages: int) -> list[dict]:
    rows: list[dict] = []
    targets = list(dict.fromkeys([*source.entry_points, source.base_url]))[:pages]

    for url in targets:
        row = {"source": source.id, "url": url, "status": 0,
               "change": ChangeType.NONE.value, "severity": "low",
               "categories": [], "topics": [], "summary": "", "action": "",
               "version": "", "rendered": False, "error": ""}
        result, page, rendered = _read(url, source, settings)
        row["status"] = result.status
        row["rendered"] = rendered
        if page is None:
            row["error"] = result.error or f"http {result.status}"
            row["action"] = "kept previous version"
            rows.append(row)
            store.audit("sync.fetch_failed", settings, source=source.id,
                        url=url, error=row["error"], dry_run=dry_run)
            continue

        current = store.active(source.id, url, settings)

        if dry_run:
            from app.sources.change import compare
            report = compare(current.text if current else "", page.text,
                             old_title=current.title if current else "",
                             new_title=page.title)
            row["change"] = report.change_type.value
            row["severity"] = report.severity.value
            row["categories"] = report.categories[:4]
            row["topics"] = report.affected_topics
            row["summary"] = report.summary
            row["action"] = ("would refuse: no usable content" if not page.is_usable
                             else "would activate" if report.change_type is not ChangeType.NONE
                             else "no change")
            rows.append(row)
            continue

        version, report = store.record(source.id, url, page, result.content_hash,
                                       settings=settings)
        row["before_after"] = report.before_after()
        row["change"] = report.change_type.value
        row["severity"] = report.severity.value
        row["categories"] = report.categories[:4]
        row["topics"] = report.affected_topics
        row["summary"] = report.summary
        if version is None:
            row["action"] = "refused: no usable content, previous version kept"
        else:
            row["version"] = version.version_id
            row["action"] = ("activated" if report.change_type is not ChangeType.NONE
                             else "unchanged")
            if report.is_substantive:
                row["action"] += " · derived caches invalidated"
            if report.needs_attention:
                # Email is a notification on top of the record, never the
                # record itself: a sync must not fail because a mail server
                # is down, and must not pretend it told anyone if it did not.
                sent, reason = mail.send_source_change(
                    source_name=source.name, url=url,
                    severity=report.severity.value, summary=report.summary,
                    before_after=report.before_after(),
                    categories=report.categories[:5],
                    topics=report.affected_topics,
                    version=version.version_id, settings=settings)
                row["notified"] = sent
                row["notification_error"] = "" if sent else reason
                store.audit("source.change_notified" if sent
                            else "source.notification_failed",
                            settings, source=source.id, url=url,
                            severity=report.severity.value, reason=reason)
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
    parser.add_argument("--due", action="store_true",
                        help="only sources past their own refresh interval")
    parser.add_argument("--all", action="store_true",
                        help="every usable source, not only live-query ones")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = [s for s in load_registry()
               if s.is_usable and (args.all or s.live_query_enabled)]
    if args.source:
        sources = [s for s in load_registry() if s.id == args.source]
        if not sources:
            print(f"no source with id {args.source!r}", file=sys.stderr)
            return 1
    if args.due:
        sources = [s for s in sources if store.due(s, settings)]
    if not sources:
        print("nothing due." if args.due else
              "no usable source is enabled for sync.")
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
        print(f"{'SOURCE':<12} {'CHANGE':<12} {'SEV':<9} {'ACTION':<40} URL")
        for row in rows:
            print(f"{row['source']:<12} {row['change']:<12} {row['severity']:<9} "
                  f"{(row['action'] or row['error'])[:40]:<40} {row['url'][:46]}")
        changed = [r for r in rows if r["change"] in ("substantive", "critical")]
        attention = [r for r in changed if r["severity"] in ("critical", "high")]
        print(f"\n{len(rows)} page(s) · {len(changed)} substantive change(s) · "
              f"{len(attention)} needing review")
        for row in attention:
            print(f"  ! [{row['severity'].upper()}] {row['source']} {row['url']}")
            print(f"    {row['summary']}")
            for line in (row.get("before_after") or "").splitlines():
                print(f"    {line}")
            if row["topics"]:
                print(f"    invalidates: {', '.join(row['topics'])}")
            if row.get("notification_error"):
                print(f"    not emailed: {row['notification_error']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
