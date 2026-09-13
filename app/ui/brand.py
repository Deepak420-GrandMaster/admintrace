"""The mark and the wordmark.

*Sésame* is the word for the thing that opens the door. In French it is the
everyday, slightly wry name for whichever document you are missing — *le
sésame pour travailler*, *le sésame pour la CAF* — and in English nobody needs
the phrase explained. It names the problem rather than a metaphor only one
half of the audience will catch.

The mark is a key, drawn down to the few strokes that still read as one at
sixteen pixels. Inline SVG, so it stays sharp at any size, takes its colour
from the page, and costs no request.
"""

from __future__ import annotations


def mark(size: int = 40, animated: bool = True) -> str:
    """The Sésame mark: a key, and the arc of it turning."""
    _ = animated
    return f"""
<svg class="rp-mark" width="{size}" height="{size}" viewBox="0 0 48 48"
     fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <circle cx="24" cy="24" r="21" stroke="currentColor" stroke-width="2"
          opacity=".14"/>
  <circle cx="24" cy="24" r="21" stroke="var(--accent)" stroke-width="2"
          stroke-linecap="round" stroke-dasharray="30 102"
          transform="rotate(-62 24 24)" class="rp-mark-arc"/>
  <g class="rp-mark-key">
    <circle cx="19.5" cy="19.5" r="6.4" stroke="var(--accent)"
            stroke-width="3.1"/>
    <path d="M23.9 24.1 L34 34.2" stroke="var(--accent)" stroke-width="3.1"
          stroke-linecap="round"/>
    <path d="M30.2 30.5 L27.2 33.5" stroke="var(--accent)" stroke-width="3.1"
          stroke-linecap="round"/>
    <path d="M33.4 33.7 L30.8 36.3" stroke="var(--accent)" stroke-width="3.1"
          stroke-linecap="round"/>
  </g>
</svg>
"""


def wordmark(size: int = 40, animated: bool = True) -> str:
    return f"""
<div class="rp-brand">
  {mark(size, animated)}
  <span class="rp-wordmark">S<span class="rp-accent">é</span>same</span>
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
    "%3Cg fill='none' stroke='%231f3fd8' stroke-width='4.6' "
    "stroke-linecap='round'%3E"
    "%3Ccircle cx='19.5' cy='19.5' r='8'/%3E"
    "%3Cpath d='M24.8 24.8 L36 36'/%3E"
    "%3Cpath d='M31 31 L27.5 34.5'/%3E%3C/g%3E%3C/svg%3E"
)
