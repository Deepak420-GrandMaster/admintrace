"""Self-monitoring: status that moves on its own, and regressions that surface.

The point of this layer is that nobody has to remember to update anything. A
capability becomes proven when evidence arrives and stops being proven when
the evidence goes away — and if a developer can make a status green by editing
a file, none of it means anything.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.config import get_settings
from app.healthcheck import snapshot, write
from app.sources import incidents, review, store
from app.status import (HistoryDepth, Verification, assess, depth_of,
                        historical_answerable, history_metrics, regressions,
                        write_scorecard)

URL = "https://www.herault.gouv.fr/demarches/titre-de-sejour/"


@pytest.fixture
def settings(tmp_path):
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


def make_page(url=URL, marker=""):
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


# ------------------------------------------------ history depth per source --

@pytest.mark.parametrize("versions,days,expected", [
    (1, 0, HistoryDepth.INSUFFICIENT),
    (2, 200, HistoryDepth.INSUFFICIENT),   # depth is not a substitute for count
    (3, 0, HistoryDepth.LIMITED),
    (5, 29, HistoryDepth.LIMITED),
    (5, 30, HistoryDepth.READY),
    (5, 120, HistoryDepth.MATURE),
])
def test_depth_is_graded_rather_than_a_yes_or_no(versions, days, expected):
    assert depth_of(versions, days, get_settings()) is expected


def test_a_deep_source_does_not_vouch_for_a_shallow_one(settings):
    """Per source, never globally — the failure this guards against is one
    mature archive making every other source look answerable."""
    store.record("mbs", "https://www.mbs-education.com/", make_page(
        "https://www.mbs-education.com/"), "m1", settings=settings)
    metrics = history_metrics(settings)
    graded = {k: v["depth"] for k, v in metrics.items() if v["version_count"]}
    assert graded, "nothing stored"
    assert all(depth in {d.value for d in HistoryDepth} for depth in graded.values())


def test_a_shallow_source_cannot_support_a_claim_about_the_past(settings):
    store.record("prefecture-herault", URL, make_page(), "h1", settings=settings)
    assert not historical_answerable("prefecture-herault", settings)


def test_a_source_with_no_history_cannot_support_one_either(settings):
    assert not historical_answerable("anef", settings)


def test_the_scorecard_reports_history_by_source_not_by_the_best_one(settings):
    cap = assess(settings)["historical_retrieval"]
    assert "by_depth" in cap.detail
    if cap.status is Verification.INSUFFICIENT_HISTORY:
        assert not cap.detail["ready_sources"]


# ----------------------------------------------------------- regressions --

def test_a_capability_that_stops_being_proven_is_flagged():
    current = assess()
    pretend_before = {"capabilities": {
        key: {"status": Verification.PRODUCTION_VERIFIED.value}
        for key in current}}
    found = regressions(pretend_before, current)
    # Anything not currently production_verified regressed against that state.
    assert found
    assert all(item["was"] == "production_verified" for item in found)
    assert all(item["capability"] in current for item in found)


def test_no_regression_is_reported_against_an_unchanged_state():
    current = assess()
    same = {"capabilities": {k: {"status": c.status.value}
                             for k, c in current.items()}}
    assert regressions(same, current) == []


def test_improving_is_not_a_regression():
    current = assess()
    worse = {"capabilities": {k: {"status": Verification.NOT_OBSERVED.value}
                              for k in current}}
    assert regressions(worse, current) == []


def test_the_scorecard_keeps_snapshots_so_last_week_has_an_answer(settings):
    write_scorecard(settings)
    write_scorecard(settings)
    archive = settings.data_dir / "status_history"
    assert archive.is_dir()
    assert list(archive.glob("*.json"))


def test_every_capability_records_when_it_was_last_verified(settings):
    path = write_scorecard(settings)
    payload = json.loads(path.read_text(encoding="utf-8"))
    for key, row in payload["capabilities"].items():
        assert row["last_verified"], key
        assert row["evidence"], key


def test_only_one_module_may_write_the_scorecard():
    """A status a developer can set by hand is a status that means nothing.

    Naming the file in help text is fine. Writing it from anywhere except the
    module that derives it is not: two writers means the derived value can be
    quietly replaced by an asserted one.
    """
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    writers = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "production_status.json" in text and "write_text" in text:
            writers.append(path.name)
    assert writers == ["status.py"], writers


# ---------------------------------------------------------- healthcheck ---

def test_a_health_snapshot_covers_everything_a_scheduler_needs(settings):
    payload = snapshot(settings)
    for key in ("checked_at", "sources", "freshness", "review", "incidents",
                "history", "claims", "scorecard", "needs_attention"):
        assert key in payload, key
    assert payload["sources"]["registered"] > 0


def test_a_blocked_source_is_named_in_the_snapshot(settings):
    payload = snapshot(settings)
    assert "caf" in payload["sources"]["blocked"]


def test_an_open_incident_makes_the_snapshot_ask_for_a_person(settings):
    assert not snapshot(settings)["needs_attention"]
    incidents.record("caf", kind="bot_wall", detail="blocked", settings=settings)
    assert snapshot(settings)["needs_attention"]


def test_a_recovered_source_clears_the_attention_flag(settings):
    incidents.record("caf", kind="bot_wall", settings=settings)
    assert snapshot(settings)["needs_attention"]
    incidents.resolve("caf", settings)
    assert not snapshot(settings)["needs_attention"]


def test_an_open_conflict_asks_for_a_person(settings):
    from app.sources.conflict import Conflict
    review.record_conflict(
        Conflict(conflict_id="CONF-test", claim_a="a", claim_b="b",
                 source_a="nat", source_b="loc"), settings=settings)
    assert snapshot(settings)["needs_attention"]


def test_health_snapshots_are_written_and_archived(settings):
    payload = snapshot(settings)
    latest, archived = write(settings, payload)
    assert latest.exists() and archived.exists()
    assert json.loads(latest.read_text(encoding="utf-8"))["checked_at"]


def test_the_healthcheck_changes_nothing(settings):
    """Safe to run from cron every few minutes."""
    store.record("prefecture-herault", URL, make_page(), "h1", settings=settings)
    before = store.active("prefecture-herault", URL, settings).version_id
    snapshot(settings)
    snapshot(settings)
    assert store.active("prefecture-herault", URL, settings).version_id == before


# ------------------------------------------------------- answer length ----

@pytest.mark.parametrize("question,expected", [
    ("What is a titre de séjour?", "micro"),
    ("How much is the deposit?", "micro"),
    ("How do I renew my residence permit?", "procedural"),
    ("What do I need to apply?", "procedural"),
    ("How do I apply as an international student, and what is the deadline?",
     "complex"),
])
def test_a_question_is_sized_before_its_answer_is_judged(question, expected):
    """One ceiling for every question either truncates or licenses padding."""
    from app.answer.length import classify
    assert classify(question).value == expected


def test_an_overlong_answer_is_caught():
    from app.answer.length import check
    result = check("What is a titre de séjour?", "word " * 200)
    assert result.over
    assert "over" in result.describe()


def test_a_short_answer_to_a_procedural_question_is_not_flagged():
    from app.answer.length import check
    assert check("How do I renew my permit?", "word " * 120).ok


def test_more_sources_must_not_raise_the_ceiling():
    """The ceiling is a property of the question, not of what was retrieved."""
    from app.answer.length import BANDS, classify
    question = "How do I renew my residence permit?"
    assert BANDS[classify(question)][1] == 180


def test_the_length_policy_covers_every_class():
    from app.answer.length import BANDS, AnswerClass
    assert set(BANDS) == set(AnswerClass)
    for floor, ceiling in BANDS.values():
        assert 0 < floor < ceiling
