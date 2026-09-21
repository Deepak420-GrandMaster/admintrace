"""The trust model: right authority, right date, right version, supported claims.

The distinction these exist to protect is the one this system kept getting
wrong: "the authority does not publish this" and "the authority could not be
read today" look identical from inside the retrieval code and are opposite
from outside it. Telling somebody there is no official information, when the
truth is that a site was behind a bot wall that afternoon, sends them away
from an answer that exists.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date

import pytest

from app.config import get_settings
from app.query.dates import parse as parse_date_question
from app.query.entity import Resolution
from app.retrieval.answerability import (Answerability, classify,
                                         is_access_failure,
                                         may_answer_confidently,
                                         should_show_near_misses)
from app.sources import claims as claim_store
from app.sources import store
from app.sources.change import compare
from app.sources.claims import Claim, claim_id_for, parse_date
from app.sources.conflict import (Classification, classify as classify_pair,
                                  detect, relationship, resolve)
from app.sources.extract import extract
from app.sources.route import plan

NOW = date(2026, 9, 15)
URL = "https://www.herault.gouv.fr/demarches/titre-de-sejour/"


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


def make_page(validity="trois mois", extra=""):
    lines = [
        "Renouvellement du titre de sejour pour les etudiants.",
        "Le dossier se depose en ligne sur le teleservice de la prefecture.",
        f"Le justificatif de domicile doit dater de moins de {validity}.",
        "Les documents requis sont le passeport et la carte etudiante.",
        "Un rendez-vous est necessaire pour le depot du dossier complet.",
    ]
    if extra:
        lines.append(extra)
    body = "".join(f"<p>{line}</p>" for line in lines)
    return extract(f"<html lang='fr'><head><title>Titre de sejour</title></head>"
                   f"<body><main>{body}</main></body></html>", URL)


def claim(text, *, source="nat", jurisdiction="national", level=2,
          effective="", until=""):
    return Claim(claim_id=claim_id_for(source, text), text=text,
                 source_id=source, version_id="v1", url="https://x",
                 authority_level=level, jurisdiction=jurisdiction,
                 effective_from=effective, effective_until=until,
                 retrieved_at="2026-09-15T00:00:00+00:00")


# ---------------------------------------------- states are not collapsed --

def test_a_gap_and_an_access_failure_are_different_answers():
    """The distinction the whole phase exists for."""
    blank = Resolution()
    gap = classify(blank, refused=True, citations=0,
                   authority_known=True, authority_reachable=True)
    down = classify(blank, refused=True, citations=0,
                    authority_known=True, authority_reachable=False)
    assert gap is Answerability.SOURCE_GAP
    assert down is Answerability.AUTHORITY_UNAVAILABLE
    assert gap is not down


def test_every_state_has_a_distinct_value():
    values = [s.value for s in Answerability]
    assert len(values) == len(set(values))


def test_only_a_supported_state_may_be_answered_confidently():
    for state in Answerability:
        assert may_answer_confidently(state) is (state is Answerability.SUPPORTED)


def test_an_access_failure_is_never_reported_as_missing_coverage():
    for state in (Answerability.AUTHORITY_UNAVAILABLE,
                  Answerability.RETRIEVAL_FAILURE, Answerability.STALE):
        assert is_access_failure(state)
        assert not should_show_near_misses(state)


def test_a_conflict_outranks_a_perfectly_good_looking_answer():
    """Two official pages disagreeing is not something to average."""
    state = classify(Resolution(), refused=False, citations=5, conflicts=1)
    assert state is Answerability.CONFLICTING_SOURCES


def test_stale_evidence_is_never_presented_as_current():
    state = classify(Resolution(), refused=False, citations=5, stale_evidence=True)
    assert state is Answerability.STALE


def test_an_unfinished_question_outranks_everything():
    state = classify(Resolution(needs_clarification=True), refused=False,
                     citations=9, conflicts=3)
    assert state is Answerability.NEEDS_CLARIFICATION


def test_a_missing_required_part_is_partial_not_supported():
    state = classify(Resolution(), refused=False, citations=4,
                     missing_required=True)
    assert state is Answerability.PARTIAL


# ------------------------------------------------------------- provenance --

def test_a_claim_keeps_its_identity_across_versions():
    """So a change reads as "this requirement moved", not as two edits."""
    text = "Les documents requis sont le passeport et la carte etudiante."
    assert claim_id_for("nat", text) == claim_id_for("nat", text)
    assert claim_id_for("nat", text) != claim_id_for("loc", text)


def test_claims_carry_where_they_came_from(settings):
    version, _ = store.record("prefecture-herault", URL, make_page(), "h1",
                              settings=settings)
    found = claim_store.load("prefecture-herault", version.version_id, settings)
    assert found
    for item in found:
        assert item.source_id == "prefecture-herault"
        assert item.version_id == version.version_id
        assert item.url
        assert item.retrieved_at
        assert item.jurisdiction


def test_only_actionable_sentences_become_claims(settings):
    page = extract(
        "<html lang='fr'><head><title>T</title></head><body><main>"
        "<p>Bienvenue sur le site de la prefecture de ce departement.</p>"
        "<p>Les documents requis sont le passeport et un justificatif.</p>"
        "<p>Nos equipes vous souhaitent une excellente journee ensoleillee.</p>"
        "</main></body></html>", URL)
    found = claim_store.extract(page.text, source_id="x", version_id="v",
                                url=URL)
    assert len(found) == 1
    assert "documents requis" in found[0].text


# ---------------------------------------------------------- effective dates --

@pytest.mark.parametrize("written,iso", [
    ("1er juillet 2026", "2026-07-01"),
    ("2026-07-01", "2026-07-01"),
    ("01/07/2026", "2026-07-01"),
    ("1 July 2026", "2026-07-01"),
])
def test_an_official_page_writes_a_date_many_ways(written, iso):
    assert parse_date(written) == iso


def test_a_rule_published_early_does_not_apply_early():
    """Published in June, effective in July, is not the rule in June."""
    future = claim("A compter du 1er juillet 2026, une attestation est requise.",
                   effective="2026-07-01")
    assert not future.applies_on(date(2026, 6, 15))
    assert future.applies_on(date(2026, 7, 1))
    assert future.applies_on(date(2026, 9, 15))


def test_a_claim_with_no_stated_start_is_in_force():
    """Most pages never state one; treating that as "not yet" would empty them."""
    assert claim("Les documents requis sont le passeport.").applies_on(NOW)


def test_a_claim_that_has_expired_no_longer_applies():
    expired = claim("Mesure temporaire.", until="2026-01-31")
    assert not expired.applies_on(NOW)
    assert expired.applies_on(date(2026, 1, 1))


def test_the_extractor_reads_an_effective_date_out_of_the_sentence():
    found = claim_store.extract(
        "A compter du 1er juillet 2026, un document supplementaire est obligatoire.",
        source_id="x", version_id="v", url=URL)
    assert found and found[0].effective_from == "2026-07-01"


# ------------------------------------------------------- date-aware asking --

@pytest.mark.parametrize("question,expected,historical", [
    ("What is the rule now?", NOW, False),
    ("What was the rule in June 2026?", date(2026, 6, 30), True),
    ("What was the rule before July 2026?", date(2026, 6, 30), True),
    ("Quelle etait la regle en juin 2026 ?", date(2026, 6, 30), True),
    ("What documents do I need?", NOW, False),
])
def test_a_question_carries_the_date_it_is_about(question, expected, historical):
    asked = parse_date_question(question, now=NOW)
    assert asked.on == expected
    assert asked.historical is historical


def test_a_question_about_change_is_recognised_as_such():
    assert parse_date_question("What changed in July 2026?", now=NOW).about_change
    assert not parse_date_question("What do I need?", now=NOW).about_change


# --------------------------------------------------------------- history ---

def test_a_historical_question_reads_the_version_in_force_then(settings):
    first, _ = store.record("prefecture-herault", URL, make_page("trois mois"),
                            "h1", settings=settings)
    second, _ = store.record("prefecture-herault", URL, make_page("un an"),
                             "h2", settings=settings)
    assert store.active("prefecture-herault", URL, settings).version_id \
        == second.version_id
    # Everything stored today, so a date before today has no covering version.
    assert store.version_on("prefecture-herault", URL, date(2020, 1, 1),
                            settings) is None
    # And today resolves to the newest.
    assert store.version_on("prefecture-herault", URL, date.today(),
                            settings).version_id == second.version_id


def test_an_unverifiable_past_is_reported_rather_than_reconstructed(settings):
    """No stored version means we cannot say; it does not mean we may guess."""
    store.record("prefecture-herault", URL, make_page(), "h1", settings=settings)
    assert not store.has_history_before("prefecture-herault", URL,
                                        date(2019, 1, 1), settings)


def test_old_versions_survive_so_a_rollback_has_something_to_return_to(settings):
    store.record("prefecture-herault", URL, make_page("trois mois"), "h1",
                 settings=settings)
    store.record("prefecture-herault", URL, make_page("un an"), "h2",
                 settings=settings)
    assert len(store.history("prefecture-herault", URL, settings)) == 2
    restored = store.rollback("prefecture-herault", URL, settings)
    assert restored is not None


# -------------------------------------------------------------- conflicts --

def test_a_local_authority_asking_for_more_is_not_a_contradiction():
    """The commonest case, and reporting it as conflict teaches distrust."""
    national = claim("Les documents requis sont le passeport et un justificatif.")
    local = claim("Les documents requis sont le passeport, un justificatif "
                  "et un formulaire local.", source="loc",
                  jurisdiction="department", level=1)
    assert classify_pair(national, local) is Classification.ADDITIVE
    assert not detect([national], [local])


def test_the_same_measure_with_two_values_is_a_contradiction():
    national = claim("Le justificatif doit dater de moins de trois mois.")
    local = claim("Le justificatif doit dater de moins de six mois.",
                  source="loc", jurisdiction="department", level=1)
    assert classify_pair(national, local) is Classification.CONTRADICTORY


def test_the_same_period_written_two_ways_is_not_a_contradiction():
    spelled = claim("Le justificatif doit dater de moins de trois mois.")
    digits = claim("Le justificatif doit dater de moins de 3 mois.",
                   source="loc", jurisdiction="department", level=1)
    assert classify_pair(spelled, digits) is not Classification.CONTRADICTORY


def test_replacing_a_requirement_outright_is_a_contradiction():
    national = claim("Le passeport est requis pour la demande complete.")
    local = claim("Le passeport n est plus requis pour la demande complete.",
                  source="loc", jurisdiction="department", level=1)
    assert classify_pair(national, local) is Classification.CONTRADICTORY


def test_statements_scoped_to_different_cases_can_both_hold():
    general = claim("Le passeport est requis pour la demande complete.")
    scoped = claim("Si vous etes etudiant, une attestation de scolarite est "
                   "requise pour la demande complete.", source="loc",
                   jurisdiction="department", level=1)
    assert classify_pair(general, scoped) is Classification.CONDITIONAL


def test_unrelated_statements_are_not_compared_as_rules():
    a = claim("Les documents requis sont le passeport et un justificatif.")
    b = claim("Les horaires du guichet sont affiches en ligne chaque semaine.",
              source="loc", jurisdiction="department", level=1)
    assert classify_pair(a, b) is Classification.COMPATIBLE


def test_a_conflict_record_carries_both_sides_and_a_decision():
    national = claim("Le passeport est requis pour la demande complete.")
    local = claim("Le passeport n est plus requis pour la demande complete.",
                  source="loc", jurisdiction="department", level=1)
    found = detect([national], [local])
    assert len(found) == 1
    record = found[0]
    assert record.conflict_id.startswith("CONF-")
    assert record.claim_a and record.claim_b
    assert record.source_a and record.source_b
    assert record.jurisdiction_a != record.jurisdiction_b
    assert record.classification == "contradictory"
    assert record.detected_at
    assert record.prefer and record.prefer_reason


def test_a_newer_national_page_does_not_beat_the_counter_you_stand_at():
    """Resolution order is jurisdiction first, recency last."""
    national = claim("Le passeport est requis.", source="nat",
                     jurisdiction="national", level=1)
    national.retrieved_at = "2026-09-15T00:00:00+00:00"
    local = claim("Le passeport n est plus requis.", source="loc",
                  jurisdiction="department", level=1)
    local.retrieved_at = "2026-01-01T00:00:00+00:00"
    winner, reason = resolve(national, local, on=NOW)
    assert winner.source_id == "loc"
    assert "specific" in reason


def test_a_rule_not_yet_in_force_loses_to_one_that_is():
    current = claim("Le passeport est requis.")
    future = claim("A compter du 1er juillet 2027, le passeport n est plus requis.",
                   source="loc", jurisdiction="department", level=1,
                   effective="2027-07-01")
    winner, reason = resolve(current, future, on=NOW)
    assert winner.source_id == "nat"
    assert "not in force" in reason


def test_the_overall_relationship_reports_the_worst_pair():
    national = [claim("Le passeport est requis pour la demande complete.")]
    local = [claim("Le passeport n est plus requis pour la demande complete.",
                   source="loc", jurisdiction="department", level=1)]
    assert relationship(national, local) is Classification.CONTRADICTORY


# ------------------------------------------------- change becomes a claim --

def test_a_changed_requirement_is_reported_in_the_sources_own_words(settings):
    store.record("prefecture-herault", URL, make_page("trois mois"), "h1",
                 settings=settings)
    _version, report = store.record("prefecture-herault", URL,
                                    make_page("un an"), "h2", settings=settings)
    assert report.is_substantive
    summary = report.before_after()
    assert "BEFORE:" in summary and "AFTER:" in summary
    assert "trois mois" in summary and "un an" in summary


def test_a_requirement_that_moved_leaves_the_old_claim_pending_review(settings):
    first, _ = store.record("prefecture-herault", URL, make_page("trois mois"),
                            "h1", settings=settings)
    store.record("prefecture-herault", URL, make_page("un an"), "h2",
                 settings=settings)
    old = claim_store.load("prefecture-herault", first.version_id, settings)
    pending = [c for c in old if c.status == claim_store.STALE_PENDING_REVIEW]
    assert pending, "the superseded requirement must not still read as current"


def test_a_cosmetic_edit_leaves_every_claim_alone(settings):
    first, _ = store.record("prefecture-herault", URL, make_page(), "h1",
                            settings=settings)
    store.record("prefecture-herault", URL,
                 make_page(extra="Nos equipes vous souhaitent une bonne visite."),
                 "h2", settings=settings)
    old = claim_store.load("prefecture-herault", first.version_id, settings)
    assert all(c.is_current for c in old)


def test_an_answer_stops_being_current_when_its_version_moves(settings):
    first, _ = store.record("prefecture-herault", URL, make_page("trois mois"),
                            "h1", settings=settings)
    key = {f"prefecture-herault|{URL}": first.version_id}
    assert store.answer_is_current(key, settings)
    store.record("prefecture-herault", URL, make_page("un an"), "h2",
                 settings=settings)
    assert not store.answer_is_current(key, settings)


# ----------------------------------------------------------------- CAF ----

def test_caf_is_known_to_be_the_authority_and_known_to_be_unreadable():
    """Not "no official information exists" — a specific, named blockage."""
    from app.sources.registry import Health, by_id
    caf = by_id("caf")
    assert caf is not None
    assert caf.authority_level == 1, "CAF decides this benefit"
    assert caf.health is Health.BLOCKED
    assert not caf.is_usable


def test_a_caf_question_names_caf_as_the_unreachable_decider():
    routing = plan("Am I eligible for CAF housing assistance?")
    assert routing.unreachable_deciders
    assert any("allocations familiales" in name.lower()
               for name in routing.unreachable_deciders)


def test_no_caf_policy_is_encoded_anywhere_in_source():
    """The source is unreadable, so any rule in code would be invented."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for phrase in ("aide personnalisee au logement is", "apl is granted",
                       "caf eligibility requires", "students are eligible for caf"):
            assert phrase not in text, f"{path.name} encodes a CAF rule"


# --------------------------------------------------------------- alerts ---

def test_a_high_severity_change_is_offered_for_notification():
    report = compare(
        "Le justificatif doit dater de moins de trois mois.",
        "Le justificatif doit dater de moins de six mois.")
    assert report.needs_attention


def test_a_notification_failure_never_loses_the_change(monkeypatch, settings):
    from app.feedback import mail
    monkeypatch.setattr(mail, "send_source_change",
                        lambda **kwargs: (False, "SMTPConnectError"))
    store.record("prefecture-herault", URL, make_page("trois mois"), "h1",
                 settings=settings)
    version, report = store.record("prefecture-herault", URL,
                                   make_page("un an"), "h2", settings=settings)
    # The version is stored and activated regardless of any notification.
    assert version is not None
    assert store.active("prefecture-herault", URL, settings).version_id \
        == version.version_id
    assert report.is_substantive


def test_a_change_alert_never_carries_configuration():
    from app.feedback import mail
    subject = mail.source_change_subject("Préfecture", "high", "changed")
    assert "ADMINTRACE SOURCE CHANGE" in subject
    assert "HIGH" in subject
    for secret in ("smtp", "password", "api_key"):
        assert secret not in subject.lower()


def test_the_audit_log_records_every_claim_change(settings):
    store.record("prefecture-herault", URL, make_page("trois mois"), "h1",
                 settings=settings)
    store.record("prefecture-herault", URL, make_page("un an"), "h2",
                 settings=settings)
    log = (store.root(settings) / "audit.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line)["event"] for line in log.splitlines() if line.strip()]
    assert "claims.changed" in events
    assert "source.change_needs_review" in events
