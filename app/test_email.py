"""Check the bug-report mail path, and say plainly whether it actually works.

    uv run python -m app.test_email            # report configuration only
    uv run python -m app.test_email --send     # actually send one

The point of this command is that it cannot be read optimistically. Without
credentials it says delivery was not tested — it does not say the mail path is
fine because the code looks fine. With credentials it sends one real message
and prints what the server said.

Nothing here prints, logs or stores a password. The configuration report shows
whether a value is present, never the value.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from app.config import get_settings
from app.feedback import mail


def _describe(settings) -> list[tuple[str, str, bool]]:
    """(label, shown value, is set) — never the secret itself."""
    return [
        ("SMTP_HOST", settings.smtp_host or "(unset)", bool(settings.smtp_host)),
        ("SMTP_PORT", str(settings.smtp_port), bool(settings.smtp_port)),
        ("SMTP_USERNAME", settings.smtp_username or "(unset)", bool(settings.smtp_username)),
        ("SMTP_PASSWORD", "set" if settings.smtp_password else "(unset)",
         bool(settings.smtp_password)),
        ("SMTP_STARTTLS", str(settings.smtp_starttls), True),
        ("BUG_REPORT_EMAIL_TO", settings.bug_email_to or "(unset)",
         bool(settings.bug_email_to)),
        ("BUG_REPORT_EMAIL_FROM",
         settings.bug_email_from or "(falls back to TO)", True),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.test_email",
                                     description="Check, and optionally exercise, bug-report email.")
    parser.add_argument("--check-config", action="store_true",
                        help="report whether delivery is configured, and stop")
    parser.add_argument("--dry-run", action="store_true",
                        help="build the message and print it; send nothing")
    parser.add_argument("--send", action="store_true",
                        help="actually send one test message")
    args = parser.parse_args(argv)

    settings = get_settings()

    print("SMTP configuration")
    for label, shown, present in _describe(settings):
        print(f"  {'✓' if present else '·'} {label:<24} {shown}")
    print()

    if args.check_config:
        print(f"configured: {'yes' if settings.email_configured else 'no'}")
        return 0 if settings.email_configured else 1

    if not settings.email_configured:
        print("SMTP credentials are not configured; real email delivery was "
              "not tested.")
        print("Bug reports are still stored locally, and the interface says so "
              "rather than claiming an email was sent.")
        return 0

    print(f"Configured to notify {settings.bug_email_to} via "
          f"{settings.smtp_host}:{settings.smtp_port}.")
    if not args.send:
        print("Run again with --send to deliver one test message.")
        return 0

    stamp = datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
    record = {
        "id": "BUG-TEST-000",
        "created_at": stamp,
        "severity": "low",
        "category": "other",
        "language": "en",
        "user_report": "Test message from `python -m app.test_email`. "
                       "No action needed.",
        "ai_summary": "SMTP delivery test",
        "app_version": "0.1.0",
    }
    if args.dry_run:
        print("DRY RUN — nothing is sent.\n")
        print(f"Subject: {mail.subject_for(record)}")
        print(f"To:      {settings.bug_email_to}")
        print()
        print(mail.body_for(record, "(not stored: this is a delivery test)"))
        return 0

    sent, reason = mail.send(record, "(not stored: this is a delivery test)", settings)
    if sent:
        print(f"Sent. The server accepted a message addressed to "
              f"{settings.bug_email_to}.")
        return 0
    print(f"Not sent. {reason}", file=sys.stderr)
    print("The bug pipeline treats this the same way: the report is stored and "
          "the reader is told the notification did not go out.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
