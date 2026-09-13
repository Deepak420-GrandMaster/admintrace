"""Turn results into the HTML the interface displays.

The markdown subset produced by the answer prompt is rendered here by hand
rather than with a library: it is six constructs, and rendering it ourselves is
what allows French vocabulary to be marked up inline and the suggested French
phrasing to be lifted into its own block.
"""

from __future__ import annotations

import html
import re

from app.answer.cite import Citation
from app.answer.generate import AnswerResult
from app.query import glossary
from app.retrieval.types import Retrieved

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_HEADING = re.compile(r"^#{2,4}\s+(.*)$")
# A line that is nothing but bold text is a heading in all but syntax.
_BOLD_HEADING = re.compile(r"^\*\*(.+?)\*\*:?\s*$")
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
_NUMBER = re.compile(r"^(\d+)[.)]\s+(.*)$")

# Headings that carry the copyable French phrasing, in either language.
_SAY_HEADINGS = ("say it in french", "le dire en français", "dites-le en français")


def _french_terms() -> list[str]:
    terms = sorted((t.fr for t in glossary.load()), key=len, reverse=True)
    return [t for t in terms if len(t) > 2]


def _mark_french(text: str) -> str:
    """Mark French administrative vocabulary so it stands out in the answer."""
    for term in _french_terms():
        pattern = re.compile(rf"(?<![\w>]){re.escape(html.escape(term))}(?![\w<])", re.I)
        if pattern.search(text):
            text = pattern.sub(
                lambda m: f'<span class="rp-fr">{m.group(0)}</span>', text, count=1
            )
    return text


def inline(text: str) -> str:
    escaped = html.escape(text.strip())
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC.sub(r"<em>\1</em>", escaped)
    return _mark_french(escaped)


def markdown(text: str) -> str:
    """Render the answer's markdown subset."""
    blocks: list[str] = []
    list_items: list[str] = []
    list_tag = ""
    in_say = False

    def flush_list() -> None:
        nonlocal list_items, list_tag
        if list_items:
            blocks.append(f"<{list_tag}>" + "".join(list_items) + f"</{list_tag}>")
            list_items, list_tag = [], ""

    def close_say() -> None:
        nonlocal in_say
        if in_say:
            blocks.append("<p class='rp-say-note'>Suggested wording, not an official text.</p></div>")
            in_say = False

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush_list()
            continue

        heading = _HEADING.match(line) or _BOLD_HEADING.match(line)
        if heading:
            flush_list()
            close_say()
            title = heading.group(1).strip()
            blocks.append(f"<h2>{html.escape(title)}</h2>")
            if title.strip().lower().rstrip(":") in _SAY_HEADINGS:
                blocks.append("<div class='rp-say'>")
                in_say = True
            continue

        bullet = _BULLET.match(line)
        if bullet:
            if list_tag != "ul":
                flush_list()
                list_tag = "ul"
            list_items.append(f"<li>{inline(bullet.group(1))}</li>")
            continue

        numbered = _NUMBER.match(line)
        if numbered:
            if list_tag != "ol":
                flush_list()
                list_tag = "ol"
            list_items.append(f"<li>{inline(numbered.group(2))}</li>")
            continue

        flush_list()
        blocks.append(f"<p>{inline(line)}</p>")

    flush_list()
    close_say()
    return "".join(blocks)


# ------------------------------------------------------------------ answer --

def skeleton(status: str) -> str:
    return (
        "<div class='rp-skeleton'>"
        f"<div class='rp-sk-status'><span class='rp-spinner'></span>{html.escape(status)}</div>"
        "<div class='rp-sk-line' style='width:92%'></div>"
        "<div class='rp-sk-line' style='width:99%'></div>"
        "<div class='rp-sk-line' style='width:78%'></div>"
        "<div class='rp-sk-line' style='width:88%'></div>"
        "</div>"
    )


def answer_html(result: AnswerResult, streaming: bool = False) -> str:
    if result.error and not result.text:
        return f"<div class='rp-error'>{html.escape(result.error)}</div>"
    if not result.text:
        return skeleton("Reading the sources…")

    if result.refused:
        badge = "<span class='rp-badge'><span class='rp-dot'></span>Not covered by the sources</span>"
        shell = "rp-answer rp-refusal"
    else:
        count = len(result.citations)
        label = f"Grounded in {count} official source{'s' if count != 1 else ''}"
        badge = f"<span class='rp-badge'><span class='rp-dot'></span>{label}</span>"
        shell = "rp-answer"

    body = markdown(result.text)
    if streaming:
        body += "<span class='rp-caret'></span>"

    error = (
        f"<div class='rp-error' style='margin-top:14px'>{html.escape(result.error)}</div>"
        if result.error else ""
    )
    return f"<div class='{shell}'>{badge}{body}{error}</div>"


# ----------------------------------------------------------------- sources --

def sources_html(citations: list[Citation]) -> str:
    if not citations:
        return ""
    cards = []
    for index, citation in enumerate(citations):
        scope = (
            f"<span class='rp-scope'>{html.escape(citation.scope_label)}</span>"
            if citation.scope_label else ""
        )
        date_class = "" if citation.last_updated_is_plausible else " class='rp-date-suspect'"
        percent = max(4, min(100, round(citation.score * 100)))
        cards.append(
            f"<a class='rp-source' style='--i:{index}' href='{html.escape(citation.url)}'"
            f" target='_blank' rel='noopener noreferrer'>"
            f"<div class='rp-source-title'>{html.escape(citation.title_fr)}</div>"
            f"<div class='rp-source-meta'>"
            f"<span class='rp-source-id'>{html.escape(citation.fiche_id)}</span>"
            f"{scope}"
            f"<span{date_class}>{html.escape(citation.updated_label)}</span>"
            f"<span class='rp-score'>"
            f"<span class='rp-score-track'><span class='rp-score-fill' style='--w:{percent}%;--i:{index}'></span></span>"
            f"<span class='rp-score-value'>{citation.score:.2f}</span>"
            f"</span></div></a>"
        )
    return (
        "<div class='rp-sources-head'>Sources · service-public.gouv.fr</div>"
        + "".join(cards)
    )


# ------------------------------------------------------------------- debug --

def _chunk_row(hit: Retrieved, passed: bool) -> str:
    tags = "".join(
        f"<span class='rp-tag{' rp-tag-kw' if s == 'keyword' else ''}'>{s}</span>"
        for s in hit.sources
    )
    meta = hit.metadata
    situation = (
        f"<span class='rp-scope'>{html.escape(meta.get('situation_fr', ''))}</span>"
        if meta.get("situation_fr") else ""
    )
    snippet = html.escape(hit.text[:210].replace("\n", " ")) + "…"
    return (
        f"<div class='rp-chunk{'' if passed else ' rp-dropped'}'>"
        f"<div class='rp-chunk-head'>"
        f"<span class='rp-chunk-id'>{html.escape(hit.chunk_id)}</span>{tags}{situation}"
        f"<span class='rp-score-value' style='margin-left:auto'>"
        f"sim {hit.dense_score:.3f}{'' if hit.keyword_rank is None else f' · bm25 {hit.keyword_score:.1f}'}"
        f"</span></div>"
        f"<div class='rp-chunk-text'>{snippet}</div></div>"
    )


def debug_html(result: AnswerResult) -> str:
    if result.prepared is None or result.gate is None:
        return "<div class='rp-note'>Ask a question to see how it was answered.</div>"

    prepared, gate = result.prepared, result.gate
    rows = [
        ("Detected language", f"<code>{prepared.language}</code> "
                              f"(confidence {prepared.confidence:.0%})"),
        ("Original question", html.escape(prepared.original)),
        ("French search query", html.escape(prepared.search_query)
            + (" <em>(translation unavailable, searched as written)</em>"
               if prepared.translation_failed else "")),
        ("Glossary expansion", html.escape(prepared.expanded_query)),
        ("Glossary terms matched", ", ".join(html.escape(t) for t in prepared.glossary_terms) or "—"),
        ("Relevance threshold", f"<code>{gate.threshold:.2f}</code>"),
        ("Best similarity", f"<code>{gate.best_score:.3f}</code>"),
        ("Similarity gate",
         "<span class='rp-gate-fail'>REFUSED — nothing cleared the threshold</span>"
         if gate.refused_by == "similarity" else
         f"<span class='rp-gate-pass'>PASSED — {len(gate.passed) or len(gate.rejected)} "
         f"of {len(gate.all_candidates)} passages cleared it</span>"),
        ("Answerability check",
         ("<span class='rp-gate-fail'>REFUSED — passages are related but do not "
          "answer the question</span>" if gate.refused_by == "answerability" else
          html.escape(gate.answerability_verdict.splitlines()[0])
          if gate.answerability_verdict else "not run")),
        ("Outcome",
         f"<span class='rp-gate-fail'>REFUSAL ({gate.refused_by})</span>"
         if gate.should_refuse else
         f"<span class='rp-gate-pass'>ANSWERED from {len(gate.passed)} passages</span>"),
        ("Answered in", f"{result.elapsed:.1f}s"),
    ]
    grid = "".join(
        f"<div class='rp-debug-key'>{key}</div><div class='rp-debug-val'>{value}</div>"
        for key, value in rows
    )

    kept = "".join(_chunk_row(h, True) for h in gate.passed)
    dropped = "".join(_chunk_row(h, False) for h in gate.rejected)
    sections = f"<div class='rp-debug-grid'>{grid}</div>"
    if kept:
        sections += "<div class='rp-sources-head'>Passages used</div>" + kept
    if dropped:
        sections += ("<div class='rp-sources-head'>Dropped below threshold</div>"
                     + dropped)
    return f"<div class='rp-debug'>{sections}</div>"


# ---------------------------------------------------------------- glossary --

def glossary_html(search: str = "") -> str:
    needle = glossary._fold(search.strip())
    terms = [
        t for t in glossary.load()
        if not needle
        or needle in glossary._fold(t.fr)
        or needle in glossary._fold(t.en)
        or any(needle in glossary._fold(a) for a in t.aliases_en)
    ]
    if not terms:
        return f"<div class='rp-note'>No glossary term matches “{html.escape(search)}”.</div>"

    cards = []
    for index, term in enumerate(terms):
        provenance = (
            f"<span class='rp-prov rp-prov-official'>Official definition · "
            f"{html.escape(term.definition_id or '')}</span>"
            if term.is_official else
            "<span class='rp-prov rp-prov-authored'>Written for Repères</span>"
        )
        cards.append(
            f"<div class='rp-gloss' style='--i:{index}'>"
            f"<div class='rp-gloss-fr'>{html.escape(term.fr)}</div>"
            f"<div class='rp-gloss-en'>{html.escape(term.en)}</div>"
            f"<div class='rp-gloss-def'>{html.escape(term.explanation_en)}</div>"
            f"<div class='rp-gloss-def-fr'>{html.escape(term.explanation_fr)}</div>"
            f"{provenance}</div>"
        )
    return f"<div class='rp-gloss-grid'>{''.join(cards)}</div>"
