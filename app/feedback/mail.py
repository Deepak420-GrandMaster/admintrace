"""Telling a maintainer a report came in.

Delivery is the last step and the least important one: the report is already
on disk before this runs. So every failure here is caught and returned, never
raised — a report that could not be emailed is not a report that was lost, and
the interface says which of the two happened rather than assuming.

Credentials come from the environment through ``app.config`` and are never
written to a report, a log line, or the body of the mail itself.
"""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from app.config import Settings, get_settings

#: What the subject line shouts, per severity.
_SEVERITY = {"critical": "CRITICAL", "high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def subject_for(record: dict) -> str:
    severity = _SEVERITY.get(str(record.get("severity", "")).lower(), "UNTRIAGED")
    title = (record.get("ai_summary") or record.get("title")
             or record.get("user_report") or "New report")
    title = " ".join(str(title).split())[:80]
    return f"[CLARÉ BUG][{severity}] {record.get('id', 'BUG-?')} — {title}"


def body_for(record: dict, local_path: str = "") -> str:
    """A plain-text body a maintainer can read in a notification.

    Only fields that came from the reporter or the triage step. No settings,
    no credentials, no environment beyond model names and thresholds.
    """
    def block(label: str, value) -> list[str]:
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            rendered = "\n".join(f"  {i}. {item}" for i, item in enumerate(value, 1))
        else:
            rendered = "\n".join(f"  {line}" for line in str(value).splitlines())
        return [label, rendered, ""]

    lines = [
        f"{record.get('id', 'BUG-?')}  ·  {record.get('created_at', '')}",
        f"severity: {record.get('severity') or 'untriaged'}"
        f"   category: {record.get('category') or 'untriaged'}"
        f"   interface language: {record.get('language') or '?'}",
        "",
    ]
    lines += block("SUMMARY", record.get("ai_summary"))
    lines += block("WHAT THEY WROTE", record.get("user_report"))
    lines += block("THEIR REPORT IN ENGLISH", record.get("translation"))
    lines += block("THEIR QUESTION AT THE TIME", record.get("user_question"))
    lines += block("LIKELY CAUSE", record.get("likely_cause"))
    lines += block("SUGGESTED FIX", record.get("suggested_fix"))
    lines += block("REPRODUCTION", record.get("reproduction_steps"))
    lines += block("AFFECTED FEATURE", record.get("affected_feature"))
    lines += block("SOURCES THE ANSWER USED", record.get("sources"))
    lines += block("BROWSER", record.get("browser"))
    lines += block("VIEWPORT", record.get("viewport"))
    lines += block("APP VERSION", record.get("app_version"))
    if record.get("triage_error"):
        lines += block("TRIAGE DID NOT RUN", record["triage_error"])
    if local_path:
        lines += block("ON DISK", local_path)
    return "\n".join(lines).rstrip() + "\n"


def send(record: dict, local_path: str = "",
         settings: Settings | None = None) -> tuple[bool, str]:
    """Deliver one report. Returns ``(sent, reason_if_not)``.

    Never raises: the caller has already written the report to disk and needs
    to tell the reporter the truth about delivery, not crash on it.
    """
    settings = settings or get_settings()
    if not settings.email_configured:
        return False, "no SMTP host or recipient is configured"

    message = EmailMessage()
    message["Subject"] = subject_for(record)
    message["To"] = settings.bug_email_to
    message["From"] = settings.bug_email_from or settings.bug_email_to
    message.set_content(body_for(record, local_path))

    try:
        if settings.smtp_port == 465:
            server = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port,
                                      timeout=15, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15)
        with server:
            if settings.smtp_port != 465 and settings.smtp_starttls:
                server.starttls(context=ssl.create_default_context())
            if settings.smtp_username:
                server.login(settings.smtp_username, settings.smtp_password)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 - delivery must never take the app down
        # The exception text can name the host and the account, never the
        # password: smtplib does not put it in the message.
        return False, f"{type(exc).__name__}: {exc}"
    return True, ""
