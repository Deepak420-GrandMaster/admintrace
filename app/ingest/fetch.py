"""Download and cache the DILA open-data feeds.

Archives are keyed by feed version and download date, so re-running ingestion
does not re-download what is already on disk. Every download is recorded in a
manifest with its hash and file count, which is what lets a later answer say
honestly which snapshot of the corpus it came from.

The feed URL is never written here. It comes from configuration, because DILA
retires feed versions and we do not want that to be a code change.
"""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
import zipfile
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings

# DILA serves public open data; a descriptive agent is simple courtesy, and
# some government hosts reject the default urllib one outright.
USER_AGENT = "reperes/0.1 (local research tool; DILA open data)"

MANIFEST_NAME = "manifest.json"


class FetchError(RuntimeError):
    """A feed could not be downloaded or is not a readable archive."""


@dataclass(frozen=True)
class FeedArchive:
    """One downloaded segment of the feed, already extracted."""

    segment: str
    feed_version: str
    url: str
    archive_path: str
    extracted_dir: str
    sha256: str
    size_bytes: int
    downloaded_at: str
    document_count: int

    @property
    def documents(self) -> Path:
        return Path(self.extracted_dir)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            if response.status != 200:
                raise FetchError(f"{url} returned HTTP {response.status}")
            with partial.open("wb") as handle:
                while chunk := response.read(1 << 20):
                    handle.write(chunk)
    except urllib.error.URLError as exc:
        partial.unlink(missing_ok=True)
        raise FetchError(f"Could not download {url}: {exc}") from exc
    # Rename only once the download finished, so an interrupted run never
    # leaves behind a truncated archive that looks like a valid cache hit.
    partial.replace(target)


def _extract(archive: Path, destination: Path) -> int:
    if destination.exists():
        existing = sum(1 for _ in destination.glob("*.xml"))
        if existing:
            return existing
    destination.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(archive) as bundle:
            members = [m for m in bundle.namelist() if m.lower().endswith(".xml")]
            for member in members:
                # Flatten, and never let an archive path escape the target dir.
                name = Path(member).name
                if not name:
                    continue
                with bundle.open(member) as source:
                    (destination / name).write_bytes(source.read())
    except zipfile.BadZipFile as exc:
        raise FetchError(f"{archive} is not a readable zip archive") from exc
    return sum(1 for _ in destination.glob("*.xml"))


def ensure_segment(
    segment: str,
    settings: Settings | None = None,
    refresh: bool = False,
) -> FeedArchive:
    """Return the local copy of one feed segment, downloading it if needed."""
    settings = settings or get_settings()
    settings.ensure_dirs()

    url = settings.feed_url(segment)
    version_dir = settings.raw_dir / settings.feed_version
    today = date.today().isoformat()
    archive = version_dir / f"{segment}-{today}.zip"

    if not refresh and not archive.exists():
        # Any earlier download of this same feed version is still valid; the
        # point of the cache is not to re-download 23 MB to get the same bytes.
        previous = sorted(version_dir.glob(f"{segment}-*.zip"))
        if previous:
            archive = previous[-1]

    if refresh or not archive.exists():
        _download(url, archive)

    extracted = settings.extracted_dir / settings.feed_version / segment
    count = _extract(archive, extracted)
    if count == 0:
        raise FetchError(f"No XML documents found in {archive}")

    return FeedArchive(
        segment=segment,
        feed_version=settings.feed_version,
        url=url,
        archive_path=str(archive),
        extracted_dir=str(extracted),
        sha256=_sha256(archive),
        size_bytes=archive.stat().st_size,
        downloaded_at=datetime.fromtimestamp(
            archive.stat().st_mtime, tz=timezone.utc
        ).isoformat(timespec="seconds"),
        document_count=count,
    )


def ensure_all(
    settings: Settings | None = None, refresh: bool = False
) -> list[FeedArchive]:
    """Download and extract every configured segment, then write the manifest."""
    settings = settings or get_settings()
    archives = [ensure_segment(s, settings, refresh) for s in settings.feed_segments]
    write_manifest(archives, settings)
    return archives


def write_manifest(archives: list[FeedArchive], settings: Settings | None = None) -> Path:
    settings = settings or get_settings()
    path = settings.raw_dir / MANIFEST_NAME
    payload = {
        "feed_version": settings.feed_version,
        "written_at": datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        "source": "DILA / service-public.gouv.fr open data (Licence Ouverte)",
        "segments": [asdict(a) for a in archives],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def read_manifest(settings: Settings | None = None) -> dict | None:
    settings = settings or get_settings()
    path = settings.raw_dir / MANIFEST_NAME
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # pragma: no cover - operational entry point
    for archive in ensure_all():
        print(
            f"{archive.segment:5s}  v{archive.feed_version}  "
            f"{archive.document_count:6,d} documents  "
            f"{archive.size_bytes / 1_048_576:6.1f} MB  "
            f"sha256={archive.sha256[:16]}…"
        )
