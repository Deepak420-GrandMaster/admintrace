"""Which domains are allowed to be evidence.

The corpus this system was built on — the DILA publication of
service-public.gouv.fr — answers questions about French public administration
and nothing else. It cannot answer "what does this school require", because
that is not public administration: it is one institution's own rule, published
by that institution.

The obvious fix is to let the model search the web. That would end the one
guarantee the system has, which is that every fact in an answer came from a
document we retrieved and can show you.

So instead: a closed registry. A domain is evidence because it was written
down here as the authority for something, verified, and dated — not because
it ranked well or because a reader pasted it into the chat. Anything not in
this file cannot become a source, however official it looks.

Two things this must get right, and both are load-bearing:

* **Hostname matching, not string matching.** ``montpellier-bs.com`` is
  authoritative; ``fake-montpellier-bs.com`` and ``montpellier-bs.evil.com``
  both contain it as a substring and are not.
* **Rebrands.** Institutions rename and move. ``montpellier-bs.com`` now
  redirects to ``mbs-education.com``; if the registry only knew the old
  domain, following that redirect would look exactly like a hijack and the
  real site would be refused. Both are recorded, the canonical one is
  verified, and the date it was checked is kept.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

import yaml

REGISTRY_PATH = Path(__file__).resolve().parent / "registry.yml"


class SourceType(str, Enum):
    """What kind of body publishes this, which is what authority means here."""

    GOVERNMENT = "government"
    PUBLIC_BODY = "public_body"
    LOCAL_AUTHORITY = "local_authority"
    UNIVERSITY = "university"
    SCHOOL = "school"
    INSTITUTION = "institution"
    THIRD_PARTY = "third_party"


#: Lower is more authoritative. Level 1 is the body that decides the rule.
AUTHORITY_DECIDES = 1
AUTHORITY_EXPLAINS = 2


class RegistryError(RuntimeError):
    """The registry file is missing or cannot be read."""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    domain: str
    base_url: str
    source_type: SourceType
    authority_level: int = AUTHORITY_EXPLAINS
    #: Domains that legitimately serve this source, including ones it has
    #: moved away from. The first entry is always ``domain``.
    aliases: tuple[str, ...] = field(default_factory=tuple)
    supported_topics: tuple[str, ...] = field(default_factory=tuple)
    supported_entities: tuple[str, ...] = field(default_factory=tuple)
    language: str = "fr"
    verified: bool = False
    verified_at: str = ""
    crawl_enabled: bool = False
    live_query_enabled: bool = False
    #: Hours after which an indexed copy is treated as stale.
    freshness_hours: int = 720
    entry_points: tuple[str, ...] = field(default_factory=tuple)
    notes: str = ""

    @property
    def domains(self) -> tuple[str, ...]:
        """Every hostname that may serve this source, canonical first."""
        seen, out = set(), []
        for candidate in (self.domain, *self.aliases):
            host = (candidate or "").strip().lower().lstrip(".")
            if host and host not in seen:
                seen.add(host)
                out.append(host)
        return tuple(out)

    def allows(self, url_or_host: str) -> bool:
        return hostname_allowed(self, url_or_host)


def _host_of(url_or_host: str) -> str:
    """The hostname, whether given a URL or a bare host."""
    value = (url_or_host or "").strip()
    if "//" in value:
        value = urlsplit(value).hostname or ""
    else:
        value = urlsplit("//" + value).hostname or ""
    return value.lower().rstrip(".")


def hostname_allowed(source: Source, url_or_host: str) -> bool:
    """Whether this host is the source, or a subdomain of it.

    Suffix matching only on a dot boundary. Without the dot,
    ``fake-montpellier-bs.com`` ends with ``montpellier-bs.com`` and would be
    accepted as the school.
    """
    host = _host_of(url_or_host)
    if not host:
        return False
    # A bare address is never a registered institution, and allowing one opens
    # the fetcher onto the local network.
    try:
        ipaddress.ip_address(host)
        return False
    except ValueError:
        pass
    for domain in source.domains:
        if host == domain or host.endswith("." + domain):
            return True
    return False


def _source_from(row: dict) -> Source:
    try:
        source_type = SourceType(str(row["source_type"]).lower())
    except (KeyError, ValueError) as exc:
        raise RegistryError(
            f"source {row.get('id', '?')!r} has an unusable source_type") from exc
    return Source(
        id=str(row["id"]),
        name=str(row["name"]),
        domain=str(row["domain"]).lower(),
        base_url=str(row["base_url"]),
        source_type=source_type,
        authority_level=int(row.get("authority_level", AUTHORITY_EXPLAINS)),
        aliases=tuple(str(a).lower() for a in row.get("aliases", ())),
        supported_topics=tuple(str(t) for t in row.get("supported_topics", ())),
        supported_entities=tuple(str(e) for e in row.get("supported_entities", ())),
        language=str(row.get("language", "fr")),
        verified=bool(row.get("verified", False)),
        verified_at=str(row.get("verified_at", "")),
        crawl_enabled=bool(row.get("crawl_enabled", False)),
        live_query_enabled=bool(row.get("live_query_enabled", False)),
        freshness_hours=int(row.get("freshness_hours", 720)),
        entry_points=tuple(str(u) for u in row.get("entry_points", ())),
        notes=str(row.get("notes", "")),
    )


@lru_cache(maxsize=1)
def load_registry(path: str | None = None) -> tuple[Source, ...]:
    """Every registered source, in file order."""
    file = Path(path) if path else REGISTRY_PATH
    if not file.exists():
        raise RegistryError(f"no source registry at {file}")
    try:
        raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise RegistryError(f"the source registry is not readable YAML: {exc}") from exc

    sources = tuple(_source_from(row) for row in raw.get("sources", []))
    seen: set[str] = set()
    for source in sources:
        if source.id in seen:
            raise RegistryError(f"duplicate source id {source.id!r}")
        seen.add(source.id)
        if not source.base_url.startswith("https://"):
            raise RegistryError(f"{source.id}: base_url must be https")
    return sources


def by_id(source_id: str) -> Source | None:
    return next((s for s in load_registry() if s.id == source_id), None)


def for_entity(entity_id: str) -> tuple[Source, ...]:
    """Sources that speak for this entity, most authoritative first."""
    key = (entity_id or "").strip().lower()
    if not key:
        return ()
    hits = [s for s in load_registry()
            if key in tuple(e.lower() for e in s.supported_entities)]
    return tuple(sorted(hits, key=lambda s: s.authority_level))


def for_topic(topic: str) -> tuple[Source, ...]:
    key = (topic or "").strip().lower()
    if not key:
        return ()
    hits = [s for s in load_registry()
            if key in tuple(t.lower() for t in s.supported_topics)]
    return tuple(sorted(hits, key=lambda s: s.authority_level))


def for_domain(url_or_host: str) -> Source | None:
    """The registered source that may serve this host, if any.

    This is the gate every fetched page passes through. A page whose host
    matches nothing here is not evidence, whatever it says about itself.
    """
    for source in load_registry():
        if hostname_allowed(source, url_or_host):
            return source
    return None


def is_authoritative(url_or_host: str) -> bool:
    source = for_domain(url_or_host)
    return source is not None and source.verified
