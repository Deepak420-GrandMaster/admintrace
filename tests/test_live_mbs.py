"""The route from "this school" to that school's own website.

The offline tests here check the routing decisions. The ones marked
``network`` actually read montpellier-bs.com and are skipped unless
``CLARE_NETWORK_TESTS=1`` — a test suite that fails because someone else's
web server is slow teaches you nothing, but a claim that live retrieval works
is worthless without having done it, so both exist.

    CLARE_NETWORK_TESTS=1 uv run pytest tests/test_live_mbs.py -v
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import pytest

from app.directory.universities import search
from app.query.entity import canonical_ids, resolve
from app.sources import live as live_sources
from app.sources import purpose
from app.sources.registry import by_id, for_entity

network = pytest.mark.skipif(
    os.environ.get("CLARE_NETWORK_TESTS") != "1",
    reason="set CLARE_NETWORK_TESTS=1 to reach the live web",
)

ENTITY = "montpellier-business-school"
QUESTION = "What do I need for admission to this school?"


@pytest.fixture(scope="module")
def mbs_source():
    source = by_id("mbs")
    assert source is not None
    return source


# ------------------------------------------------------------- routing ----

def test_the_school_register_and_the_source_registry_join_up():
    """Two lists kept for different reasons, joined on a name slug."""
    hits = search("Montpellier Business School", limit=1)
    if not hits:
        pytest.skip("institution register unavailable")
    ids = canonical_ids(hits[0])
    assert ENTITY in ids
    assert any(for_entity(candidate) for candidate in ids)


def test_the_entity_routes_to_its_own_domain(mbs_source):
    sources = for_entity(ENTITY)
    assert sources and sources[0].id == "mbs"
    assert sources[0].domain == "mbs-education.com"
    assert sources[0].live_query_enabled


def test_an_entity_with_no_registered_site_does_not_reach_the_web():
    result = live_sources.gather("some-school-nobody-registered", QUESTION)
    assert not result.ok
    assert "no verified live source" in result.error


def test_a_reference_with_nothing_to_resolve_to_never_routes_anywhere():
    """Clarify first. Guessing which school is the failure mode."""
    resolution = resolve("how do I get admission to this school", [])
    assert resolution.needs_clarification
    assert resolution.institution is None
    assert canonical_ids(resolution.institution) == ()


def test_the_school_is_carried_from_earlier_in_the_conversation():
    resolution = resolve(QUESTION, ["I'm applying to Montpellier Business School."])
    assert resolution.institution is not None
    assert resolution.from_context
    assert ENTITY in canonical_ids(resolution.institution)


def test_questions_about_what_is_true_now_ask_for_a_live_read():
    assert live_sources.wants_current_information("has the admission rule changed recently?")
    assert live_sources.wants_current_information("quelles sont les conditions actuelles ?")
    assert not live_sources.wants_current_information("what documents do I need?")


# --------------------------------------------------------------- live -----

@network
def test_page_discovery_never_leaves_the_registered_domain(mbs_source):
    urls = live_sources.candidate_pages(mbs_source, QUESTION, limit=3)
    assert urls
    for url in urls:
        assert mbs_source.allows(url), url
        assert urlsplit(url).scheme == "https"


@network
def test_admission_evidence_comes_from_the_schools_own_site():
    """The regression case, run for real."""
    result = live_sources.gather(ENTITY, QUESTION)
    assert result.ok, result.error
    assert result.source.id == "mbs"
    for item in result.evidence:
        host = urlsplit(item.canonical_url or item.url).hostname or ""
        assert host.endswith("mbs-education.com"), host
        # Not a government page standing in for the school.
        assert not host.endswith("service-public.gouv.fr")


@network
def test_every_piece_of_evidence_can_be_traced_to_a_version():
    result = live_sources.gather(ENTITY, QUESTION)
    assert result.ok, result.error
    for item in result.evidence:
        assert item.canonical_url.startswith("https://")
        assert item.retrieved_at
        assert len(item.content_hash) >= 16
        assert item.version_id
        assert item.text.strip()


@network
def test_the_old_domain_still_reaches_the_school(mbs_source):
    """montpellier-bs.com redirects to mbs-education.com; both are the school."""
    from app.sources.fetch import fetch
    result = fetch("https://www.montpellier-bs.com/", expect=mbs_source)
    assert result.ok, result.error
    assert result.source_id == "mbs"
    assert (urlsplit(result.final_url).hostname or "").endswith("mbs-education.com")


# ---------------------------------------------------- the question battery --

#: The questions a real applicant asks, and the kind of page that answers each.
BATTERY = [
    ("What are the admission requirements?", {"admissions", "requirements"}),
    ("What documents do I need to apply?", {"admissions", "requirements"}),
    ("When is the application deadline?", {"admissions", "deadline",
                                           "official_procedure"}),
    ("How much does the programme cost?", {"fees", "official_procedure"}),
    ("How can an international student apply?", {"admissions", "requirements",
                                                 "official_procedure"}),
]


@pytest.mark.parametrize("question,wanted_types", BATTERY)
@network
def test_each_real_applicant_question_finds_a_page_that_answers_it(
        mbs_source, question, wanted_types):
    """Not merely a page on the right site — a page of the right kind."""
    candidates = live_sources.discover(mbs_source, question, limit=4)
    assert candidates, question

    for candidate in candidates:
        assert mbs_source.allows(candidate.url), candidate.url

    types = {c.page_type for c in candidates}
    assert types & wanted_types, (
        f"{question!r} found only {sorted(types)}; expected one of "
        f"{sorted(wanted_types)}")


@pytest.mark.parametrize("question,_types", BATTERY)
@network
def test_no_press_release_is_ever_the_top_result(mbs_source, question, _types):
    """A news item about last year's intake is not the rule."""
    top = live_sources.discover(mbs_source, question, limit=3)[0]
    assert not purpose.is_editorial(top.page_type), (
        f"{question!r} put a {top.page_type} page first: {top.url}")


@network
def test_the_answer_cites_only_the_schools_own_pages():
    from urllib.parse import urlsplit
    result = live_sources.gather(ENTITY, "What are the admission requirements?")
    assert result.ok, result.error
    assert result.evidence
    for item in result.evidence:
        host = urlsplit(item.canonical_url or item.url).hostname or ""
        assert host.endswith("mbs-education.com"), host


@network
def test_what_the_school_does_not_publish_is_not_invented():
    """A question the site has no page for must not be answered from elsewhere."""
    result = live_sources.gather(
        ENTITY, "What is the exact bank account number for paying the deposit?")
    # Either nothing relevant was found, or whatever was found is still MBS's.
    from urllib.parse import urlsplit
    for item in result.evidence:
        host = urlsplit(item.canonical_url or item.url).hostname or ""
        assert host.endswith("mbs-education.com"), host
