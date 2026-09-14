"""Run the full ingestion: fetch, parse, chunk, embed.

    uv run python -m app.ingest.pipeline            # resume or build
    uv run python -m app.ingest.pipeline --refresh  # rebuild from scratch
"""

from __future__ import annotations

import argparse
import sys
import time

from app.config import get_settings
from app.ingest.chunk import chunk_documents
from app.ingest.embed import build_index
from app.ingest.fetch import ensure_all
from app.ingest.parse import ParseReport, deduplicate, parse_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the Claré index.")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download the feeds and rebuild the index")
    args = parser.parse_args(argv)

    settings = get_settings()
    started = time.time()

    print(f"Claré ingestion — feed version {settings.feed_version}", flush=True)
    print("Source: DILA / service-public.gouv.fr open data (Licence Ouverte)\n",
          flush=True)

    archives = ensure_all(settings, refresh=args.refresh)
    for archive in archives:
        print(f"  fetched  {archive.segment:5s} {archive.document_count:6,d} files  "
              f"{archive.size_bytes / 1_048_576:5.1f} MB", flush=True)

    report = ParseReport()
    for archive in archives:
        parse_directory(archive.documents, archive.segment, report)
    documents, dropped = deduplicate(report.documents)
    print(f"\n  parsed   {len(report.documents):,} documents, "
          f"{len(report.failures)} failures, {len(dropped):,} duplicates dropped",
          flush=True)
    for failure in report.failures[:10]:
        print(f"    FAILED {failure.path}: {failure.reason}", flush=True)

    chunks = chunk_documents(documents, settings)
    print(f"  chunked  {len(chunks):,} chunks from {len(documents):,} documents\n",
          flush=True)

    milestone = {"next": 0.0}

    def progress(done: int, total: int) -> None:
        share = done / total if total else 1.0
        if share >= milestone["next"] or done == total:
            milestone["next"] = share + 0.02
            print(f"  embedding {done:,}/{total:,} ({share:5.1%})", flush=True)

    stats = build_index(chunks, settings, progress=progress, refresh=args.refresh)
    print(f"\n  embedded {stats.embedded:,} new, skipped {stats.skipped:,} existing",
          flush=True)
    print(f"  collection now holds {stats.total_in_collection:,} chunks", flush=True)
    print(f"\nDone in {time.time() - started:,.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
