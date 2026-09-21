"""A report has to survive everything that can go wrong around it.

The triage model can be unreachable, the mail server can refuse, the queue can
be moved around by hand. None of that may cost a reporter the words they
typed, and none of it may make the interface claim more than happened.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from app.config import get_settings
from app.feedback import mail, report as report_module, store
from app.llm import ProviderError


@pytest.fixture
def settings(tmp_path):
    """Real settings, pointed at a throwaway data directory."""
    return dataclasses.replace(get_settings(), data_dir=tmp_path)


@pytest.fixture
def no_triage(monkeypatch):
    """The triage model is unreachable."""
    def unreachable(*args, **kwargs):
        raise ProviderError("connection refused")
    monkeypatch.setattr(report_module, "get_chat_provider", unreachable)


@pytest.fixture
def fake_triage(monkeypatch):
    """The triage model answers with a well-formed analysis."""
    class Provider:
        def complete(self, *args, **kwargs):
            return json.dumps({
                "title": "Answer cited an unrelated page",
                "translation": "The answer pointed at the wrong page.",
                "category": "retrieval",
                "severity": "high",
                "expected": "a page about renewing a permit",
                "actual": "a page about something else",
                "likely_cause": "the query matched on topic, not on intent",
                "suggested_fix": "check the relevance gate for this question",
                "reproduction_steps": ["ask about a renewal", "read the source"],
                "affected_feature": "sources",
                "confidence": "medium",
                "needs_more_info": False,
                "notes": "",
            })
    monkeypatch.setattr(report_module, "get_chat_provider", lambda *a, **k: Provider())


# ------------------------------------------------------------ identity ----

def test_a_report_gets_a_readable_id_and_its_own_directory(settings, fake_triage):
    record, directory = report_module.submit("it showed the wrong page",
                                             settings=settings)
    assert record.bug_id.startswith("BUG-")
    assert directory.name == record.bug_id
    assert directory.parent.name == "open"
    assert (directory / "report.json").is_file()
    assert (directory / "report.md").is_file()


def test_two_reports_on_the_same_day_do_not_collide(settings, fake_triage):
    first, _ = report_module.submit("one", settings=settings)
    second, _ = report_module.submit("two", settings=settings)
    assert first.bug_id != second.bug_id


# ----------------------------------------------------------- surviving ----

def test_the_raw_words_are_kept_when_triage_cannot_run(settings, no_triage):
    """Losing someone's report because a rate limit was hit is the worse bug."""
    record, directory = report_module.submit("la réponse était fausse",
                                             reporter_language="fr",
                                             settings=settings)
    assert not record.is_triaged
    assert record.triage_error
    stored = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert stored["user_report"] == "la réponse était fausse"
    assert stored["severity"] == "untriaged"


def test_triage_fills_the_analysis(settings, fake_triage):
    record, _ = report_module.submit("wrong page", settings=settings)
    assert record.is_triaged
    assert record.severity == "high"
    assert record.category == "retrieval"
    assert record.reproduction_steps
    assert record.suggested_fix


def test_the_context_the_page_captured_is_stored(settings, fake_triage):
    _, directory = report_module.submit(
        "wrong page", browser="Chrome on macOS", viewport="390x844",
        question="How do I renew my permit?", sources=["F2231"],
        settings=settings)
    stored = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert stored["browser"] == "Chrome on macOS"
    assert stored["viewport"] == "390x844"
    assert stored["user_question"] == "How do I renew my permit?"
    assert stored["sources"] == ["F2231"]
    assert stored["app_version"]


def test_no_secret_ever_reaches_a_stored_report(settings, fake_triage):
    _, directory = report_module.submit("wrong page", settings=settings)
    blob = (directory / "report.json").read_text(encoding="utf-8").lower()
    for secret in ("api_key", "gsk_", "password", "smtp_password", "token",
                   "cookie", "authorization"):
        assert secret not in blob, f"{secret} leaked into a bug report"


# --------------------------------------------------------------- email ----

def test_nothing_is_emailed_when_no_mail_is_configured(settings, fake_triage):
    record, _ = report_module.submit("wrong page", settings=settings)
    assert record.emailed is False
    assert record.email_error == ""


def test_a_failed_delivery_still_leaves_the_report_on_disk(settings, fake_triage,
                                                           monkeypatch):
    """Email is a notification. The record is the record."""
    configured = dataclasses.replace(
        settings, smtp_host="smtp.invalid", bug_email_to="dev@example.org")
    monkeypatch.setattr(mail, "send", lambda *a, **k: (False, "SMTPConnectError: no route"))

    record, directory = report_module.submit("wrong page", settings=configured)
    assert record.emailed is False
    assert "SMTPConnectError" in record.email_error
    stored = json.loads((directory / "report.json").read_text(encoding="utf-8"))
    assert stored["user_report"] == "wrong page"
    assert stored["emailed"] is False
    assert stored["email_error"]


def test_send_reports_failure_rather_than_raising(settings):
    """A mail server that is down must not take the page down with it."""
    configured = dataclasses.replace(
        settings, smtp_host="127.0.0.1", smtp_port=1, bug_email_to="dev@example.org")
    sent, reason = mail.send({"id": "BUG-1", "severity": "low"}, "", configured)
    assert sent is False and reason


def test_the_subject_line_leads_with_severity_and_id():
    subject = mail.subject_for({"id": "BUG-20260914-001", "severity": "high",
                                "ai_summary": "Wrong source returned"})
    assert subject.startswith("[ADMINTRACE BUG][HIGH] BUG-20260914-001")
    assert "Wrong source returned" in subject


def test_an_untriaged_report_still_gets_a_usable_subject():
    subject = mail.subject_for({"id": "BUG-20260914-002", "user_report": "broken"})
    assert "UNTRIAGED" in subject


def test_the_email_body_carries_no_configuration():
    body = mail.body_for({"id": "BUG-1", "user_report": "x", "severity": "low"})
    for secret in ("smtp", "password", "api_key", "gsk_"):
        assert secret not in body.lower()


# --------------------------------------------------------------- queue ----

def test_a_report_moves_through_the_queue(settings, fake_triage):
    record, _ = report_module.submit("wrong page", settings=settings)
    bug = record.bug_id

    store.set_status(bug, "in_progress", settings)
    assert store.find(bug, settings)[0] == "in_progress"

    store.set_status(bug, "resolved", settings, resolution="gate threshold raised")
    assert store.read(bug, settings)["status"] == "resolved"
    assert store.read(bug, settings)["resolution"] == "gate threshold raised"

    assert store.archive_resolved(settings) == [bug]
    assert store.find(bug, settings)[0] == "archived"


def test_listing_can_be_narrowed_to_one_status(settings, fake_triage):
    first, _ = report_module.submit("one", settings=settings)
    second, _ = report_module.submit("two", settings=settings)
    store.set_status(second.bug_id, "resolved", settings)

    open_ids = [r["id"] for r in store.listing("open", settings)]
    assert open_ids == [first.bug_id]
    assert [r["id"] for r in store.listing("resolved", settings)] == [second.bug_id]
    assert len(store.listing(None, settings)) == 2


def test_an_unknown_status_is_refused(settings):
    with pytest.raises(store.UnknownStatus):
        store.status_dir("deleted", settings)


def test_moving_a_report_that_does_not_exist_fails_loudly(settings):
    with pytest.raises(FileNotFoundError):
        store.set_status("BUG-20260101-001", "resolved", settings)
