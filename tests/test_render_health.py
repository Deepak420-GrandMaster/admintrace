"""Reading sites that only exist once their scripts have run — safely.

Rendering is the one place this system runs someone else's code. The tests
that matter most here are the refusals: the limits have to hold before a
browser is ever launched.
"""

from __future__ import annotations

import dataclasses
import os

import pytest

from app.config import get_settings
from app.live_source_check import _classify_refusal
from app.sources import store
from app.sources.registry import Health, REFRESH_HOURS, by_id, load_registry
from app.sources.render import available, render

network = pytest.mark.skipif(
    os.environ.get("ADMINTRACE_NETWORK_TESTS") != "1",
    reason="set ADMINTRACE_NETWORK_TESTS=1 to reach the live web",
)


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


@pytest.fixture(scope="module")
def mbs():
    return by_id("mbs")


def make_page(url, extra=""):
    """A page long enough to be usable.

    Every line is different on purpose: the extractor drops repeated lines as
    navigation, so a fixture built by multiplying one sentence parses to that
    one sentence and is correctly judged too short to cite.
    """
    from app.sources.extract import extract
    lines = [
        "Les conditions d'admission sont publiees chaque annee sur cette page.",
        "Le dossier se depose en ligne via le teleservice de l'etablissement.",
        "Les documents requis sont le passeport et un justificatif de domicile.",
        "Un releve de notes des deux dernieres annees est demande aux candidats.",
        "La selection comporte un entretien individuel avec un jury.",
        "Les candidats internationaux fournissent une preuve de niveau de langue.",
        "Le calendrier des sessions est mis a jour au debut de chaque annee.",
    ]
    if extra:
        lines.append(extra)
    body = "".join(f"<p>{line}</p>" for line in lines)
    return extract(
        f"<html lang='fr'><head><title>Admission</title></head><body><main>"
        f"{body}</main></body></html>", url)


# ------------------------------------------------- refusals, before a browser --

def test_rendering_refuses_a_domain_that_is_not_the_source(mbs):
    result = render("https://example.com/", source=mbs)
    assert not result.ok
    assert "not registered" in result.error


def test_rendering_refuses_plain_http(mbs):
    result = render("http://www.mbs-education.com/", source=mbs)
    assert not result.ok
    assert "https" in result.error


def test_rendering_refuses_a_lookalike_domain(mbs):
    result = render("https://mbs-education.com.attacker.io/", source=mbs)
    assert not result.ok
    assert "not registered" in result.error


# ------------------------------------------------------------ health states --

def test_every_health_state_is_distinct():
    """Collapsing these loses the difference between a bug and a refusal."""
    values = [h.value for h in Health]
    assert len(values) == len(set(values))
    for expected in ("healthy", "healthy_rendered", "stale", "changed",
                     "unavailable", "parser_failure", "blocked", "unverified"):
        assert expected in values


def test_a_bot_wall_is_blocked_not_merely_unavailable():
    """Being sent to a captcha is a refusal by the site, not a fault."""
    health, target, kind = _classify_refusal(
        "refused: redirect to 'validate.perfdrive.com', which is not registered for caf")
    assert health is Health.BLOCKED
    assert target == "validate.perfdrive.com"
    assert kind == "bot protection"


def test_a_redirect_to_an_unknown_official_looking_domain_is_a_rebrand_candidate():
    health, target, kind = _classify_refusal(
        "refused: redirect to 'new-school-name.fr', which is not registered for mbs")
    assert kind == "rebrand candidate"
    assert target == "new-school-name.fr"
    # A candidate, never a fact: it stays unusable until someone verifies it.
    assert health is Health.UNAVAILABLE


def test_an_ordinary_failure_is_simply_unavailable():
    health, target, _kind = _classify_refusal("ConnectError: no route to host")
    assert health is Health.UNAVAILABLE
    assert target == ""


def test_only_a_healthy_source_may_be_cited():
    usable = {Health.HEALTHY, Health.HEALTHY_RENDERED, Health.STALE, Health.CHANGED}
    for source in load_registry():
        expected = source.verified and source.health in usable
        assert source.is_usable is expected, source.id


def test_a_blocked_source_is_never_usable():
    blocked = [s for s in load_registry() if s.health is Health.BLOCKED]
    for source in blocked:
        assert not source.is_usable, source.id


# -------------------------------------------------------- refresh policy ----

def test_importance_sets_the_refresh_interval():
    assert REFRESH_HOURS["critical"] < REFRESH_HOURS["high"] \
        < REFRESH_HOURS["medium"] < REFRESH_HOURS["low"]


def test_each_source_declares_its_own_interval():
    for source in load_registry():
        assert source.refresh_hours == REFRESH_HOURS[source.refresh_priority]


def test_a_source_never_read_is_due(settings):
    source = by_id("mbs")
    assert store.due(source, settings)


def test_a_source_just_read_is_not_due(settings):
    source = by_id("mbs")
    version, _ = store.record(source.id, source.base_url,
                              make_page(source.base_url), "hash-1",
                              settings=settings)
    assert version is not None, "the fixture page must be usable"
    assert not store.due(source, settings)


# ------------------------------------------------ stale answer protection ----

def test_an_answer_resting_on_a_current_version_is_current(settings):
    url = "https://www.mbs-education.com/admissions/"
    version, _ = store.record("mbs", url, make_page(url), "hash-1", settings=settings)
    assert version is not None
    assert store.answer_is_current({f"mbs|{url}": version.version_id}, settings)


def test_an_answer_resting_on_a_superseded_version_is_not_current(settings):
    url = "https://www.mbs-education.com/admissions/"
    old, _ = store.record("mbs", url, make_page(url), "hash-1", settings=settings)
    store.record("mbs", url,
                 make_page(url, "Un releve bancaire est desormais obligatoire."),
                 "hash-2", settings=settings)
    assert not store.answer_is_current({f"mbs|{url}": old.version_id}, settings)


def test_a_substantive_change_stamps_the_topics_it_makes_doubtful(settings):
    url = "https://www.mbs-education.com/admissions/"
    store.record("mbs", url, make_page(url), "hash-1", settings=settings)
    _version, report = store.record(
        "mbs", url,
        make_page(url, "Un releve bancaire est desormais un document obligatoire."),
        "hash-2", settings=settings)
    assert report.is_substantive
    assert "documents" in report.affected_topics
    assert store.topic_invalidated_at("documents", settings)


# ------------------------------------------------------------------- live ----

@network
def test_rendering_reads_a_site_that_static_html_cannot():
    """ANEF, ANTS and France Travail serve an empty shell over HTTP."""
    from app.sources.extract import extract
    from app.sources.fetch import fetch

    readable = 0
    for source_id in ("anef", "ants", "france-travail"):
        source = by_id(source_id)
        static = fetch(source.base_url, expect=source)
        static_page = extract(static.body, static.final_url) if static.ok else None
        rendered = render(source.base_url, source=source)
        if not rendered.ok:
            continue
        rendered_page = extract(rendered.body, rendered.final_url)
        if rendered_page.is_usable:
            readable += 1
            # The point of the second path: static gave nothing, rendering did.
            assert static_page is None or not static_page.is_usable, source_id
    assert readable >= 1, "no JS-rendered official source could be read"


@network
def test_a_render_stays_on_the_approved_domain(mbs):
    result = render(mbs.base_url, source=mbs)
    if not result.ok:
        pytest.skip(f"MBS unreachable: {result.error}")
    from urllib.parse import urlsplit
    assert (urlsplit(result.final_url).hostname or "").endswith("mbs-education.com")
    assert result.content_type.endswith("rendered")


@network
@pytest.mark.skipif(not available(), reason="rendering extra is not installed")
def test_rendering_works_while_another_playwright_session_is_open():
    """Playwright's sync API refuses to start twice on one thread.

    Its event loop keeps running in whichever thread opened the session, so a
    second `sync_playwright()` there dies with "Sync API inside the asyncio
    loop" — which surfaced as a source that looked unreachable rather than as
    a bug in us. AdminTrace serves on asyncio and the browser tests hold a session
    of their own, so rendering has to survive a caller that already has one.
    """
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright

    source = by_id("anef")
    try:
        with sync_playwright() as driver:
            browser = driver.chromium.launch(headless=True)
            try:
                result = render(source.base_url, source=source)
            finally:
                browser.close()
    except PlaywrightError:
        # A session is already open on this thread — the browser suite holds
        # one for the whole run. That is precisely the condition under test,
        # so render straight into it rather than skipping.
        result = render(source.base_url, source=source)

    assert "Sync API" not in (result.error or ""), result.error
    if not result.ok:
        pytest.skip(f"ANEF unreachable: {result.error}")
