"""Check every registered source against the live web, and change nothing.

    uv run python -m app.live_source_check
    uv run python -m app.live_source_check --source mbs
    uv run python -m app.live_source_check --json
    uv run python -m app.live_source_check --write     # record health

This is the command that keeps the registry honest, and it is how every
surprise so far was found: that montpellier-bs.com had become
mbs-education.com, that caf.fr answers with a bot wall, that three state
services serve an empty shell until their scripts run.

It reports a *health state*, not a pass/fail, because the remedies differ. A
parser failure may be fixable by rendering. A block never is, and is not
something to route around. An unverified source has simply never been asked.

Static HTML is read first. Rendering is attempted only for sources that
declare ``render_enabled`` and only when the static read produced nothing —
never as a matter of routine.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

from app.config import get_settings
from app.sources import store
from app.sources.extract import extract
from app.sources.fetch import fetch
from app.sources.registry import REGISTRY_PATH, Health, load_registry

#: Hosts that mean "prove you are human". Being sent to one is a deliberate
#: refusal by the site, not a fault to work around.
_BOT_WALLS = ("perfdrive", "datadome", "imperva", "incapsula", "captcha",
              "hcaptcha", "recaptcha", "cloudflare", "akamai", "queue-it",
              "distil", "shieldsquare")

_REDIRECT_REFUSED = re.compile(r"refused: redirect to '([^']+)'")


def _classify_refusal(error: str) -> tuple[Health, str, str]:
    """Read a refusal, and say what kind of problem it actually is."""
    match = _REDIRECT_REFUSED.search(error or "")
    if not match:
        return Health.UNAVAILABLE, "", ""
    target = match.group(1)
    if any(wall in target.lower() for wall in _BOT_WALLS):
        return Health.BLOCKED, target, "bot protection"
    # An official domain that redirects somewhere unregistered may have moved.
    # It is a candidate, never a fact: a maintainer confirms it.
    return Health.UNAVAILABLE, target, "rebrand candidate"


def check_one(source, settings, *, allow_render: bool = True) -> dict:
    row = {
        "id": source.id, "name": source.name, "domain": source.domain,
        "requested": source.base_url, "status": 0, "canonical_url": "",
        "title": "", "words": 0, "language": "", "content_hash": "",
        "retrieved_at": "", "parser": "not run", "redirects": [],
        "health": Health.UNVERIFIED.value, "rendered": False,
        "redirect_target": "", "rebrand_candidate": "", "error": "",
    }

    result = fetch(source.base_url, settings=settings, expect=source)
    row["status"] = result.status
    row["retrieved_at"] = result.retrieved_at
    row["redirects"] = result.redirects
    row["content_hash"] = result.content_hash[:16]

    page = extract(result.body, result.final_url) if result.ok else None

    # Static HTML had nothing readable: this is what rendering is for.
    if allow_render and source.render_enabled and (page is None or not page.is_usable):
        from app.sources.render import available, render
        if available():
            rendered = render(source.base_url, source=source, settings=settings)
            if rendered.ok:
                candidate = extract(rendered.body, rendered.final_url)
                if candidate.is_usable:
                    result, page, row["rendered"] = rendered, candidate, True
                    row["status"] = rendered.status
                    row["content_hash"] = rendered.content_hash[:16]
            elif not result.ok:
                result.error = result.error or rendered.error

    if not result.ok and page is None:
        health, target, kind = _classify_refusal(result.error)
        row["health"] = health.value
        row["error"] = result.error or f"http {result.status}"
        row["redirect_target"] = target
        if kind == "rebrand candidate":
            row["rebrand_candidate"] = target
            store.audit("source.domain_change", settings, source=source.id,
                        severity="high", old_domain=source.domain,
                        new_domain=urlsplit(f"//{target}").hostname or target,
                        status=result.status, redirect_chain=result.redirects,
                        manual_verification="required")
        elif health is Health.BLOCKED:
            store.audit("source.blocked", settings, source=source.id,
                        redirected_to=target, note="bot protection; not bypassed")
        return row

    if page is None or not page.is_usable:
        row["health"] = Health.PARSER_FAILURE.value
        row["parser"] = "no usable content"
        row["error"] = ("page fetched but parsed to nothing readable"
                        + (" even after rendering" if row["rendered"] else ""))
        return row

    row["canonical_url"] = page.canonical_url
    row["title"] = page.title
    row["words"] = page.word_count
    row["language"] = page.language
    row["parser"] = "ok (rendered)" if row["rendered"] else "ok"
    row["health"] = (Health.HEALTHY_RENDERED if row["rendered"]
                     else Health.HEALTHY).value
    return row


def _write_health(rows: list[dict]) -> int:
    """Record health, verified and verified_at in the registry, in place."""
    text = REGISTRY_PATH.read_text(encoding="utf-8")
    today = datetime.now(tz=timezone.utc).date().isoformat()
    written = 0
    good = {Health.HEALTHY.value, Health.HEALTHY_RENDERED.value}

    for row in rows:
        marker = f"  - id: {row['id']}\n"
        start = text.find(marker)
        if start == -1:
            continue
        end = text.find("\n  - id:", start + 1)
        end = len(text) if end == -1 else end
        block = original = text[start:end]

        healthy = row["health"] in good
        block = re.sub(r"    health: \S+", f"    health: {row['health']}", block)
        block = re.sub(r"    verified: (?:true|false)",
                       f"    verified: {'true' if healthy else 'false'}", block)
        if healthy:
            if "verified_at:" in block:
                block = re.sub(r"    verified_at: .*", f"    verified_at: {today}", block)
            else:
                block = block.replace("    verified: true",
                                      f"    verified: true\n    verified_at: {today}")
        if block != original:
            text = text[:start] + block + text[end:]
            written += 1
    if written:
        REGISTRY_PATH.write_text(text, encoding="utf-8")
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.live_source_check",
                                     description="Check registered sources against the live web.")
    parser.add_argument("--source", help="check one source by id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--write", action="store_true",
                        help="record health and verification in the registry")
    parser.add_argument("--no-render", action="store_true",
                        help="static HTML only, even for render-enabled sources")
    args = parser.parse_args(argv)

    settings = get_settings()
    sources = load_registry()
    if args.source:
        sources = tuple(s for s in sources if s.id == args.source)
        if not sources:
            print(f"no source with id {args.source!r}", file=sys.stderr)
            return 1

    rows = [check_one(s, settings, allow_render=not args.no_render) for s in sources]

    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(f"{'ID':<16} {'HEALTH':<18} {'WORDS':<7} {'PARSER':<16} CANONICAL / ERROR")
        for row in rows:
            print(f"{row['id']:<16} {row['health']:<18} {row['words']:<7} "
                  f"{row['parser'][:16]:<16} "
                  f"{(row['canonical_url'] or row['error'])[:58]}")
            if row["redirects"]:
                print(f"{'':<16} redirected → {row['redirects'][-1][:70]}")
            if row["rebrand_candidate"]:
                print(f"{'':<16} ⚠ REBRAND CANDIDATE → {row['rebrand_candidate']} "
                      f"(needs manual verification)")

        counts: dict[str, int] = {}
        for row in rows:
            counts[row["health"]] = counts.get(row["health"], 0) + 1
        print()
        print(f"checked {len(rows)} · "
              + " · ".join(f"{state} {n}" for state, n in sorted(counts.items())))

    if args.write:
        print(f"registry updated for {_write_health(rows)} source(s)")

    usable = sum(1 for r in rows
                 if r["health"] in (Health.HEALTHY.value, Health.HEALTHY_RENDERED.value))
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
