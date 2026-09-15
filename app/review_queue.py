"""What still needs a person.

    uv run python -m app.review_queue
    uv run python -m app.review_queue --json
    uv run python -m app.review_queue --resolve CHANGE-… --note "checked"

Four things collect here: source changes that were classified high or
critical, genuine contradictions between two official sources, claims left
pending review after their page moved, and sources that are currently failing.

Nothing on this list resolves itself. That is deliberate — a contradiction
between two official bodies is exactly the sort of thing that should require
somebody to have looked at it.
"""

from __future__ import annotations

import argparse
import json

from app.config import get_settings
from app.sources import claims as claim_store
from app.sources import incidents, review, store
from app.sources.registry import load_registry


def gather(settings) -> dict:
    changes = review.listing("changes", review.OPEN, settings)
    conflicts = review.listing("conflicts", review.OPEN, settings)

    pending: list[dict] = []
    for source in load_registry():
        current = store.active(source.id, source.base_url, settings)
        if current is None:
            continue
        for item in claim_store.load(source.id, current.version_id, settings):
            if not item.is_current:
                pending.append({"source": source.id, "claim": item.text[:90],
                                "status": item.status,
                                "version": item.version_id})
    return {
        "changes": changes,
        "conflicts": conflicts,
        "stale_claims": pending,
        "incidents": incidents.open_incidents(settings),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.review_queue",
                                     description="Everything still awaiting a human.")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--resolve", metavar="ID",
                        help="mark a CHANGE-… or CONFLICT-… reviewed")
    parser.add_argument("--note", default="", help="why")
    args = parser.parse_args(argv)

    settings = get_settings()

    if args.resolve:
        kind = "conflicts" if args.resolve.startswith("CONFLICT") else "changes"
        done = review.set_status(kind, args.resolve, review.RESOLVED,
                                 note=args.note, settings=settings)
        print(f"{args.resolve} marked resolved" if done
              else f"no {kind[:-1]} with id {args.resolve!r}")
        return 0 if done else 1

    queue = gather(settings)
    if args.json:
        print(json.dumps(queue, ensure_ascii=False, indent=2))
        return 0

    total = sum(len(v) for v in queue.values())
    print("REVIEW QUEUE\n")

    print(f"Source changes needing review ({len(queue['changes'])})")
    for row in queue["changes"][:10]:
        print(f"  [{row.get('severity', '?').upper()}] {row['change_id']}  "
              f"{row['source']}")
        print(f"      {row.get('summary', '')[:80]}")
    if not queue["changes"]:
        print("  none")

    print(f"\nConflicts between official sources ({len(queue['conflicts'])})")
    for row in queue["conflicts"][:10]:
        print(f"  [{row.get('severity', '?').upper()}] {row['conflict_id']}  "
              f"{row.get('source_a')} vs {row.get('source_b')}")
        print(f"      recommend: {row.get('recommended_resolution')} "
              f"({row.get('recommended_reason')})")
    if not queue["conflicts"]:
        print("  none")

    print(f"\nClaims pending review ({len(queue['stale_claims'])})")
    for row in queue["stale_claims"][:10]:
        print(f"  {row['source']:<26} {row['claim'][:64]}")
    if not queue["stale_claims"]:
        print("  none")

    print(f"\nSources currently failing ({len(queue['incidents'])})")
    for row in queue["incidents"]:
        print(f"  {row['source']:<26} {row['kind']:<16} "
              f"×{row['occurrence_count']}  since {row['first_seen'][:16]}")
    if not queue["incidents"]:
        print("  none")

    print(f"\n{total} item(s) awaiting review")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
