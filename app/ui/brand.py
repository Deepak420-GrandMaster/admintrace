"""The mark and the wordmark.

*Claré* is built on *clair* — clear — and the job is the whole name: French
administration, made clear. Half the people who need this do not read French,
and *clair* carries to English, Spanish, Italian and Portuguese speakers
without translation.

The wordmark carries the brand; the mark only has to survive beside it at
16px. So the mark says one thing and stops: two broken, faint rules — official
text as it is met — resolving into a single clean stroke, with the acute of
the É above it. Four paths. Inline SVG, so it takes its colour from the page
and costs no request.

No flags anywhere. A flag is a country, and neither of these languages belongs
to one; French speakers arriving from Dakar or Montréal are not served by
being handed a tricolore. A language is named in its own language instead.
"""

from __future__ import annotations


def mark(size: int = 28) -> str:
    """Broken official lines resolving into one clear one, under an acute."""
    return f"""
<svg class="rp-mark" width="{size}" height="{size}" viewBox="0 0 24 24"
     fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <g class="rp-mark-dense" stroke="currentColor" stroke-width="1.9"
     stroke-linecap="round" opacity=".38">
    <path d="M3 10h6.5M12.5 10h7"/>
    <path d="M3 14.4h4.5M10.5 14.4h4M17.5 14.4h2.5"/>
  </g>
  <path class="rp-mark-clear" d="M3 18.9h16.5" stroke="var(--accent)"
        stroke-width="2.6" stroke-linecap="round"/>
  <path class="rp-mark-acute" d="M15.6 5.4 18.4 2.6" stroke="var(--accent)"
        stroke-width="2.2" stroke-linecap="round"/>
</svg>
"""


def wordmark(size: int = 28) -> str:
    return f"""
<div class="rp-brand">
  {mark(size)}
  <span class="rp-wordmark">Clar<span class="rp-accent">é</span></span>
</div>
"""


GLOBE = """
<svg width="12" height="12" viewBox="0 0 16 16" fill="none" aria-hidden="true">
  <circle cx="8" cy="8" r="6.4" stroke="currentColor" stroke-width="1.4"/>
  <path d="M1.6 8h12.8M8 1.6c1.7 1.8 2.6 4 2.6 6.4S9.7 12.6 8 14.4
           C6.3 12.6 5.4 10.4 5.4 8S6.3 3.4 8 1.6Z"
        stroke="currentColor" stroke-width="1.25"/>
</svg>
"""

# The same four paths, flattened to one colour so it still reads at 16px.
FAVICON = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E"
    "%3Cg fill='none' stroke='%232743c4' stroke-linecap='round'%3E"
    "%3Cg opacity='.42' stroke-width='1.9'%3E"
    "%3Cpath d='M3 10h6.5M12.5 10h7'/%3E"
    "%3Cpath d='M3 14.4h4.5M10.5 14.4h4M17.5 14.4h2.5'/%3E%3C/g%3E"
    "%3Cpath stroke-width='2.6' d='M3 18.9h16.5'/%3E"
    "%3Cpath stroke-width='2.2' d='M15.6 5.4 18.4 2.6'/%3E"
    "%3C/g%3E%3C/svg%3E"
)
