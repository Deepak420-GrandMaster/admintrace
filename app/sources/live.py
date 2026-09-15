"""Answering from a registered source's own website.

The route is narrow on purpose:

    entity  →  registry  →  that source's domain  →  its own pages

There is no step in it where a search engine, a link found on a page, or a URL
a reader typed can introduce a domain. Discovery happens *inside* one
registered site, and every candidate is re-validated against the registry
before it is fetched.

Discovery is purpose-aware rather than similarity-aware, because those are not
the same thing. A question about admission and a press release reporting last
year's admission round share every important word; what separates them is what
the page is *for*. So candidates are ranked by whether their kind serves the
reader's purpose — see :mod:`app.sources.purpose` — and dated announcements
are pushed down unless the question is about what recently changed.

Depth is bounded at two: the entry point, what it links to, and the children
of the links that already looked relevant. Nothing recurses.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.config import Settings, get_settings
from app.sources import purpose as purpose_model
from app.sources import store
from app.sources.extract import Page, extract
from app.sources.fetch import fetch
from app.sources.registry import Source, for_entity
from app.sources.store import Freshness

#: Words that say "what is true now", which is when a cached copy is not good
#: enough even if it is inside its freshness window — and the one case where a
#: dated announcement is the right document rather than the wrong one.
_NOW_WORDS = ("latest", "current", "currently", "now", "recent", "recently",
              "changed", "change", "changes", "update", "updated", "this year",
              "still", "new rule", "actuel", "actuelle", "actuellement",
              "récent", "recent", "récemment", "changé", "change", "changement",
              "mise à jour", "maintenant", "cette année", "toujours", "nouveau")

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def _fold(text: str) -> str:
    folded = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in folded if unicodedata.category(c) != "Mn")


def wants_current_information(question: str) -> bool:
    folded = _fold(question)
    return any(_fold(word) in folded for word in _NOW_WORDS)


# ------------------------------------------------------------- evidence ----

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
    page_type: str = purpose_model.UNKNOWN_TYPE
    #: The area this source speaks for, so a Montpellier answer can be caught
    #: citing the Rhône préfecture before a reader acts on it.
    jurisdiction_area: str = ""

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
    purposes: tuple[str, ...] = field(default_factory=tuple)
    #: Every source that contributed, in the order the plan asked them.
    sources: list[Source] = field(default_factory=list)
    #: Authorities that should have answered and could not be reached. Named,
    #: because "the préfecture is down" and "there is no answer" are different
    #: things to tell a reader.
    unreachable: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.evidence)

    @property
    def local_source(self) -> Source | None:
        from app.sources.registry import JurisdictionLevel
        return next((s for s in self.sources
                     if s.jurisdiction is not JurisdictionLevel.NATIONAL), None)

    @property
    def source_versions(self) -> dict[str, str]:
        """What this answer rests on, for deciding later whether it still holds."""
        return {f"{e.source_id}|{e.url}": e.version_id
                for e in self.evidence if e.version_id}


# ------------------------------------------------------------ discovery ----

@dataclass
class Candidate:
    """A page worth considering, and why."""

    url: str
    anchor: str = ""
    title: str = ""
    page_type: str = purpose_model.UNKNOWN_TYPE
    depth: int = 1
    score: float = 0.0


def score_candidate(url: str, *, anchor: str = "", title: str = "",
                    found: tuple, source: Source, depth: int = 1,
                    wants_news: bool = False) -> tuple[float, str]:
    """How well this page is likely to answer, and what kind of page it is."""
    path = urlsplit(url).path
    page_type = purpose_model.classify(url, title=title, anchor=anchor)

    score = 0.0
    # The site's own label for a link is its clearest statement of intent.
    score += purpose_model.keyword_hits(anchor, found) * 4.0
    score += purpose_model.keyword_hits(path, found) * 3.0
    score += purpose_model.keyword_hits(title, found) * 2.0

    if purpose_model.serves(page_type, found):
        score += 6.0
    elif page_type in ("official_procedure", "requirements"):
        score += 2.0

    if purpose_model.is_editorial(page_type):
        score += 5.0 if wants_news else -8.0
    if page_type == "application_portal":
        # Where you do it, not what it requires. Useful, rarely the answer.
        score -= 1.0

    segments = len([p for p in path.split("/") if p])
    if 1 <= segments <= 4:
        score += 1.0
    elif segments > 6:
        score -= 1.0
    if source.language and f"/{source.language}/" in path:
        score += 0.5

    score += {0: 0.0, 1: 0.0, 2: -0.5}.get(depth, -2.0)
    return score, page_type


def sitemap_urls(source: Source, *, settings: Settings | None = None,
                 max_children: int = 6, cap: int = 1200) -> list[str]:
    """Pages the source itself publishes as its index.

    A site's own sitemap is a better map of it than whatever happens to be
    linked from the homepage — a dedicated admissions page is often reachable
    only from a menu the extractor discards as navigation. Index files are
    followed one level, a few children at most; this is not a crawl.
    """
    settings = settings or get_settings()
    found: list[str] = []
    seen: set[str] = set()
    queue = [f"https://{urlsplit(source.base_url).hostname}/sitemap.xml"]

    while queue and len(found) < cap:
        target = queue.pop(0)
        if target in seen or not source.allows(target):
            continue
        seen.add(target)
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


def _harvest(url: str, source: Source, settings: Settings,
             depth: int) -> tuple[Page | None, list[Candidate]]:
    """Read one page and offer up the links it publishes."""
    result = fetch(url, settings=settings, expect=source,
                   max_age_seconds=source.freshness_hours * 3600)
    if not result.ok:
        return None, []
    page = extract(result.body, result.final_url)
    out = []
    for link in page.links:
        if source.allows(link):
            out.append(Candidate(url=link, anchor=page.anchors.get(link, ""),
                                 depth=depth))
    return page, out


def discover(source: Source, question: str, *, limit: int = 3,
             settings: Settings | None = None) -> list[Candidate]:
    """The pages on this source most likely to answer this question.

    Depth 0 is the entry point, depth 1 the links it publishes, depth 2 the
    children of the depth-1 links that already scored well. Relevance is
    judged before anything deeper is fetched, so a site is never walked
    speculatively.
    """
    settings = settings or get_settings()
    found = purpose_model.detect(question)
    wants_news = wants_current_information(question)
    entries = list(source.entry_points) or [source.base_url]

    pool: dict[str, Candidate] = {}

    def offer(candidates: list[Candidate]) -> None:
        for candidate in candidates:
            existing = pool.get(candidate.url)
            if existing is None or candidate.depth < existing.depth:
                pool[candidate.url] = candidate

    # Depth 0 and 1.
    for entry in entries[:2]:
        page, links = _harvest(entry, source, settings, depth=1)
        if page is not None:
            offer([Candidate(url=entry, title=page.title, depth=0)])
        offer(links)

    # The site's own index, which reaches pages no menu links to.
    offer([Candidate(url=u, depth=1) for u in sitemap_urls(source, settings=settings)])

    def rescore() -> list[Candidate]:
        for candidate in pool.values():
            candidate.score, candidate.page_type = score_candidate(
                candidate.url, anchor=candidate.anchor, title=candidate.title,
                found=found, source=source, depth=candidate.depth,
                wants_news=wants_news)
        return sorted(pool.values(), key=lambda c: -c.score)

    ranked = rescore()

    # Depth 2, only from links that already look right. A strong section page
    # ("Admissions") usually lists the page that actually answers.
    for parent in [c for c in ranked[:2] if c.score >= 6.0 and c.depth <= 1]:
        _page, links = _harvest(parent.url, source, settings, depth=2)
        offer(links)
    ranked = rescore()

    best = [c for c in ranked if c.score > 0][:limit]
    if not best:
        best = [Candidate(url=entries[0], depth=0)]
    return best


def candidate_pages(source: Source, question: str, *, limit: int = 3,
                    settings: Settings | None = None) -> list[str]:
    """Discovery, as a plain list of URLs."""
    return [c.url for c in discover(source, question, limit=limit, settings=settings)]


# ------------------------------------------------------------ gathering ----

def _evidence_from_version(version, source: Source, url: str,
                           freshness: Freshness, page_type: str) -> Evidence:
    return Evidence(
        source_id=source.id, source_name=source.name, domain=source.domain,
        url=url, canonical_url=version.canonical_url, title=version.title,
        text=version.text, retrieved_at=version.retrieved_at,
        content_hash=version.content_hash, freshness=freshness,
        version_id=version.version_id, updated=version.updated,
        page_type=page_type, jurisdiction_area=source.jurisdiction_area)


def gather(entity_id: str, question: str, *, limit: int = 3,
           force_live: bool | None = None,
           source_override: Source | None = None,
           settings: Settings | None = None) -> LiveResult:
    """Evidence for this question from one source's own official pages."""
    settings = settings or get_settings()
    if source_override is not None:
        source = source_override
    else:
        sources = [s for s in for_entity(entity_id)
                   if s.live_query_enabled and s.verified]
        if not sources:
            return LiveResult(
                entity_id=entity_id,
                error="no verified live source is registered for this entity")
        source = sources[0]
    found = purpose_model.detect(question)
    result = LiveResult(entity_id=entity_id, source=source,
                        sources=[source],
                        purposes=tuple(p.id for p in found))
    live_wanted = wants_current_information(question) if force_live is None else force_live

    try:
        candidates = discover(source, question, limit=limit, settings=settings)
    except Exception as exc:  # noqa: BLE001 - a source outage is an outcome
        result.error = f"{type(exc).__name__}: {exc}"
        return result

    worst = Freshness.LIVE_VERIFIED
    for candidate in candidates:
        url = candidate.url
        state = store.freshness(source.id, url, source.freshness_hours, settings)

        if state is Freshness.FRESH and not live_wanted:
            version = store.active(source.id, url, settings)
            if version is not None:
                result.evidence.append(_evidence_from_version(
                    version, source, url, Freshness.FRESH, candidate.page_type))
                if worst is Freshness.LIVE_VERIFIED:
                    worst = Freshness.FRESH
                continue

        result.attempted_live = True
        fetched = fetch(url, settings=settings, expect=source)
        if not fetched.ok:
            store.audit("live.fetch_failed", settings, source=source.id,
                        url=url, error=fetched.error)
            fallback = store.active(source.id, url, settings)
            if fallback is not None:
                result.evidence.append(_evidence_from_version(
                    fallback, source, url, Freshness.STALE, candidate.page_type))
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
        result.evidence.append(_evidence_from_version(
            version, source, url, Freshness.LIVE_VERIFIED, candidate.page_type))

    result.freshness = worst if result.evidence else Freshness.UNAVAILABLE
    if not result.evidence and not result.error:
        result.error = "the official site could not be read just now"
    return result


def gather_plan(routing_plan, question: str, *, per_source: int = 2,
                settings: Settings | None = None) -> LiveResult:
    """Walk a route, asking each authority in turn.

    The order is the plan's, and the plan's order is the point: a local
    authority is asked before the national one for a locally administered
    procedure, and an institution before either for its own rules. Evidence
    keeps that order, so the answer is written from the most specific source
    first rather than from whichever page happened to be longest.

    A source that cannot be reached is named rather than silently dropped.
    """
    settings = settings or get_settings()
    found = purpose_model.detect(question)
    result = LiveResult(purposes=tuple(p.id for p in found))
    result.unreachable.extend(routing_plan.unreachable)

    steps = routing_plan.live_steps
    if not steps:
        result.error = ("no authority for this question can be queried live "
                        "just now")
        result.freshness = Freshness.UNAVAILABLE
        return result

    worst = Freshness.LIVE_VERIFIED
    for step in steps:
        source = step.source
        single = gather(_entity_for(source), question, limit=per_source,
                        settings=settings, source_override=source)
        if not single.ok:
            result.unreachable.append(source.name)
            continue
        result.sources.append(source)
        result.evidence.extend(single.evidence)
        if single.freshness is Freshness.STALE:
            worst = Freshness.STALE
        elif single.freshness is Freshness.FRESH and worst is Freshness.LIVE_VERIFIED:
            worst = Freshness.FRESH

    result.source = result.sources[0] if result.sources else None
    result.freshness = worst if result.evidence else Freshness.UNAVAILABLE
    if not result.evidence and not result.error:
        result.error = "none of the authorities for this question could be read"
    return result


def _entity_for(source: Source) -> str:
    return source.supported_entities[0] if source.supported_entities else source.id
