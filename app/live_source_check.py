"""Check every registered source against the live web, and change nothing.

    uv run python -m app.live_source_check
    uv run python -m app.live_source_check --source mbs
    uv run python -m app.live_source_check --json
    uv run python -m app.live_source_check --write     # record verification

This is the command that keeps the registry honest. Domains move: the school
registered here as montpellier-bs.com now answers as mbs-education.com, and
the only way that was discovered was by asking. Without `--write` it reads
only; with it, the verification result and its date are written back to the
registry so nothing claims to be verified without a date attached.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone

from app.config import get_settings
from app.sources.extract import extract
from app.sources.fetch import fetch
from app.sources.registry import REGISTRY_PATH, load_registry


def check_one(source, settings) -> dict:
    row = {
        "id": source.id, "name": source.name, "domain": source.domain,
        "requested": source.base_url, "status": 0, "canonical_url": "",
        "title": "", "words": 0, "content_hash": "", "retrieved_at": "",
        "parser": "not run", "redirects": [], "healthy": False, "error": "",
    }
    result = fetch(source.base_url, settings=settings, expect=source)
    row["status"] = result.status
    row["retrieved_at"] = result.retrieved_at
    row["redirects"] = result.redirects
    row["content_hash"] = result.content_hash[:16]
    if not result.ok:
        row["error"] = result.error or f"http {result.status}"
        return row

    page = extract(result.body, result.final_url)
    row["canonical_url"] = page.canonical_url
    row["title"] = page.title
    row["words"] = page.word_count
    row["language"] = page.language
    row["parser"] = "ok" if page.is_usable else "no usable content"
    row["healthy"] = page.is_usable
    if not page.is_usable:
        row["error"] = "page fetched but parsed to nothing readable"
    return row


def _write_verification(rows: list[dict]) -> int:
    """Record verified/verified_at in the registry, in place, for healthy sources."""
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    today = datetime.now(tz=timezone.utc).date().isoformat()
    written = 0
    for row in rows:
        if not row["healthy"]:
            continue
        marker = f"  - id: {row['id']}\n"
        start = text.find(marker)
        if start == -1:
            continue
        end = text.find("\n  - id:", start + 1)
        end = len(text) if end == -1 else end
        block = text[start:end]
        updated = block.replace("verified: false", "verified: true")
        if "verified_at:" in updated:
            import re
            updated = re.sub(r"verified_at: .*", f"verified_at: {today}", updated)
        else:
            updated = updated.replace("verified: true",
                                      f"verified: true\n    verified_at: {today}")
        if updated != block:
            text = text[:start] + updated + text[end:]
            written += 1
    if written:
        REGISTRY_PATH.write_text(text, encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.live_source_check",
                                     description="Check registered sources against the live web.")
    parser.add_argument("--source", help="check one source by id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="record verified/verified_at for healthy sources")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = load_registry()
    if args.source:
        sources = tuple(s for s in sources if s.id == args.source)
        if not sources:
            print(f"no source with id {args.source!r}", file=sys.stderr)
            return 1

    rows = [check_one(source, settings) for source in sources]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'ID':<16} {'STATUS':<7} {'WORDS':<7} {'PARSER':<20} CANONICAL")
        for row in rows:
            print(f"{row['id']:<16} {row['status'] or '-':<7} {row['words']:<7} "
                  f"{row['parser'][:20]:<20} {row['canonical_url'] or row['error'][:60]}")
            if row["redirects"]:
                print(f"{'':<16} redirected → {row['redirects'][-1]}")

    healthy = sum(1 for r in rows if r["healthy"])
    failed = [r["id"] for r in rows if not r["healthy"]]
    if not args.json:
        print()
        print(f"checked {len(rows)} · healthy {healthy} · failed {len(failed)}")
        if failed:
            print("failed: " + ", ".join(failed))

    if args.write:
        written = _write_verification(rows)
        print(f"registry updated for {written} source(s)")

    return 0 if healthy else 1


if __name__ == "__main__":
    raise SystemExit(main())
