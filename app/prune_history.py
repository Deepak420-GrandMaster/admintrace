"""Keep the archive useful without letting it grow forever.

    uv run python -m app.prune_history --dry-run
    uv run python -m app.prune_history

Retention is per the configured policy — ``RETENTION_DAYS`` and
``RETENTION_VERSIONS`` — and whichever keeps *more* wins, because history is
the thing that makes a question about the past answerable and it is cheap.

Three versions are never removed whatever the policy says: the one currently
answering, the one before it (which is what a rollback returns to), and any
version whose claims are still marked as pending review. Deleting the evidence
under an open review is how a queue becomes a list of unanswerable questions.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.sources import claims as claim_store
from app.sources import store
from app.sources.registry import load_registry


def plan_for(source, settings) -> list[dict]:
    """Which versions of this source's pages fall outside retention."""
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=settings.retention_days)
    rows = []

    index_keys = {source.base_url, *source.entry_points}
    for url in index_keys:
        versions = store.history(source.id, url, settings)
        if len(versions) <= 2:
            continue
        active = store.active(source.id, url, settings)
        active_id = active.version_id if active else ""
        # The active version and the one before it always stay: the second is
        # what rollback returns to.
        protected = {active_id, versions[-1].version_id, versions[-2].version_id}

        keep_recent = {v.version_id for v in versions[-settings.retention_versions:]}
        for version in versions:
            if version.version_id in protected or version.version_id in keep_recent:
                continue
            try:
                when = datetime.fromisoformat(version.retrieved_at)
            except ValueError:
                continue
            if when >= cutoff:
                continue
            pending = [c for c in claim_store.load(source.id, version.version_id,
                                                   settings)
                       if not c.is_current]
            if pending:
                rows.append({"source": source.id, "url": url,
                             "version": version.version_id,
                             "retrieved_at": version.retrieved_at,
                             "action": "kept", "reason":
                             f"{len(pending)} claim(s) pending review"})
                continue
            rows.append({"source": source.id, "url": url,
                         "version": version.version_id,
                         "retrieved_at": version.retrieved_at,
                         "action": "prune", "reason": "outside retention"})
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.prune_history",
        description="Remove source versions outside the retention policy.")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be removed, change nothing")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    settings = get_settings()
    rows: list[dict] = []
    for source in load_registry():
        rows.extend(plan_for(source, settings))

    removable = [r for r in rows if r["action"] == "prune"]

    if not args.dry_run:
        for row in removable:
            directory = (store.root(settings) / "versions" / row["source"])
            for path in directory.rglob(f"{row['version']}.json"):
                try:
                    path.unlink()
                except OSError:
                    continue
            claims_path = (settings.data_dir / "sources" / "claims"
                           / row["source"] / f"{row['version']}.json")
            try:
                claims_path.unlink(missing_ok=True)
            except OSError:
                pass
            store.audit("history.pruned", settings, source=row["source"],
                        url=row["url"], version=row["version"])

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0

    mode = "DRY RUN — nothing removed" if args.dry_run else "PRUNE"
    print(f"{mode}\n")
    print(f"retention: {settings.retention_days} days / "
          f"{settings.retention_versions} versions (whichever keeps more)")
    print()
    if not rows:
        print("Nothing is outside retention. The archive is younger than the "
              "policy, which is the expected state early on.")
        return 0
    for row in rows:
        print(f"  {row['action']:<6} {row['source']:<26} "
              f"{row['version'][:24]:<26} {row['reason']}")
    print()
    print(f"{len(removable)} version(s) "
          f"{'would be' if args.dry_run else 'were'} removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
