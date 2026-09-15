"""Fetching a page from a registered source, and refusing everything else.

The rules this enforces are the difference between "Claré can read official
institution pages" and "Claré reads the internet":

* HTTPS only.
* The host must belong to a registered source — checked **at every redirect
  hop**, not only at the end. A registered domain that bounces to somewhere
  else is the whole attack, and a fetcher that validates only the final URL
  walks straight into it.
* A content type we can actually read, and a size we are willing to hold.
* One request per host at a time, with a floor on the interval between them.
* robots.txt is honoured.
* Nothing from the page is executed. The extractor reads markup as text.

Failures return a :class:`FetchResult` with ``ok=False`` and a reason. This
layer never raises into the answer path: a source being unreachable is an
outcome to report to the reader, not a traceback.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.config import Settings, get_settings
from app.sources.registry import Source, for_domain, hostname_allowed

USER_AGENT = "ClareBot/0.1 (+grounded question answering over official sources)"

MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
TIMEOUT_SECONDS = 20.0
MIN_INTERVAL_SECONDS = 1.0
READABLE_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "application/xml", "text/xml")

_host_lock = threading.Lock()
_last_request: dict[str, float] = {}
_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}


@dataclass
class FetchResult:
    url: str
    ok: bool = False
    status: int = 0
    final_url: str = ""
    content_type: str = ""
    body: str = ""
    bytes_read: int = 0
    retrieved_at: str = ""
    content_hash: str = ""
    source_id: str = ""
    from_cache: bool = False
    redirects: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def host(self) -> str:
        return (urlsplit(self.final_url or self.url).hostname or "").lower()


def _now() -> str:
    return datetime.now(tz=timezone.utc).isoformat(timespec="seconds")


def _cache_dir(settings: Settings) -> Path:
    path = settings.data_dir / "sources" / "cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _cache_file(settings: Settings, url: str) -> Path:
    key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:24]
    return _cache_dir(settings) / f"{key}.json"


def _read_cache(settings: Settings, url: str, max_age_seconds: int) -> FetchResult | None:
    path = _cache_file(settings, url)
    if not path.exists():
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(record["retrieved_at"])
    except (OSError, ValueError, KeyError):
        return None
    if (datetime.now(tz=timezone.utc) - stamp).total_seconds() > max_age_seconds:
        return None
    record.pop("from_cache", None)
    return FetchResult(from_cache=True, **record)


def _write_cache(settings: Settings, result: FetchResult) -> None:
    try:
        payload = dict(vars(result))
        payload.pop("from_cache", None)
        _cache_file(settings, result.url).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _throttle(host: str) -> None:
    """One request per host at a time, and never two in quick succession."""
    with _host_lock:
        last = _last_request.get(host, 0.0)
        wait = MIN_INTERVAL_SECONDS - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_request[host] = time.monotonic()


def _robots_for(client: httpx.Client, host: str) -> urllib.robotparser.RobotFileParser | None:
    if host in _robots:
        return _robots[host]
    parser = urllib.robotparser.RobotFileParser()
    try:
        reply = client.get(f"https://{host}/robots.txt", timeout=8.0)
        if reply.status_code == 200 and len(reply.content) < 500_000:
            parser.parse(reply.text.splitlines())
        else:
            parser.parse([])
    except httpx.HTTPError:
        # Unreachable robots.txt is not permission to ignore it, but it is
        # also not a reason to refuse a site that may simply not publish one.
        parser.parse([])
    _robots[host] = parser
    return parser


def allowed_by_robots(client: httpx.Client, url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower()
    parser = _robots_for(client, host)
    if parser is None:
        return True
    try:
        return parser.can_fetch(USER_AGENT, url)
    except Exception:  # noqa: BLE001 - a malformed robots file is not a refusal
        return True


def fetch(url: str, *, settings: Settings | None = None,
          max_age_seconds: int | None = None,
          expect: Source | None = None) -> FetchResult:
    """Fetch one page from a registered source.

    ``expect`` narrows the check to a single source: used when the caller
    already knows which institution it is asking about, so a redirect onto a
    *different* registered domain is still refused.
    """
    settings = settings or get_settings()
    result = FetchResult(url=url, retrieved_at=_now())

    split = urlsplit(url)
    if split.scheme != "https":
        result.error = "refused: only https is fetched"
        return result

    source = expect or for_domain(url)
    if source is None or not hostname_allowed(source, url):
        result.error = f"refused: {split.hostname!r} is not a registered source"
        return result
    result.source_id = source.id

    if max_age_seconds:
        cached = _read_cache(settings, url, max_age_seconds)
        if cached is not None:
            return cached

    headers = {"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"}
    try:
        with httpx.Client(follow_redirects=False, timeout=TIMEOUT_SECONDS,
                          headers=headers) as client:
            if not allowed_by_robots(client, url):
                result.error = "refused: robots.txt disallows this path"
                return result

            current = url
            for _ in range(MAX_REDIRECTS + 1):
                _throttle((urlsplit(current).hostname or "").lower())
                reply = client.get(current)
                result.status = reply.status_code

                if reply.is_redirect:
                    target = str(reply.next_request.url) if reply.next_request else ""
                    # Every hop is validated. A registered domain redirecting
                    # somewhere unregistered is exactly the case this exists
                    # to stop.
                    if not target.startswith("https://"):
                        result.error = "refused: redirect left https"
                        return result
                    if not hostname_allowed(source, target):
                        result.error = (
                            f"refused: redirect to {urlsplit(target).hostname!r}, "
                            f"which is not registered for {source.id}")
                        return result
                    result.redirects.append(target)
                    current = target
                    continue

                content_type = reply.headers.get("content-type", "").split(";")[0].strip()
                result.content_type = content_type
                if content_type and not any(content_type.startswith(t) for t in READABLE_TYPES):
                    result.error = f"refused: content type {content_type!r} is not readable"
                    return result

                body = reply.content[:MAX_BYTES]
                result.bytes_read = len(body)
                result.final_url = str(reply.url)
                result.body = body.decode(reply.encoding or "utf-8", errors="replace")
                result.content_hash = hashlib.sha256(body).hexdigest()
                result.ok = 200 <= reply.status_code < 300 and bool(result.body.strip())
                if not result.ok and not result.error:
                    result.error = f"http {reply.status_code}"
                break
            else:
                result.error = "refused: too many redirects"
                return result
    except httpx.HTTPError as exc:
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    if result.ok:
        _write_cache(settings, result)
    return result
