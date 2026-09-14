"""The mark and the wordmark.

*En clair* is the French for putting something plainly — decoding it, saying
it in the open. It is what this does and the only thing it does: official
administrative French, in the clear. Half the people who need it do not read
French, and *clair* carries to English, Spanish, Italian and Portuguese
speakers without translation.

The mark is the job: dense, unreadable official text on the left, resolving
into three clean lines on the right. Inline SVG, so it stays sharp at any
size, takes its colour from the page, and costs no request.
"""

from __future__ import annotations


def mark(size: int = 38, animated: bool = True) -> str:
    """Officialese on the left, resolving into plain lines on the right."""
    _ = animated
    return f"""
<svg class="rp-mark" width="{size}" height="{size}" viewBox="0 0 48 48"
     fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <g class="rp-mark-walls" stroke="currentColor" stroke-width="2.2"
     stroke-linecap="round" opacity=".26">
    <path d="M6 12h13M6 17.5h10M6 23h13M6 28.5h8M6 34h12M6 39.5h9"/>
  </g>
  <g class="rp-mark-clear" stroke="var(--accent)" stroke-width="3.2"
     stroke-linecap="round">
    <path d="M28 16h14M28 24h14M28 32h9"/>
  </g>
  <path class="rp-mark-arc" d="M22 8v32" stroke="var(--accent)"
        stroke-width="2.2" stroke-linecap="round" opacity=".45"/>
</svg>
"""


def wordmark(size: int = 38, animated: bool = True) -> str:
    return f"""
<div class="rp-brand">
  {mark(size, animated)}
  <span class="rp-wordmark">En&nbsp;Cl<span class="rp-accent">ai</span>r</span>
</div>
"""


FLAGS = {
    # Drawn as roundels rather than rectangles so they sit as icons beside a
    # label instead of competing with it.
    "en": """
<svg class="rp-flag" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
  <defs><clipPath id="rpFlagEn"><circle cx="12" cy="12" r="11"/></clipPath></defs>
  <g clip-path="url(#rpFlagEn)">
    <rect width="24" height="24" fill="#0b2d6b"/>
    <path d="M0 0 L24 24 M24 0 L0 24" stroke="#fff" stroke-width="5"/>
    <path d="M0 0 L24 24 M24 0 L0 24" stroke="#c8102e" stroke-width="2.6"/>
    <path d="M12 0 V24 M0 12 H24" stroke="#fff" stroke-width="7.5"/>
    <path d="M12 0 V24 M0 12 H24" stroke="#c8102e" stroke-width="4.4"/>
  </g>
  <circle cx="12" cy="12" r="11" fill="none" stroke="rgba(0,0,0,.16)"/>
</svg>
""",
    "fr": """
<svg class="rp-flag" viewBox="0 0 24 24" width="17" height="17" aria-hidden="true">
  <defs><clipPath id="rpFlagFr"><circle cx="12" cy="12" r="11"/></clipPath></defs>
  <g clip-path="url(#rpFlagFr)">
    <rect width="8" height="24" fill="#0b2d6b"/>
    <rect x="8" width="8" height="24" fill="#fff"/>
    <rect x="16" width="8" height="24" fill="#c8102e"/>
  </g>
  <circle cx="12" cy="12" r="11" fill="none" stroke="rgba(0,0,0,.16)"/>
</svg>
""",
}


def flag(code: str) -> str:
    return FLAGS.get(code, FLAGS["en"])


GLOBE = """
<svg width="13" height="13" viewBox="0 0 16 16" fill="none" aria-hidden="true">
  <circle cx="8" cy="8" r="6.4" stroke="currentColor" stroke-width="1.4"/>
  <path d="M1.6 8h12.8M8 1.6c1.7 1.8 2.6 4 2.6 6.4S9.7 12.6 8 14.4
           C6.3 12.6 5.4 10.4 5.4 8S6.3 3.4 8 1.6Z"
        stroke="currentColor" stroke-width="1.25"/>
</svg>
"""

FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 48 48'%3E"
    "%3Cg fill='none' stroke='%232743c4' stroke-linecap='round'%3E"
    "%3Cg opacity='.35' stroke-width='3'%3E"
    "%3Cpath d='M6 14h12M6 24h9M6 34h12'/%3E%3C/g%3E"
    "%3Cg stroke-width='5'%3E%3Cpath d='M28 14h14M28 24h14M28 34h9'/%3E"
    "%3C/g%3E%3C/g%3E%3C/svg%3E"
)
