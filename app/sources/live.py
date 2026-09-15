"""Answering from a registered source's own website.

The route is narrow on purpose:

    entity  →  registry  →  that source's domain  →  its own pages

There is no step in it where a search engine, a link found on a page, or a URL
a reader typed can introduce a domain. Discovery happens *inside* one
registered site: fetch its entry point, read the links it publishes, keep the
ones on its own domain, and rank them against the question.

Cached first, live when the cache is stale or the question is about what is
true now. Every page that comes back carries the URL, the domain, the time it
was retrieved and the hash of what was read, so a claim can be traced to a
version rather than to "the web".
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.sources import store
from app.sources.extract import Page, extract
from app.sources.fetch import fetch
from app.sources.registry import Source, for_entity
from app.sources.store import Freshness

#: Words that say "what is true now", which is when a cached copy is not good
#: enough even if it is inside its freshness window.
_NOW_WORDS = ("latest", "current", "currently", "now", "recent", "recently",
              "changed", "change", "update", "updated", "this year", "still",
              "actuel", "actuelle", "actuellement", "récent", "recent",
              "récemment", "changé", "change", "mise à jour", "maintenant",
              "cette année", "toujours")

_STOP = frozenset({
    "how", "what", "where", "when", "which", "who", "why", "do", "does", "did",
    "can", "could", "should", "is", "are", "was", "the", "a", "an", "to", "for",
    "of", "in", "on", "at", "and", "or", "my", "me", "i", "it", "this", "that",
    "get", "need", "want", "with", "from", "about", "into", "school",
    "comment", "que", "quoi", "ou", "quand", "quel", "quelle", "est", "sont",
    "je", "mon", "ma", "les", "des", "une", "pour", "dans", "avec", "sur",
    "ecole", "faire", "faut",
})


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return folded


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]{3,}", _fold(text))
    return {w for w in words if w not in _STOP}


def wants_current_information(question: str) -> bool:
    folded = _fold(question)
    return any(_fold(word) in folded for word in _NOW_WORDS)


@dataclass
class Evidence:
    """One page, and everything needed to trace a claim back to it."""

    source_id: str
    source_name: str
    domain: str
    url: str
    canonical_url: str
    title: str
    text: str
    retrieved_at: str
    content_hash: str
    freshness: Freshness
    version_id: str = ""
    updated: str = ""

    @property
    def excerpt(self) -> str:
        return " ".join(self.text.split())[:600]


@dataclass
class LiveResult:
    entity_id: str = ""
    source: Source | None = None
    evidence: list[Evidence] = field(default_factory=list)
    freshness: Freshness = Freshness.UNKNOWN
    attempted_live: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.evidence)


#: Sections that publish dated stories rather than standing procedure. A news
#: item about last year's admission round is not what the rules are now.
_EDITORIAL = ("/actualites/", "/actualite/", "/news/", "/blog/", "/evenements/",
              "/events/", "/temoignages/", "/presse/", "/agenda/")


def _score(url: str, wanted: set[str], topics: tuple[str, ...]) -> int:
    """How much a link's own path looks like the question."""
    raw_path = urlsplit(url).path
    path = _fold(raw_path.replace("-", " ").replace("/", " ").replace("_", " "))
    words = set(re.findall(r"[a-z0-9]{3,}", path))
    score = len(words & wanted) * 3
    score += sum(1 for topic in topics if _fold(topic) in path)
    # A deep, specific page beats the homepage for a specific question.
    depth = len([p for p in raw_path.split("/") if p])
    score += 1 if 1 <= depth <= 4 else 0
    if any(section in _fold(raw_path) for section in _EDITORIAL):
        score -= 4
    return score


_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def sitemap_urls(source: Source, *, settings: Settings | None = None,
                 max_children: int = 3, cap: int = 600) -> list[str]:
    """Pages the source itself publishes as its index.

    A site's own sitemap is a better map of it than whatever happens to be
    linked from the homepage — a dedicated admissions page is often reachable
    from a menu the extractor discards as navigation. Index files are followed
    one level, a few children at most; this is not a crawl.
    """
    settings = settings or get_settings()
    found: list[str] = []
    seen_sitemaps: set[str] = set()
    queue = [f"https://{urlsplit(source.base_url).hostname}/sitemap.xml"]

    while queue and len(found) < cap:
        target = queue.pop(0)
        if target in seen_sitemaps or not source.allows(target):
            continue
        seen_sitemaps.add(target)
        result = fetch(target, settings=settings, expect=source,
                       max_age_seconds=source.freshness_hours * 3600)
        if not result.ok:
            continue
        locations = [u for u in _LOC.findall(result.body) if source.allows(u)]
        if "<sitemapindex" in result.body[:2000].lower():
            queue.extend(locations[:max_children])
            continue
        found.extend(locations)
    return list(dict.fromkeys(found))[:cap]


def candidate_pages(source: Source, question: str, *, limit: int = 3,
                    settings: Settings | None = None) -> list[str]:
    """Pages on this source worth reading for this question.

    Discovery is confined to links the source itself publishes on its own
    entry point, and every candidate is re-validated against the registry
    before it is used.
    """
    settings = settings or get_settings()
    entries = list(source.entry_points) or [source.base_url]
    wanted = _keywords(question)

    found: list[str] = []
    for entry in entries[:2]:
        result = fetch(entry, settings=settings, expect=source,
                       max_age_seconds=source.freshness_hours * 3600)
        if not result.ok:
            continue
        page = extract(result.body, result.final_url)
        found.extend(link for link in page.links if source.allows(link))

    def rank(urls: list[str]) -> list[tuple[int, str]]:
        return sorted(((_score(u, wanted, source.supported_topics), u)
                       for u in dict.fromkeys(urls)), reverse=True)

    scored = rank(found)
    # A homepage rarely links to the page that answers a specific question.
    # If nothing linked from it actually matches the words asked, ask the
    # site for its own index instead.
    if not scored or scored[0][0] < 4:
        scored = rank([*found, *sitemap_urls(source, settings=settings)])

    best = [url for score, url in scored if score > 1][:limit]
    # The entry point is always worth keeping as a fallback.
    return list(dict.fromkeys([*best, entries[0]]))[:limit + 1]


def gather(entity_id: str, question: str, *, limit: int = 3,
           force_live: bool | None = None,
           settings: Settings | None = None) -> LiveResult:
    """Evidence for this question from the entity's own official source."""
    settings = settings or get_settings()
    sources = [s for s in for_entity(entity_id) if s.live_query_enabled and s.verified]
    if not sources:
        return LiveResult(entity_id=entity_id,
                          error="no verified live source is registered for this entity")

    source = sources[0]
    result = LiveResult(entity_id=entity_id, source=source)
    live_wanted = wants_current_information(question) if force_live is None else force_live

    try:
        urls = candidate_pages(source, question, limit=limit, settings=settings)
    except Exception as exc:  # noqa: BLE001 - a source outage is an outcome
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    worst = Freshness.LIVE_VERIFIED
    for url in urls:
        state = store.freshness(source.id, url, source.freshness_hours, settings)
        use_cache = state is Freshness.FRESH and not live_wanted

        if use_cache:
            version = store.active(source.id, url, settings)
            if version is not None:
                result.evidence.append(Evidence(
                    source_id=source.id, source_name=source.name,
                    domain=source.domain, url=url,
                    canonical_url=version.canonical_url, title=version.title,
                    text=version.text, retrieved_at=version.retrieved_at,
                    content_hash=version.content_hash, freshness=Freshness.FRESH,
                    version_id=version.version_id, updated=version.updated))
                worst = Freshness.FRESH if worst is Freshness.LIVE_VERIFIED else worst
                continue

        result.attempted_live = True
        fetched = fetch(url, settings=settings, expect=source)
        if not fetched.ok:
            store.audit("live.fetch_failed", settings, source=source.id,
                        url=url, error=fetched.error)
            fallback = store.active(source.id, url, settings)
            if fallback is not None:
                result.evidence.append(Evidence(
                    source_id=source.id, source_name=source.name,
                    domain=source.domain, url=url,
                    canonical_url=fallback.canonical_url, title=fallback.title,
                    text=fallback.text, retrieved_at=fallback.retrieved_at,
                    content_hash=fallback.content_hash,
                    freshness=Freshness.STALE, version_id=fallback.version_id,
                    updated=fallback.updated))
                worst = Freshness.STALE
            else:
                worst = Freshness.UNAVAILABLE
            continue

        page = extract(fetched.body, fetched.final_url)
        version, _report = store.record(source.id, url, page, fetched.content_hash,
                                        settings=settings)
        if version is None:
            worst = Freshness.UNAVAILABLE
            continue
        result.evidence.append(Evidence(
            source_id=source.id, source_name=source.name, domain=source.domain,
            url=url, canonical_url=version.canonical_url, title=version.title,
            text=version.text, retrieved_at=version.retrieved_at,
            content_hash=version.content_hash, freshness=Freshness.LIVE_VERIFIED,
            version_id=version.version_id, updated=version.updated))

    result.freshness = worst if result.evidence else Freshness.UNAVAILABLE
    if not result.evidence and not result.error:
        result.error = "the official site could not be read just now"
    return result
