"""Bug reports, written in whatever language the person has.

Someone who has just been handed a wrong answer about their visa should not
also have to compose a clear bug report in English. They write a sentence in
their own language; the model turns it into something a maintainer can act on,
and the raw words are kept alongside it so nothing is lost in the retelling.

This is the one place the system stores what a person typed. It only ever runs
because they pressed a button that says so, and the interface says plainly
what is kept — a privacy promise with a quiet exception in it is not a
privacy promise.
"""

from __future__ import annotations

import json
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.config import Settings, get_settings
from app.feedback import mail, store
from app.llm import ChatMessage, ProviderError, get_chat_provider

APP_VERSION = "0.1.0"

TRIAGE_SYSTEM = """\
You turn a bug report into something a maintainer can act on. You analyse and
recommend. You never modify anything, and you never answer the reporter.

The reporter may write in any language and may not be technical. Take them
seriously and read generously: they are describing something real that
happened to them.

Reply as JSON with exactly these keys, and nothing else:
  "title"        a one-line summary in English, under 80 characters
  "translation"  their report in English, faithful, nothing added or softened
  "category"     one of: ui, ux, retrieval, source, conversation, translation,
                 accessibility, performance, runtime, security, other
  "severity"     one of: critical, high, medium, low. Use critical or high only
                 where someone could act on wrong information about their legal
                 status, their money or a deadline, or where the system is
                 unusable.
  "expected"     what they expected to happen, in English
  "actual"       what actually happened, in English
  "likely_cause" your best reading of what is going wrong underneath, or
                 "unclear" if the report does not support a guess
  "suggested_fix" what a maintainer should look at first. A recommendation,
                 never an instruction to change something automatically.
  "reproduction_steps" a JSON array of short steps, [] if they cannot be
                 inferred from what was written
  "affected_feature" the part of the product involved: asking, answering,
                 sources, glossary, language, reporting, or other
  "confidence"   one of: high, medium, low — how far this analysis is
                 supported by what they actually wrote
  "needs_more_info" true if a maintainer would have to go back to the reporter
  "notes"        anything else a maintainer should know, or "" if nothing

Invent nothing. If they did not say what they expected, write "not stated".
"""


@dataclass
class Report:
    """One report, raw words first."""

    reported_at: str
    raw_text: str
    reporter_language: str
    question: str = ""
    answer_language: str = ""
    refused: bool | None = None
    sources: list[str] = field(default_factory=list)
    institution: str = ""
    # Filled by the model when it is reachable.
    title: str = ""
    translation: str = ""
    category: str = ""
    severity: str = ""
    expected: str = ""
    actual: str = ""
    likely_cause: str = ""
    suggested_fix: str = ""
    reproduction_steps: list[str] = field(default_factory=list)
    affected_feature: str = ""
    confidence: str = ""
    needs_more_info: bool = False
    notes: str = ""
    triage_error: str = ""
    app: dict = field(default_factory=dict)
    # Filled in by submit(): identity, where it went, and whether anyone was
    # told about it.
    bug_id: str = ""
    status: str = "open"
    directory: str = ""
    context_summary: str = ""
    what_doing: str = ""
    browser: str = ""
    viewport: str = ""
    emailed: bool = False
    email_error: str = ""

    @property
    def is_triaged(self) -> bool:
        return bool(self.title) and not self.triage_error


def _environment(settings: Settings) -> dict:
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "feed_version": settings.feed_version,
        "embed_model": settings.embed_model,
        "chat_model": settings.groq_model or settings.ollama_chat_model,
        "provider": settings.llm_provider,
        "retrieval_k": settings.retrieval_k,
        "relevance_threshold": settings.relevance_threshold,
    }


def triage(report: Report, settings: Settings | None = None) -> Report:
    """Translate and structure the report. Never loses the original."""
    settings = settings or get_settings()
    payload = (
        f"Reported in: {report.reporter_language or 'unknown'}\n"
        f"Their question at the time: {report.question or '(none)'}\n"
        f"Did the system refuse to answer: {report.refused}\n"
        f"Sources it used: {', '.join(report.sources) or '(none)'}\n\n"
        f"Their report:\n{report.raw_text}"
    )
    try:
        reply = get_chat_provider(settings).complete(
            [ChatMessage("system", TRIAGE_SYSTEM), ChatMessage("user", payload)],
            temperature=0.0, max_tokens=900,
        )
    except ProviderError as exc:
        # A report that cannot be triaged is still a report. Losing someone's
        # words because a rate limit was hit would be the worse bug.
        report.triage_error = str(exc)
        return report

    text = reply.strip()
    if text.startswith("```"):
        text = text.split("```")[1].removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        report.triage_error = "the model did not return readable JSON"
        report.notes = text[:500]
        return report

    for key in ("title", "translation", "category", "severity", "expected",
                "actual", "likely_cause", "suggested_fix", "affected_feature",
                "confidence", "notes"):
        value = parsed.get(key)
        if isinstance(value, str):
            setattr(report, key, value.strip())

    steps = parsed.get("reproduction_steps")
    if isinstance(steps, list):
        report.reproduction_steps = [str(step).strip() for step in steps
                                     if str(step).strip()]
    report.needs_more_info = bool(parsed.get("needs_more_info"))
    return report


def as_record(report: Report) -> dict:
    """The report as it is written to disk and read by a maintainer.

    Deliberately flat and deliberately boring: this file is the thing that
    survives, and it has to be readable by someone who has never seen this
    module. Nothing secret reaches it — see the note at the top of store.py.
    """
    return {
        "id": report.bug_id,
        "created_at": report.reported_at,
        "status": report.status,
        "severity": report.severity or "untriaged",
        "category": report.category or "untriaged",
        "language": report.reporter_language,
        "user_report": report.raw_text,
        "what_doing": report.what_doing,
        "user_question": report.question,
        "answer_language": report.answer_language,
        "refused": report.refused,
        "institution": report.institution,
        "title": report.title,
        "translation": report.translation,
        "ai_summary": report.title,
        "ai_analysis": report.notes,
        "expected": report.expected,
        "actual": report.actual,
        "likely_cause": report.likely_cause,
        "suggested_fix": report.suggested_fix,
        "reproduction_steps": report.reproduction_steps,
        "affected_feature": report.affected_feature,
        "confidence": report.confidence,
        "needs_more_info": report.needs_more_info,
        "triage_error": report.triage_error,
        "browser": report.browser,
        "viewport": report.viewport,
        "app_version": APP_VERSION,
        "conversation_context": report.context_summary,
        "sources": report.sources,
        "environment": report.app,
        "emailed": report.emailed,
        "email_error": report.email_error,
        "resolution": None,
    }


def save_report(report: Report, settings: Settings | None = None) -> Path:
    """Write the report into its own directory under ``data/bugs/open``.

    Both files are written: ``report.json`` for anything that reads the queue,
    and ``report.md`` for a person opening the folder. A jsonl line is still
    appended to the old log so an existing pipeline over it keeps working.
    """
    settings = settings or get_settings()
    if not report.bug_id:
        report.bug_id, directory = store.claim_id(settings)
    else:
        located = store.find(report.bug_id, settings)
        directory = (located[1] if located
                     else store.status_dir("open", settings) / report.bug_id)
        directory.mkdir(parents=True, exist_ok=True)
    report.directory = str(directory)

    store.write(as_record(report), directory)
    (directory / "report.md").write_text(_as_markdown(report), encoding="utf-8")

    legacy = settings.data_dir / "reports"
    legacy.mkdir(parents=True, exist_ok=True)
    with (legacy / "reports.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(asdict(report), ensure_ascii=False) + "\n")
    return directory


def _as_markdown(report: Report) -> str:
    lines = [
        f"# {report.title or 'Untriaged report'}",
        "",
        f"- **Reported** {report.reported_at}",
        f"- **Category** {report.category or '—'}   "
        f"**Severity** {report.severity or '—'}",
        f"- **Written in** {report.reporter_language or 'unknown'}",
    ]
    if report.institution:
        lines.append(f"- **Institution** {report.institution}")
    if report.question:
        lines.append(f"- **Question at the time** {report.question}")
    if report.refused is not None:
        lines.append(f"- **System refused** {report.refused}")
    if report.sources:
        lines.append(f"- **Sources used** {', '.join(report.sources)}")
    if report.triage_error:
        lines.append(f"- **Triage failed** {report.triage_error}")

    lines += ["", "## What they expected", report.expected or "not stated",
              "", "## What happened", report.actual or "not stated"]
    if report.translation:
        lines += ["", "## Their report, in English", report.translation]
    lines += ["", "## Their own words, exactly as written", "",
              "```", report.raw_text.strip(), "```"]
    if report.notes:
        lines += ["", "## Notes", report.notes]
    lines += ["", "## Environment", "",
              "```json", json.dumps(report.app, indent=2), "```", ""]
    return "\n".join(lines)


def submit(raw_text: str, *, reporter_language: str = "", question: str = "",
           answer_language: str = "", refused: bool | None = None,
           sources: list[str] | None = None, institution: str = "",
           what_doing: str = "", browser: str = "", viewport: str = "",
           conversation_context: str = "",
           settings: Settings | None = None) -> tuple[Report, Path]:
    """Take a report, triage it, store it, and try to tell someone.

    The order matters and is the whole design: the report is on disk before
    delivery is attempted, and the delivery result is recorded on the report
    rather than assumed. A failed email leaves a stored report that says so.
    """
    settings = settings or get_settings()
    report = Report(
        reported_at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
        raw_text=raw_text.strip(),
        reporter_language=reporter_language,
        question=question,
        answer_language=answer_language,
        refused=refused,
        sources=sources or [],
        institution=institution,
        what_doing=what_doing.strip(),
        browser=browser,
        viewport=viewport,
        context_summary=conversation_context,
        app=_environment(settings),
    )
    report = triage(report, settings)
    directory = save_report(report, settings)

    if settings.email_configured:
        report.emailed, report.email_error = mail.send(
            as_record(report), str(directory), settings)
        # Re-write with the delivery outcome, so the stored report never
        # claims more than actually happened.
        store.write(as_record(report), directory)
    return report, directory
