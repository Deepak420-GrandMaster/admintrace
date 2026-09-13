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
from app.llm import ChatMessage, ProviderError, get_chat_provider

TRIAGE_SYSTEM = """\
You turn a bug report into something a maintainer can act on.

The reporter may write in any language and may not be technical. Take them
seriously and read generously: they are describing something real that
happened to them.

Reply as JSON with exactly these keys, and nothing else:
  "title"       a one-line summary in English, under 80 characters
  "translation" their report in English, faithful, nothing added or softened
  "category"    one of: wrong-answer, missing-answer, wrong-language,
                broken-link, slow, interface, privacy, other
  "severity"    one of: low, medium, high — high only if someone could act on
                wrong information about their legal status, money, or a deadline
  "expected"    what they expected to happen, in English
  "actual"      what actually happened, in English
  "notes"       anything a maintainer should know, or "" if nothing

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
    notes: str = ""
    triage_error: str = ""
    app: dict = field(default_factory=dict)

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

    for key in ("title", "translation", "category", "severity",
                "expected", "actual", "notes"):
        value = parsed.get(key)
        if isinstance(value, str):
            setattr(report, key, value.strip())
    return report


def save_report(report: Report, settings: Settings | None = None) -> Path:
    """Append to a log, and keep a readable file per report."""
    settings = settings or get_settings()
    directory = settings.data_dir / "reports"
    directory.mkdir(parents=True, exist_ok=True)

    with (directory / "reports.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(asdict(report), ensure_ascii=False) + "\n")

    stamp = report.reported_at.replace(":", "").replace("-", "")[:15]
    path = directory / f"{stamp}-{report.category or 'report'}.md"
    path.write_text(_as_markdown(report), encoding="utf-8")
    return path


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
           settings: Settings | None = None) -> tuple[Report, Path]:
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
        app=_environment(settings),
    )
    report = triage(report, settings)
    return report, save_report(report, settings)
