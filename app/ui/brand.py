"""The mark and the wordmark.

*Repères* means the fixed points you navigate by — the landmarks you take a
bearing from when you do not yet know where you are. The mark is that idea
reduced to its minimum: a ring, and one needle finding its direction.

Drawn as inline SVG so it stays sharp at any size, inherits the page's colours,
and needs no network request.
"""

from __future__ import annotations


def mark(size: int = 34, animated: bool = True) -> str:
    """The Repères mark: a bearing taken from a fixed point."""
    spin = (
        '<animateTransform attributeName="transform" type="rotate" '
        'from="-18 24 24" to="0 24 24" dur="1.1s" '
        'calcMode="spline" keySplines="0.22 0.61 0.36 1" fill="freeze"/>'
        if animated else ""
    )
    return f"""
<svg class="rp-mark" width="{size}" height="{size}" viewBox="0 0 48 48"
     fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true">
  <circle cx="24" cy="24" r="20.5" stroke="currentColor" stroke-width="2.2"
          opacity=".18"/>
  <circle cx="24" cy="24" r="20.5" stroke="var(--accent)" stroke-width="2.2"
          stroke-linecap="round" stroke-dasharray="34 95"
          transform="rotate(-58 24 24)" class="rp-mark-arc"/>
  <g class="rp-mark-needle">{spin}
    <path d="M24 8.5 L30 27 L24 23.2 L18 27 Z" fill="var(--accent)"/>
    <path d="M24 23.2 L30 27 L24 39.5 L18 27 Z" fill="var(--accent)"
          opacity=".32"/>
  </g>
  <circle cx="24" cy="24" r="2.6" fill="var(--surface)" stroke="var(--accent)"
          stroke-width="2"/>
</svg>
"""


def wordmark(size: int = 40, animated: bool = True) -> str:
    return f"""
<div class="rp-brand">
  {mark(size, animated)}
  <span class="rp-wordmark">Rep<span class="rp-accent">è</span>res</span>
</div>
"""


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
    "%3Ccircle cx='24' cy='24' r='20.5' fill='none' stroke='%231f3fd8' "
    "stroke-width='3'/%3E%3Cpath d='M24 8.5 L30 27 L24 23.2 L18 27 Z' "
    "fill='%231f3fd8'/%3E%3C/svg%3E"
)
