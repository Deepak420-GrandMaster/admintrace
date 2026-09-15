"""Which authority the reader is standing in front of.

French immigration procedure is national law applied at a préfecture counter,
and what that counter wants is not what the national page says. Answering from
the national page alone is how somebody arrives with the wrong folder — these
tests are about not doing that, and about not guessing when nobody has said
where they are.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

from app.sources import jurisdiction
from app.sources.gating import QueryMode, evaluate, mode_for
from app.sources.jurisdiction import Place, resolve
from app.sources.registry import Health, JurisdictionLevel, by_id, load_registry
from app.sources.route import plan, topics, topics_for_question

network = pytest.mark.skipif(
    os.environ.get("CLARE_NETWORK_TESTS") != "1",
    reason="set CLARE_NETWORK_TESTS=1 to reach the live web",
)


# ------------------------------------------------------------ resolving ---

@pytest.mark.parametrize("question,city,department", [
    ("I live in Montpellier and need to renew my permit.", "Montpellier", "Hérault"),
    ("Je vis à Lyon, comment renouveler mon titre de séjour ?", "Lyon", "Rhône"),
    ("I'm based in Lille.", "Lille", "Nord"),
    ("I live in Castelnau-le-Lez", "Castelnau-le-Lez", "Hérault"),
])
def test_a_town_resolves_to_the_authority_that_administers_it(question, city, department):
    place = resolve(question)
    assert place.city == city
    assert place.department is not None and place.department.name == department
    assert place.authority_name


def test_a_department_named_outright_wins():
    place = resolve("I am in the Hérault")
    assert place.department.name == "Hérault"


def test_a_question_with_no_place_resolves_to_nothing():
    """Not a failure — just an unanswered half of the question."""
    place = resolve("What do I need to renew my residence permit?")
    assert not place.known
    assert place.city == ""


def test_a_capitalised_sentence_is_not_read_as_a_place_name():
    """The case-insensitive flag once made [A-Z] match everything."""
    place = resolve("I live in Montpellier and need to renew my student permit")
    assert place.city == "Montpellier"


def test_an_institutions_department_can_supply_the_place():
    """A school's département is known; the reader should not have to say it."""
    place = resolve("what about admission?", hint_department="Hérault")
    assert place.department.name == "Hérault"
    assert place.authority_name == "Préfecture de l'Hérault"


def test_paris_is_not_served_by_a_department_domain():
    """There is no paris.gouv.fr; the Préfecture de police handles it."""
    place = resolve("I live in Paris")
    assert place.department.name == "Paris"
    assert place.authority_source_id == "prefecture-police-paris"
    source = by_id("prefecture-police-paris")
    assert source.domain == "prefecturedepolice.interieur.gouv.fr"


def test_every_registered_department_names_an_authority_that_exists():
    ids = {s.id for s in load_registry()}
    for department in jurisdiction.departments():
        assert department.prefecture_source in ids, department.name


# -------------------------------------------------------------- routing ---

def test_a_residence_permit_question_puts_the_local_authority_first():
    """The whole point: national rule, local counter, local asked first."""
    question = "I live in Montpellier and need to renew my residence permit"
    routing = plan(question, place=resolve(question))
    assert routing.topic.id == "residence_permit"
    assert routing.steps
    assert routing.steps[0].is_local
    assert routing.steps[0].source.id == "prefecture-herault"


def test_a_residence_permit_question_still_reaches_national_sources():
    question = "I live in Montpellier and need to renew my residence permit"
    routing = plan(question, place=resolve(question))
    levels = {s.source.jurisdiction for s in routing.steps}
    assert JurisdictionLevel.DEPARTMENT in levels
    assert JurisdictionLevel.NATIONAL in levels


def test_a_locally_administered_question_without_a_place_asks_rather_than_averaging():
    routing = plan("What do I need to renew my residence permit?", place=Place())
    assert routing.needs_place


def test_an_institution_question_does_not_route_to_government_at_all():
    """No government page can state a school's own admission rule."""
    routing = plan("What are the admission requirements?",
                   entity_id="montpellier-business-school")
    assert routing.topic.id == "admission"
    assert routing.steps[0].is_institution
    assert routing.steps[0].source.id == "mbs"
    assert not any(s.is_local for s in routing.steps)


def test_an_institution_question_never_asks_for_a_place():
    routing = plan("What are the admission requirements?",
                   entity_id="montpellier-business-school")
    assert not routing.needs_place


def test_an_unreachable_authority_is_named_rather_than_dropped():
    """"The préfecture is down" and "there is no answer" are different."""
    question = "I live in Montpellier and need to renew my residence permit"
    routing = plan(question, place=resolve(question))
    # interieur is currently unreachable and must be reported, not ignored.
    assert routing.unreachable or all(
        s.mode is not QueryMode.UNAVAILABLE for s in routing.steps)


@pytest.mark.parametrize("question,topic", [
    ("What do I need to renew my residence permit?", "residence_permit"),
    ("What are the admission requirements?", "admission"),
    ("How do I register as a job seeker?", "work"),
    ("How do I declare my income?", "taxes"),
])
def test_a_question_lands_on_the_right_administrative_topic(question, topic):
    found = topics_for_question(question)
    assert found and found[0].id == topic


def test_every_topic_declares_who_answers_it():
    for topic in topics():
        assert topic.preferred, topic.id
        assert topic.required, topic.id


# --------------------------------------------------------------- gating ---

def test_a_blocked_source_is_never_promoted_to_live():
    for source in load_registry():
        if source.health is Health.BLOCKED:
            assert mode_for(source) is not QueryMode.LIVE_QUERY, source.id


def test_an_unverified_source_is_never_live():
    for source in load_registry():
        if not source.verified:
            assert mode_for(source) is not QueryMode.LIVE_QUERY, source.id


def test_the_gate_names_the_check_that_failed():
    down = next((s for s in load_registry()
                 if s.health is Health.UNAVAILABLE), None)
    if down is None:
        pytest.skip("no unavailable source registered right now")
    result = evaluate(down)
    assert result.failures
    assert result.why()


def test_a_source_eligible_but_not_switched_on_stays_sync_only():
    """Promotion is deliberate; passing the gate is not the same as being on."""
    for source in load_registry():
        if not source.live_query_enabled:
            assert mode_for(source) is not QueryMode.LIVE_QUERY, source.id


def test_the_corpus_is_deliberately_not_queried_live():
    """It is already indexed locally; re-fetching it page by page is slower."""
    corpus = by_id("service-public")
    assert not corpus.live_query_enabled
    assert mode_for(corpus) is QueryMode.SYNC_ONLY


# ----------------------------------------------------------- the real run --

@network
def test_montpellier_residence_permit_reads_the_herault_prefecture():
    """The end-to-end case, for real."""
    from app.sources.live import gather_plan

    question = ("I live in Montpellier and need to renew my student "
                "residence permit. What do I need?")
    place = resolve(question)
    routing = plan(question, place=place)
    assert place.authority_name == "Préfecture de l'Hérault"

    found = gather_plan(routing, question, per_source=2)
    assert found.ok, found.error
    hosts = {urlsplit(e.canonical_url or e.url).hostname or "" for e in found.evidence}
    assert any(h.endswith("herault.gouv.fr") for h in hosts), hosts
    assert found.local_source is not None
    assert found.local_source.id == "prefecture-herault"


@network
def test_every_prefecture_registered_is_actually_reachable():
    from app.sources.extract import extract
    from app.sources.fetch import fetch

    unreadable = []
    for source in load_registry():
        if source.jurisdiction is not JurisdictionLevel.DEPARTMENT:
            continue
        result = fetch(source.base_url, expect=source)
        page = extract(result.body, result.final_url) if result.ok else None
        if page is not None and page.is_usable:
            continue
        if source.render_enabled:
            from app.sources.render import render
            rendered = render(source.base_url, source=source)
            if rendered.ok and extract(rendered.body, rendered.final_url).is_usable:
                continue
        unreadable.append(source.id)
    assert not unreadable, f"registered but unreadable: {unreadable}"
