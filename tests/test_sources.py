"""The rules that keep "read official sites" from becoming "read the web".

None of these touch the network. They are about the decisions the layer makes
before and after a fetch: who is allowed to be evidence, what counts as a
change worth acting on, and what must never overwrite a good version.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.config import get_settings
from app.sources import registry, store
from app.sources.change import ChangeType, compare, signal_lines
from app.sources.extract import extract
from app.sources.fetch import fetch
from app.sources.registry import hostname_allowed


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


@pytest.fixture(scope="module")
def mbs():
    source = registry.by_id("mbs")
    assert source is not None, "the MBS source must stay registered"
    return source


# ------------------------------------------------------- who may speak ----

def test_the_registry_only_holds_https_base_urls():
    for source in registry.load_registry():
        assert source.base_url.startswith("https://"), source.id


def test_an_institutions_own_domain_is_registered_for_it(mbs):
    assert "mbs-education.com" in mbs.domains
    assert mbs.source_type is registry.SourceType.SCHOOL
    assert mbs.authority_level == registry.AUTHORITY_DECIDES


def test_a_domain_the_institution_moved_away_from_still_resolves_to_it(mbs):
    """montpellier-bs.com redirects to mbs-education.com.

    Without the alias the redirect looks exactly like a hijack and the real
    site gets refused.
    """
    assert hostname_allowed(mbs, "https://www.montpellier-bs.com/")
    assert registry.for_domain("https://www.montpellier-bs.com/").id == "mbs"


@pytest.mark.parametrize("host", [
    "fake-montpellier-bs.com",
    "montpellier-bs.com.attacker.net",
    "mbs-education.com.evil.io",
    "mbseducation.com",
    "notmbs-education.com",
])
def test_a_lookalike_domain_is_not_the_institution(mbs, host):
    assert not hostname_allowed(mbs, host)
    assert registry.for_domain(host) is None


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "10.0.0.5", "[::1]"])
def test_a_bare_address_is_never_a_source(mbs, host):
    assert not hostname_allowed(mbs, host)


def test_a_legitimate_subdomain_is_the_institution(mbs):
    assert hostname_allowed(mbs, "https://careers.mbs-education.com/x")


def test_an_unregistered_domain_is_not_authoritative():
    assert registry.for_domain("https://example.com/") is None
    assert not registry.is_authoritative("https://example.com/")


def test_a_registered_but_unverified_source_is_not_authoritative():
    """Verification is a separate gate from registration."""
    unverified = [s for s in registry.load_registry() if not s.verified]
    for source in unverified:
        assert not registry.is_authoritative(source.base_url), source.id


# ------------------------------------------------------- what is fetched --

def test_plain_http_is_refused(settings):
    result = fetch("http://www.mbs-education.com/", settings=settings)
    assert not result.ok and "https" in result.error


def test_an_unregistered_host_is_refused_before_any_request(settings):
    result = fetch("https://example.com/", settings=settings)
    assert not result.ok
    assert "not a registered source" in result.error
    assert result.status == 0, "nothing should have been requested"


def test_a_user_supplied_url_cannot_make_a_domain_authoritative(settings):
    """'Use this website' must not promote a domain to evidence."""
    for url in ("https://totally-official-france.example/",
                "https://mbs-education.com.attacker.io/admissions"):
        result = fetch(url, settings=settings)
        assert not result.ok and "not a registered source" in result.error


# ------------------------------------------------------------ extraction --

def test_extraction_keeps_content_and_drops_furniture():
    page = extract("""
        <html lang="fr"><head><title>Admission</title>
        <link rel="canonical" href="https://www.mbs-education.com/admission"/>
        <meta name="description" content="Comment candidater"></head>
        <body>
          <nav>Accueil Programmes Contact</nav>
          <div class="cookie-banner">Nous utilisons des cookies</div>
          <main><h1>Admission</h1>
            <p>Les documents requis sont le passeport et un relevé de notes.</p>
            <p>La candidature se fait en ligne sur le site.</p></main>
          <script>var tracker = 1;</script>
          <footer>Mentions légales</footer>
        </body></html>
    """, "https://www.mbs-education.com/admission")
    assert page.title == "Admission"
    assert page.canonical_url == "https://www.mbs-education.com/admission"
    assert page.language == "fr"
    assert "passeport" in page.text
    assert "cookies" not in page.text
    assert "tracker" not in page.text
    assert "Mentions" not in page.text


def test_a_page_with_no_readable_text_is_not_usable():
    """A JavaScript shell parses fine and says nothing."""
    shell = extract("<html><head><title>App</title></head><body>"
                    "<div id='root'></div><script>boot()</script></body></html>",
                    "https://www.mbs-education.com/")
    assert not shell.is_usable


# -------------------------------------------------------- what changed ----

def test_a_reworded_banner_is_not_an_administrative_change():
    old = "Bienvenue.\nLes documents requis sont le passeport et le justificatif.\nActualite du jour."
    new = "Bienvenue !\nLes documents requis sont le passeport et le justificatif.\nActualite de la semaine."
    assert compare(old, new).change_type is ChangeType.COSMETIC


def test_a_changed_document_list_is_critical():
    old = "Les documents requis sont le passeport et le justificatif."
    new = "Les documents requis sont le passeport, le justificatif et un releve bancaire."
    report = compare(old, new)
    assert report.change_type is ChangeType.CRITICAL
    assert report.is_substantive


def test_identical_content_is_no_change():
    text = "Les conditions d'admission sont publiees chaque annee."
    assert compare(text, text).change_type is ChangeType.NONE


def test_signal_lines_ignore_prose_without_administrative_meaning():
    assert signal_lines("Nous sommes ravis de vous accueillir sur le campus.") == set()
    assert signal_lines("Les documents requis sont indiques ci-dessous.")


# ------------------------------------------------- versions and safety ----

def _page(text, title="Admission"):
    return extract(
        f"<html lang='fr'><head><title>{title}</title></head><body><main>"
        f"<p>{text}</p></main></body></html>",
        "https://www.mbs-education.com/admission")


URL = "https://www.mbs-education.com/admission"
LONG = (" ".join(["Les documents requis sont le passeport et le justificatif de domicile."] * 8))


def test_a_first_version_is_stored_and_activated(settings):
    version, report = store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    assert version is not None
    assert report.change_type is ChangeType.NEW
    assert store.active("mbs", URL, settings).version_id == version.version_id


def test_an_unusable_page_never_replaces_a_good_version(settings):
    good, _ = store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    shell = extract("<html><head><title>x</title></head><body><div id=root>"
                    "</div></body></html>", URL)
    version, report = store.record("mbs", URL, shell, "hash-2", settings=settings)
    assert version is None
    assert "refused" in report.summary
    assert store.active("mbs", URL, settings).version_id == good.version_id


def test_a_substantive_change_invalidates_derived_work(settings):
    store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    verdicts = settings.cache_dir
    verdicts.mkdir(parents=True, exist_ok=True)
    (verdicts / "answerability.json").write_text("{}", encoding="utf-8")

    changed = LONG + " Un releve bancaire est desormais obligatoire."
    _version, report = store.record("mbs", URL, _page(changed), "hash-2",
                                    settings=settings)
    assert report.is_substantive
    assert not (verdicts / "answerability.json").exists()


def test_rollback_restores_the_previous_version(settings):
    first, _ = store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    second, _ = store.record("mbs", URL, _page(LONG + " Nouvelle condition requise."),
                             "hash-2", settings=settings)
    assert store.active("mbs", URL, settings).version_id == second.version_id

    restored = store.rollback("mbs", URL, settings)
    assert restored is not None
    assert restored.version_id == first.version_id
    assert store.active("mbs", URL, settings).version_id == first.version_id


def test_every_decision_is_written_to_the_audit_log(settings):
    store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    log = (store.root(settings) / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in log.splitlines() if line.strip()]
    assert "version.activated" in events


def test_freshness_is_unknown_before_anything_is_stored(settings):
    assert store.freshness("mbs", URL, 24, settings) is store.Freshness.UNKNOWN


def test_freshness_is_fresh_inside_the_window_and_stale_outside(settings):
    store.record("mbs", URL, _page(LONG), "hash-1", settings=settings)
    assert store.freshness("mbs", URL, 24, settings) is store.Freshness.FRESH
    assert store.freshness("mbs", URL, 0, settings) is store.Freshness.STALE
