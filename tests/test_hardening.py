"""Production honesty: status that is derived, and answers that are checked.

The failure this phase guards against is not a crash. It is a team believing a
capability is proven because a file said so, and a reader acting on an answer
that cited a genuinely official page about something else.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from app.answer.validate import validate
from app.config import get_settings
from app.sources import claims as claim_store
from app.sources import incidents, review, store
from app.sources.live import Evidence
from app.sources.store import Freshness
from app.status import Verification, assess, history_metrics, write_scorecard

URL = "https://www.herault.gouv.fr/demarches/titre-de-sejour/"


def make_page(url=URL, marker=""):
    """A page long enough to be stored.

    Distinct lines on purpose: the extractor drops repeated ones as
    navigation, so a fixture built by multiplying a sentence parses to that
    sentence and is correctly judged too short to cite.
    """
    from app.sources.extract import extract
    lines = [
        "Renouvellement du titre de sejour pour les etudiants etrangers.",
        "Le dossier se depose en ligne sur le teleservice de la prefecture.",
        "Les documents requis sont le passeport et un justificatif de domicile.",
        "Un releve de notes de l annee en cours est demande aux candidats.",
        "Un rendez-vous est necessaire pour le depot du dossier complet.",
        "La procedure est gratuite et se fait uniquement par voie numerique.",
    ]
    if marker:
        lines.append(marker)
    body = "".join(f"<p>{line}</p>" for line in lines)
    return extract(f"<html lang='fr'><head><title>Titre de sejour</title></head>"
                   f"<body><main>{body}</main></body></html>", url)


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


def evidence(source_id="mbs", *, area="", version="v1",
             freshness=Freshness.LIVE_VERIFIED, traceable=True):
    return Evidence(
        source_id=source_id, source_name=source_id, domain="x.fr",
        url="https://x.fr/page", canonical_url="https://x.fr/page" if traceable else "",
        title="t", text="body", retrieved_at="2026-09-15T00:00:00+00:00",
        content_hash="h" * 16 if traceable else "", freshness=freshness,
        version_id=version if traceable else "", jurisdiction_area=area)


# ----------------------------------------------- status is derived, not set --

def test_the_scorecard_is_computed_from_disk_not_declared():
    """A hand-maintained readiness file is a readiness file that goes stale."""
    first = assess()
    second = assess()
    assert {k: v.status for k, v in first.items()} == \
           {k: v.status for k, v in second.items()}
    for capability in first.values():
        assert capability.evidence, f"{capability.name} claims nothing"


def test_a_capability_with_no_real_instance_is_not_called_production_verified():
    """Tested is not proven. That distinction is the whole point."""
    capabilities = assess()
    for key in ("change_detection", "conflict_detection"):
        cap = capabilities[key]
        if cap.detail.get("live", 0) == 0:
            assert cap.status is Verification.MECHANISM_VERIFIED, key


def test_shallow_history_is_reported_as_shallow(settings):
    """Keeping versions is cheap; having enough of them takes weeks."""
    cap = assess(settings)["historical_retrieval"]
    assert cap.status in (Verification.INSUFFICIENT_HISTORY,
                          Verification.PRODUCTION_VERIFIED)
    if cap.detail["days"] < settings.minimum_historical_days:
        assert cap.status is Verification.INSUFFICIENT_HISTORY


def test_an_unconfigured_mailbox_is_never_reported_as_working():
    cap = assess()["smtp"]
    settings = get_settings()
    if not settings.email_configured:
        assert cap.status is Verification.NOT_CONFIGURED
        assert cap.status is not Verification.PRODUCTION_VERIFIED


def test_caf_is_reported_blocked_rather_than_missing():
    cap = assess()["caf"]
    assert cap.status is Verification.BLOCKED
    assert "authority known" in cap.evidence


def test_the_scorecard_is_machine_readable(settings):
    path = write_scorecard(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["generated_at"]
    assert payload["capabilities"]
    for key, row in payload["capabilities"].items():
        assert row["status"] in {v.value for v in Verification}, key


def test_history_metrics_expose_the_shallowness_rather_than_hiding_it(settings):
    version, _ = store.record("prefecture-herault", URL, make_page(), "h1",
                              settings=settings)
    assert version is not None
    metrics = history_metrics(settings)["prefecture-herault"]
    assert metrics["version_count"] == 1
    assert metrics["days_of_history"] == 0
    assert metrics["oldest_version"]


# ------------------------------------------------------- answer validation --

def test_a_question_about_a_school_answered_from_government_is_caught():
    """Reads exactly like a good answer, because the page is genuinely official."""
    result = validate([evidence("service-public")], entity_source_id="mbs")
    assert not result.ok
    assert "entity" in result.why()


def test_a_question_about_a_school_answered_by_the_school_passes():
    assert validate([evidence("mbs")], entity_source_id="mbs").ok


def test_a_montpellier_question_answered_from_the_rhone_is_caught():
    result = validate([evidence("prefecture-rhone", area="Rhône")],
                      expected_area="Hérault")
    assert not result.ok
    assert "jurisdiction" in result.why()


def test_a_montpellier_question_answered_from_herault_passes():
    assert validate([evidence("prefecture-herault", area="Hérault")],
                    expected_area="Hérault").ok


def test_national_evidence_is_not_a_jurisdiction_mismatch():
    """A national page is legitimately supplementary to a local one."""
    assert validate([evidence("anef", area="")], expected_area="Hérault").ok


def test_a_rule_not_in_force_on_the_asked_date_is_caught():
    from app.sources.claims import Claim
    future = Claim(claim_id="c1", text="A compter de la date indiquee.",
                   source_id="x", version_id="v1", url="https://x",
                   effective_from="2027-01-01")
    result = validate([evidence()], claims=[future], on=date(2026, 9, 15))
    assert not result.ok
    assert "effective_dates" in result.why()


def test_untraceable_evidence_is_caught():
    result = validate([evidence(traceable=False)])
    assert not result.ok
    assert "provenance" in result.why()


def test_stale_evidence_is_not_allowed_to_read_as_current():
    result = validate([evidence(freshness=Freshness.STALE)])
    assert not result.ok
    assert "freshness" in result.why()


def test_stale_evidence_may_be_used_when_it_is_labelled_as_such():
    assert validate([evidence(freshness=Freshness.STALE)], allow_stale=True).ok


def test_an_unresolved_contradiction_blocks_a_confident_answer():
    result = validate([evidence()], conflicts=[{"conflict_id": "CONF-1"}])
    assert not result.ok
    assert "conflicts" in result.why()


# ------------------------------------------------------------- incidents ---

@pytest.mark.parametrize("error,status,kind", [
    ("refused: redirect to 'validate.perfdrive.com', which is not registered", 0, "bot_wall"),
    ("ConnectTimeout: timed out after 20s", 0, "timeout"),
    ("http 403", 403, "forbidden"),
    ("page fetched but parsed to nothing readable", 200, "parser_failure"),
    ("ConnectError: nodename nor servname provided", 0, "dns"),
])
def test_a_failure_is_classified_not_just_recorded(error, status, kind):
    """A timeout and a bot wall need different responses; "error" says neither."""
    assert incidents.classify(error, status) == kind


def test_repeated_failures_accumulate_rather_than_overwrite(settings):
    """One failure is noise; the same failure for two days is a decision."""
    first = incidents.record("caf", kind="bot_wall", detail="x", settings=settings)
    assert first["occurrence_count"] == 1
    second = incidents.record("caf", kind="bot_wall", detail="x", settings=settings)
    assert second["occurrence_count"] == 2
    assert second["first_seen"] == first["first_seen"]


def test_a_recovered_source_closes_its_incident_rather_than_losing_it(settings):
    incidents.record("caf", kind="bot_wall", detail="blocked", settings=settings)
    assert incidents.open_incidents(settings)
    recovered = incidents.resolve("caf", settings)
    assert recovered
    assert not incidents.open_incidents(settings)
    # Closed, not deleted: the outage stays on the record.
    assert incidents.history("caf", settings)[0]["current_status"] == "resolved"


def test_recovery_of_one_source_leaves_another_alone(settings):
    incidents.record("caf", kind="bot_wall", settings=settings)
    incidents.record("interieur", kind="forbidden", settings=settings)
    incidents.resolve("caf", settings)
    still_open = {i["source"] for i in incidents.open_incidents(settings)}
    assert still_open == {"interieur"}


# ---------------------------------------------------------- review queue ---

def test_a_real_change_is_filed_for_a_person_to_read(settings):
    review.record_change(
        source_id="prefecture-herault", url=URL, old_version="v1",
        new_version="v2", change_type="critical", severity="high",
        summary="a required document changed",
        before_after="BEFORE: a\nAFTER: b", affected_topics=["documents"],
        settings=settings)
    queue = review.listing("changes", review.OPEN, settings)
    assert len(queue) == 1
    assert queue[0]["severity"] == "high"
    assert queue[0]["review_status"] == review.OPEN
    assert queue[0]["before_after"]


def test_a_reviewed_item_leaves_the_open_queue_but_not_the_record(settings):
    review.record_change(source_id="x", url=URL, old_version="v1",
                         new_version="v2", change_type="critical",
                         severity="high", summary="s", settings=settings)
    identifier = review.listing("changes", None, settings)[0]["change_id"]
    assert review.set_status("changes", identifier, review.RESOLVED,
                             note="checked", settings=settings)
    assert not review.listing("changes", review.OPEN, settings)
    kept = review.listing("changes", None, settings)[0]
    assert kept["review_status"] == review.RESOLVED
    assert kept["note"] == "checked"


def test_a_conflict_is_filed_with_its_recommendation(settings):
    from app.sources.claims import Claim
    from app.sources.conflict import detect

    def claim(text, source, jurisdiction, level):
        return Claim(claim_id=text[:12], text=text, source_id=source,
                     version_id="v1", url="https://x", authority_level=level,
                     jurisdiction=jurisdiction)

    found = detect(
        [claim("Le passeport est requis pour la demande complete.", "nat", "national", 1)],
        [claim("Le passeport n est plus requis pour la demande complete.",
               "loc", "department", 1)])
    assert found
    review.record_conflict(found[0], settings=settings)
    queue = review.listing("conflicts", review.OPEN, settings)
    assert len(queue) == 1
    assert queue[0]["recommended_resolution"] == "loc"
    assert queue[0]["recommended_reason"]


def test_an_unknown_review_status_is_refused(settings):
    with pytest.raises(ValueError):
        review.set_status("changes", "CHANGE-x", "deleted", settings=settings)


# ------------------------------------------------------------- retention ---

def test_the_active_version_is_never_prunable(settings):
    from app.prune_history import plan_for
    from app.sources.registry import by_id

    source = by_id("prefecture-herault")
    for index in range(3):
        store.record(source.id, source.base_url,
                     make_page(source.base_url, f"Note interne numero {index}."),
                     f"h{index}", settings=settings)

    active = store.active(source.id, source.base_url, settings)
    prunable = {r["version"] for r in plan_for(source, settings)
                if r["action"] == "prune"}
    assert active.version_id not in prunable


def test_a_version_with_claims_under_review_is_kept(settings):
    from app.prune_history import plan_for
    from app.sources.registry import by_id

    source = by_id("prefecture-herault")
    version, _ = store.record(source.id, source.base_url,
                              make_page(source.base_url), "h1", settings=settings)
    assert version is not None
    stored = claim_store.load(source.id, version.version_id, settings)
    claim_store.mark_stale(source.id, version.version_id,
                           [stored[0].claim_id], settings)
    kept = [r for r in plan_for(source, settings) if r["action"] == "kept"]
    assert all(r["version"] != version.version_id
               for r in plan_for(source, settings) if r["action"] == "prune")


def test_retention_keeps_at_least_two_versions_for_rollback():
    settings = get_settings()
    assert settings.retention_versions >= 2
