"""Choosing which authority to ask, before asking anything.

This is where the registry stops being infrastructure and starts being an
answer. Given a question, it decides which bodies could possibly speak to it,
in what order, and says so — including when the right authority exists but
cannot be reached, which is a different situation from having no answer.

The ordering is per topic because "who decides this" genuinely differs:

* an institution's admission rules are the institution's, and no government
  page can answer them;
* a residence permit is national law applied at a préfecture counter — the
  ministry states the rule, the préfecture states what its counter wants, and
  neither alone is the whole answer;
* housing benefit is one national body's to decide.

The fallback to the general corpus comes last, and only after the
authoritative route has actually been tried. Falling back early is how a
question about one school ends up answered with a generic government page.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.config import Settings, get_settings
from app.sources import purpose as purpose_model
from app.sources.gating import QueryMode, mode_for
from app.sources.jurisdiction import Place
from app.sources.registry import Source, SourceType, for_entity, load_registry

COVERAGE_PATH = Path(__file__).resolve().parent / "coverage.yml"


@dataclass(frozen=True)
class Topic:
    id: str
    purposes: tuple[str, ...] = field(default_factory=tuple)
    preferred: tuple[str, ...] = field(default_factory=tuple)
    required: tuple[str, ...] = field(default_factory=tuple)
    fallback: tuple[str, ...] = field(default_factory=tuple)
    note: str = ""


@lru_cache(maxsize=1)
def topics() -> tuple[Topic, ...]:
    raw = yaml.safe_load(COVERAGE_PATH.read_text(encoding="utf-8")) or {}
    return tuple(
        Topic(id=key,
              purposes=tuple((row or {}).get("purposes", ())),
              preferred=tuple((row or {}).get("preferred", ())),
              required=tuple((row or {}).get("required", ())),
              fallback=tuple((row or {}).get("fallback", ())),
              note=str((row or {}).get("note", "")))
        for key, row in (raw.get("topics") or {}).items()
    )


def topic_by_id(topic_id: str) -> Topic | None:
    return next((t for t in topics() if t.id == topic_id), None)


def topics_for_question(question: str) -> tuple[Topic, ...]:
    """Which administrative topics this question falls under, strongest first."""
    found = [p.id for p in purpose_model.detect(question)]
    if not found:
        return ()
    scored = []
    for topic in topics():
        hits = sum(len(found) - found.index(p) for p in topic.purposes if p in found)
        if hits:
            scored.append((hits, topic))
    scored.sort(key=lambda pair: -pair[0])
    return tuple(topic for _hits, topic in scored)


@dataclass
class Step:
    """One authority worth asking, and why it is at this position."""

    source: Source
    reason: str
    mode: QueryMode
    is_local: bool = False
    is_institution: bool = False


@dataclass
class Plan:
    question: str = ""
    topic: Topic | None = None
    place: Place | None = None
    steps: list[Step] = field(default_factory=list)
    #: An authority that should have answered but cannot be reached.
    unreachable: list[str] = field(default_factory=list)
    #: Of those, the ones that actually *decide* this topic. CAF decides
    #: housing benefit; CROUS answering instead is useful and is not the same
    #: thing, and a reader must be told which they are getting.
    unreachable_deciders: list[str] = field(default_factory=list)
    #: True when the topic needs a place and the reader has not given one.
    needs_place: bool = False
    fall_back_to_corpus: bool = True

    @property
    def live_steps(self) -> list[Step]:
        return [s for s in self.steps if s.mode is QueryMode.LIVE_QUERY]

    @property
    def has_authority(self) -> bool:
        return bool(self.steps)


def _matches_class(source: Source, wanted: str) -> bool:
    if wanted == "corpus":
        return source.id == "service-public"
    try:
        return source.source_type is SourceType(wanted)
    except ValueError:
        return False


def plan(question: str, *, entity_id: str = "", place: Place | None = None,
         settings: Settings | None = None) -> Plan:
    """Who to ask about this question, in order.

    Nothing is fetched here. This decides the route; :mod:`app.sources.live`
    walks it.
    """
    settings = settings or get_settings()
    found = topics_for_question(question)
    topic = found[0] if found else None
    result = Plan(question=question, topic=topic, place=place)

    seen: set[str] = set()

    def add(source: Source, reason: str, *, local=False, institution=False) -> None:
        if source.id in seen:
            return
        mode = mode_for(source, settings)
        if mode is QueryMode.UNAVAILABLE:
            result.unreachable.append(source.name)
            if source.authority_level == 1:
                result.unreachable_deciders.append(source.name)
            seen.add(source.id)
            return
        seen.add(source.id)
        result.steps.append(Step(source=source, reason=reason, mode=mode,
                                 is_local=local, is_institution=institution))

    # 1. A named institution speaks for itself, whatever the topic.
    if entity_id:
        for source in for_entity(entity_id):
            add(source, "the institution's own rule", institution=True)

    # 2. The local authority, when the topic is administered locally and we
    #    know where the reader is.
    wants_local = topic is not None and "local_authority" in topic.preferred
    if wants_local:
        if place is not None and place.known and place.authority_source_id:
            local = next((s for s in load_registry()
                          if s.id == place.authority_source_id), None)
            if local is not None:
                add(local, f"the authority for {place.department.name}", local=True)
        elif place is None or not place.known:
            # The topic turns on where you are, and nobody has said.
            result.needs_place = True

    # 3. The bodies that decide this topic nationally.
    if topic is not None:
        for wanted in topic.preferred:
            if wanted == "local_authority":
                continue
            for source in load_registry():
                if _matches_class(source, wanted) and (
                        not source.supported_topics
                        or any(p in source.supported_topics
                               for p in topic.purposes)
                        or any(_purpose_matches(source, p) for p in topic.purposes)):
                    add(source, f"a {wanted.replace('_', ' ')} for this topic")

    result.fall_back_to_corpus = topic is None or "corpus" in (topic.fallback or ())
    return result


def _purpose_matches(source: Source, purpose_id: str) -> bool:
    """Topic ids in the registry are hyphenated; purposes are underscored."""
    wanted = purpose_id.replace("_", "-")
    return any(wanted == t or wanted in t for t in source.supported_topics)
