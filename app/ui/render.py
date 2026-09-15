"""Turn results into the HTML the interface displays.

The markdown subset produced by the answer prompt is rendered here by hand
rather than with a library: it is six constructs, and rendering it ourselves is
what allows French vocabulary to be marked up inline and the suggested French
phrasing to be lifted into its own block.
"""

from __future__ import annotations

import html
import re
import urllib.parse

from app.answer.cite import Citation, ServiceLink
from app.answer.generate import AnswerResult
from app.query import glossary
from app.retrieval.types import Retrieved
from app.ui import brand
from app.ui.i18n import t

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_HEADING = re.compile(r"^#{2,4}\s+(.*)$")
# A line that is nothing but bold text is a heading in all but syntax.
_BOLD_HEADING = re.compile(r"^\*\*(.+?)\*\*:?\s*$")
_BULLET = re.compile(r"^[-*•]\s+(.*)$")
_NUMBER = re.compile(r"^(\d+)[.)]\s+(.*)$")

# Headings that carry the copyable French phrasing, in either language.
_SAY_HEADINGS = ("say it in french", "le dire en français", "dites-le en français")


def _french_terms():
    terms = sorted(glossary.load(), key=lambda t: len(t.fr), reverse=True)
    return [t for t in terms if len(t.fr) > 2]


def _mark_french(text: str, lang: str = "en") -> str:
    """Mark French vocabulary, and carry its definition for a tooltip.

    Knowing the word is only half of it: the reader also needs to know what it
    means, and sending them to another tab to find out is how you lose them
    mid-procedure. The definition travels with the word.
    """
    for term in _french_terms():
        pattern = re.compile(
            rf"(?<![\w>]){re.escape(html.escape(term.fr))}(?![\w<])", re.I
        )
        if not pattern.search(text):
            continue
        gloss = term.explanation_fr if lang == "fr" else term.explanation_en
        source = ("service-public.gouv.fr" if term.is_official
                  else ("écrit pour Claré" if lang == "fr"
                        else "written for Claré"))
        attrs = (f'class="rp-fr" tabindex="0" '
                 f'data-en="{html.escape(term.en, quote=True)}" '
                 f'data-gloss="{html.escape(gloss, quote=True)}" '
                 f'data-src="{html.escape(source, quote=True)}"')
        text = pattern.sub(
            lambda m: f'<span {attrs}>{m.group(0)}</span>', text, count=1
        )
    return text


def inline(text: str, lang: str = "en") -> str:
    escaped = html.escape(text.strip())
    escaped = _BOLD.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC.sub(r"<em>\1</em>", escaped)
    return _mark_french(escaped, lang)


def markdown(text: str, lang: str = "en") -> str:
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
            blocks.append(
                f"<p class='rp-say-note'>{html.escape(t(lang, 'say_note'))}</p>"
                f"<button class='rp-copy' type='button' data-copy "
                f"data-done=\"{html.escape(t(lang, 'copied'))}\">"
                f"{html.escape(t(lang, 'copy'))}</button></div>"
            )
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
            list_items.append(f"<li>{inline(bullet.group(1), lang)}</li>")
            continue

        numbered = _NUMBER.match(line)
        if numbered:
            if list_tag != "ol":
                flush_list()
                list_tag = "ol"
            list_items.append(f"<li>{inline(numbered.group(2), lang)}</li>")
            continue

        flush_list()
        blocks.append(f"<p>{inline(line, lang)}</p>")

    flush_list()
    close_say()
    return "".join(blocks)


# ------------------------------------------------------------------ answer --

STAGES = ("stage_search", "stage_read", "stage_write")


# The route the thread takes. Declared once so the drawn line and the bead
# travelling along it cannot drift apart.
_MAZE_ROUTE = "M10 10 L10 34 L26 34 L26 18 L42 18 L42 42 L58 42 L58 10 L74 10 L74 50"


def skeleton(active: int = 0, lang: str = "en") -> str:
    """The wait, shown as the work.

    Waiting is the moment a worried person is most alone with the question, so
    rather than a spinner the page shows a thread finding its way out of a
    labyrinth — which is the thing being done, and the thing the name means.
    """
    rows = []
    for index, key in enumerate(STAGES):
        state = ("rp-done-stage" if index < active
                 else "rp-on" if index == active else "")
        rows.append(
            f"<div class='rp-stage {state}'><span class='rp-stage-dot'></span>"
            f"{html.escape(t(lang, key))}</div>"
        )
    maze = (
        "<svg class='rp-maze' width='84' height='60' viewBox='0 0 84 60' "
        "fill='none' aria-hidden='true'>"
        "<g class='rp-maze-walls'>"
        "<path d='M2 2h80v56H2z'/><path d='M18 2v26M34 58V32M50 2v26M66 58V26'/>"
        "</g>"
        f"<path class='rp-maze-thread' d='{_MAZE_ROUTE}'/>"
        "<circle class='rp-maze-head' cx='0' cy='0'/>"
        "</svg>"
    )
    return (f"<div class='rp-loader'>{maze}"
            f"<div class='rp-stages'>{''.join(rows)}</div></div>")


def limit_html(result: AnswerResult, lang: str = "en") -> str:
    """Running out of allowance is not a fault, and should not read like one.

    The stock message is a wall of quota arithmetic and a link to a billing
    page. Someone waiting on an answer about their visa needs to know three
    things: nothing is broken, they did nothing wrong, and when it is back.
    """
    wait = ""
    if result.retry_after:
        minutes = round(result.retry_after / 60)
        wait = (t(lang, "limit_wait", mins=minutes) if minutes >= 1
                else t(lang, "limit_wait_soon"))
    return (
        "<div class='rp-limit'>"
        "<div class='rp-limit-cup'>"
        "<svg width='30' height='30' viewBox='0 0 32 32' fill='none'>"
        "<path d='M8 11h14v10a6 6 0 0 1-6 6h-2a6 6 0 0 1-6-6V11Z' "
        "stroke='currentColor' stroke-width='1.9' stroke-linejoin='round'/>"
        "<path d='M22 14h3a3 3 0 0 1 0 6h-3' stroke='currentColor' "
        "stroke-width='1.9' stroke-linejoin='round'/>"
        "<path d='M12 4.5c-1 1.4-1 2.6 0 4M17 4c-1 1.4-1 2.6 0 4' "
        "stroke='currentColor' stroke-width='1.7' stroke-linecap='round'/>"
        "</svg></div>"
        f"<div><div class='rp-limit-title'>{html.escape(t(lang, 'limit_title'))}</div>"
        f"<p class='rp-limit-body'>{html.escape(t(lang, 'limit_body'))}</p>"
        + (f"<p class='rp-limit-wait'>{html.escape(wait)}</p>" if wait else "")
        + f"<p class='rp-limit-local'>{html.escape(t(lang, 'limit_local'))}</p>"
        "</div></div>"
    )


def answer_html(result: AnswerResult, streaming: bool = False,
                lang: str = "en") -> str:
    if result.rate_limited and not result.text:
        return limit_html(result, lang)
    if result.error and not result.text:
        return f"<div class='rp-error'>{html.escape(result.error)}</div>"
    if not result.text:
        return skeleton(2, lang)

    if result.refused:
        label = t(lang, "refused")
        shell = "rp-answer rp-refusal"
    else:
        count = len(result.citations)
        label = (t(lang, "grounded_one") if count == 1
                 else t(lang, "grounded_many", n=count))
        shell = "rp-answer"
    badge = (f"<span class='rp-badge'><span class='rp-dot'></span>"
             f"{html.escape(label)}</span>")

    body = markdown(result.text, lang)
    if streaming:
        body += "<span class='rp-caret'></span>"

    error = (
        f"<div class='rp-error' style='margin-top:14px'>{html.escape(result.error)}</div>"
        if result.error else ""
    )
    return f"<div class='{shell}'>{badge}{body}{error}</div>"


def services_html(services: list[ServiceLink], lang: str = "en") -> str:
    """The pages where the procedure is actually carried out.

    Placed directly under the answer, because telling somebody a service
    exists and making them hunt for it is most of what makes this hard.
    """
    if not services:
        return ""
    rows = []
    for index, service in enumerate(services):
        host = service.url.split("/")[2] if "://" in service.url else service.url
        rows.append(
            f"<a class='rp-service' style='--i:{index}' "
            f"href='{html.escape(service.url)}' target='_blank' "
            f"rel='noopener noreferrer' "
            f"data-pop-label=\"{html.escape(t(lang, 'pop_service'), quote=True)}\">"
            f"<span class='rp-service-icon'>&#8599;</span>"
            f"<span class='rp-service-body'>"
            f"<span class='rp-service-title'>{html.escape(service.title)}</span>"
            f"<span class='rp-service-host'>{html.escape(host)}</span></span></a>"
        )
    return (f"<div class='rp-services'>"
            f"<div class='rp-sources-head'>{html.escape(t(lang, 'services_head'))}</div>"
            f"{''.join(rows)}"
            f"<div class='rp-service-note'>{html.escape(t(lang, 'services_note'))}</div>"
            f"</div>")


def near_misses_html(result: AnswerResult, lang: str = "en") -> str:
    """Where to go next when we refuse.

    A refusal that ends the conversation is only half honest: the corpus was
    searched, something came close, and the person is still standing where
    they started. These are the passages that did not clear the bar, offered
    as leads rather than as answers — plus the official search, so there is
    always a next step that is not a guess.
    """
    if not result.refused or result.gate is None:
        return ""

    seen: dict[str, object] = {}
    for hit in result.gate.rejected:
        fiche = hit.metadata.get("fiche_id", "")
        if fiche and fiche not in seen:
            seen[fiche] = hit
    near = list(seen.values())[:4]

    query = (result.prepared.search_query if result.prepared else result.question)
    search = (
        "https://www.service-public.gouv.fr/particuliers/recherche?keyword="
        + urllib.parse.quote(query or "")
    )
    rows = []
    for index, hit in enumerate(near):
        meta = hit.metadata
        rows.append(
            f"<a class='rp-near' style='--i:{index}' "
            f"href='{html.escape(meta.get('source_url', ''))}' target='_blank' "
            f"rel='noopener noreferrer'>"
            f"<span class='rp-near-title'>"
            f"{html.escape(meta.get('fiche_title_fr', ''))}</span>"
            f"<span class='rp-near-meta'>"
            f"<span class='rp-source-id'>{html.escape(meta.get('fiche_id', ''))}</span>"
            + (f"<span>{html.escape(meta.get('last_updated', ''))}</span>"
               if meta.get("last_updated") else "")
            + "</span></a>"
        )

    return (
        f"<div class='rp-near-block'>"
        f"<div class='rp-sources-head'>{html.escape(t(lang, 'near_head'))}</div>"
        f"<p class='rp-near-note'>{html.escape(t(lang, 'near_note'))}</p>"
        f"{''.join(rows)}"
        f"<a class='rp-near-search' href='{html.escape(search)}' target='_blank' "
        f"rel='noopener noreferrer'>{html.escape(t(lang, 'search_official'))} "
        f"&#8599;</a></div>"
    )


# ----------------------------------------------------------------- sources --

def sources_html(citations: list[Citation], lang: str = "en") -> str:
    if not citations:
        return ""
    cards = []
    for index, citation in enumerate(citations):
        scope = (
            f"<span class='rp-scope'>{html.escape(citation.scope_label)}</span>"
            if citation.scope_label else ""
        )
        date_class = "" if citation.last_updated_is_plausible else " class='rp-date-suspect'"
        cards.append(
            f"<a class='rp-source' style='--i:{index}' "
            f"href='{html.escape(citation.url)}' target='_blank' "
            f"rel='noopener noreferrer' title='{html.escape(citation.url)}' "
            f"data-pop-label=\"{html.escape(t(lang, 'pop_source'), quote=True)}\">"
            f"<span class='rp-source-link'>&#8599;</span>"
            f"<div class='rp-source-title'>{html.escape(citation.title_fr)}</div>"
            f"<div class='rp-source-meta'>"
            f"<span class='rp-source-id'>{html.escape(citation.fiche_id)}</span>"
            f"{scope}"
            f"<span{date_class}>{html.escape(citation.updated_label)}</span>"
            f"</div>"
            + (f"<span class='rp-peek' data-expandable>"
               f"{html.escape(t(lang, 'peek'))}</span>"
               f"<span class='rp-excerpt'>{html.escape(citation.excerpt)}</span>"
               if citation.excerpt else "")
            + "</a>"
        )
    # Closed by default. The licence the data ships under requires the source
    # and its update date to be stated, and it is the only way a reader can
    # check an answer — so it is tucked away, never dropped.
    count = len(citations)
    label = (t(lang, "sources_toggle_one") if count == 1
             else t(lang, "sources_toggle_many", n=count))
    return (
        "<details class='rp-sources'>"
        f"<summary class='rp-sources-toggle'>"
        f"<svg class='rp-chev' width='11' height='11' viewBox='0 0 12 12' "
        f"fill='none' aria-hidden='true'>"
        f"<path d='M4 2.5 L8 6 L4 9.5' stroke='currentColor' stroke-width='1.7' "
        f"stroke-linecap='round' stroke-linejoin='round'/></svg>"
        f"{html.escape(label)}</summary>"
        f"<div class='rp-sources-body'>{''.join(cards)}</div>"
        "</details>"
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


def debug_html(result: AnswerResult, lang: str = "en") -> str:
    if result.prepared is None or result.gate is None:
        return f"<div class='rp-note'>{html.escape(t(lang, 'debug_empty'))}</div>"

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

def glossary_html(search: str = "", lang: str = "en") -> str:
    needle = glossary._fold(search.strip())
    terms = [
        t for t in glossary.load()
        if not needle
        or needle in glossary._fold(t.fr)
        or needle in glossary._fold(t.en)
        or any(needle in glossary._fold(a) for a in t.aliases_en)
    ]
    if not terms:
        return (f"<div class='rp-note'>"
                f"{html.escape(t(lang, 'no_match', q=search))}</div>")

    cards = []
    for index, term in enumerate(terms):
        provenance = (
            f"<span class='rp-prov rp-prov-official'>Official definition · "
            f"{html.escape(term.definition_id or '')}</span>"
            if term.is_official else
            "<span class='rp-prov rp-prov-authored'>Written for Claré</span>"
        )
        aliases = [a for a in term.aliases_en
                   if a.lower() not in {term.fr.lower(), term.en.lower()}]
        also = (
            "<div class='rp-gloss-aliases'>"
            "<span class='rp-gloss-aliases-label'>"
            + html.escape(t(lang, "also_called")) + "</span>"
            + " ".join(f"<span class='rp-alias'>{html.escape(a)}</span>"
                       for a in aliases[:6])
            + "</div>"
        ) if aliases else ""
        cards.append(
            f"<div class='rp-gloss' style='--i:{index}' data-expandable"
            f" tabindex='0' role='button'>"
            f"<div class='rp-gloss-fr'>{html.escape(term.fr)}"
            f"<svg class='rp-gloss-chev' width='12' height='12' viewBox='0 0 12 12'"
            f" fill='none' aria-hidden='true'><path d='M3 4.5 L6 7.5 L9 4.5'"
            f" stroke='currentColor' stroke-width='1.7' stroke-linecap='round'"
            f" stroke-linejoin='round'/></svg></div>"
            f"<div class='rp-gloss-en'>{html.escape(term.en)}</div>"
            f"<div class='rp-gloss-def'>{html.escape(term.explanation_en)}</div>"
            f"<div class='rp-gloss-more'>"
            f"<div class='rp-gloss-def-fr'>{html.escape(term.explanation_fr)}</div>"
            f"{also}{provenance}</div></div>"
        )
    return f"<div class='rp-gloss-grid'>{''.join(cards)}</div>"


# ==========================================================================
#  The conversation
#  A question and its answer belong together on screen. The landing state
#  and the answered state are the same surface, so asking does not feel like
#  being taken somewhere else.
# ==========================================================================

ICONS = {
    # One family, drawn on a 24-grid, stroked not filled, so they scale and
    # recolour with the text beside them.
    "send": "M5 12h14M13 6l6 6-6 6",
    "copy": "M9 9V6.5A1.5 1.5 0 0 1 10.5 5h7A1.5 1.5 0 0 1 19 6.5v7"
            "a1.5 1.5 0 0 1-1.5 1.5H15M5.5 9h8A1.5 1.5 0 0 1 15 10.5v7"
            "A1.5 1.5 0 0 1 13.5 19h-8A1.5 1.5 0 0 1 4 17.5v-7A1.5 1.5 0 0 1 5.5 9Z",
    "up": "M7 11l5-5 5 5M12 6v12",
    "down": "M7 13l5 5 5-5M12 18V6",
    "external": "M14 5h5v5M19 5l-8 8M18 14v4.5A1.5 1.5 0 0 1 16.5 20h-11"
                "A1.5 1.5 0 0 1 4 18.5v-11A1.5 1.5 0 0 1 5.5 6H10",
    "restart": "M4 10a8 8 0 1 1 1.5 6M4 5v5h5",
    "bug": "M8.5 8.5a3.5 3.5 0 0 1 7 0M7 13H3.5M20.5 13H17M7.6 9.4 4.8 7.2"
           "M16.4 9.4l2.8-2.2M7.6 17.2 4.8 19.4M16.4 17.2l2.8 2.2M9.8 6 8.6 4"
           "M14.2 6l1.2-2M12 11v6",
    "globe": "M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM3 12h18"
             "M12 3c2.2 2.4 3.3 5.4 3.3 9s-1.1 6.6-3.3 9c-2.2-2.4-3.3-5.4-3.3-9S9.8 5.4 12 3Z",
}


def icon(name: str, size: int = 16, cls: str = "") -> str:
    path = ICONS.get(name, "")
    return (
        f"<svg class='rp-icon {cls}' width='{size}' height='{size}' "
        f"viewBox='0 0 24 24' fill='none' stroke='currentColor' "
        f"stroke-width='1.7' stroke-linecap='round' stroke-linejoin='round' "
        f"aria-hidden='true'><path d='{path}'/></svg>"
    )


def thinking_html(lang: str = "en") -> str:
    """Shown while the sources are being read. Three dots, nothing clever."""
    return (
        "<div class='rp-thinking' role='status' aria-live='polite'>"
        "<span class='rp-think-dots'><i></i><i></i><i></i></span>"
        f"<span class='rp-think-label'>{html.escape(t(lang, 'thinking'))}…</span>"
        "</div>"
    )


def turn_html(turn: dict, lang: str, reply_lang: str, index: int) -> str:
    """One question and its answer."""
    question = html.escape(turn.get("question", ""))
    body_parts: list[str] = []

    result = turn.get("result")
    if turn.get("state") == "clarify":
        body_parts.append(clarification_html(turn.get("surface", ""), lang))
    elif turn.get("state") == "thinking" or result is None:
        body_parts.append(thinking_html(lang))
    else:
        if result.rate_limited and not result.text:
            body_parts.append(limit_html(result, lang))
        elif result.error and not result.text:
            body_parts.append(error_html(result.error, lang))
        else:
            body_parts.append(
                answer_html(result, streaming=turn.get("streaming", False),
                            lang=reply_lang))
            if result.refused:
                # A named institution owns this answer; the nearest
                # public-administration pages do not, and offering them here
                # is what made a miss look like a result.
                institution = turn.get("institution")
                if institution:
                    body_parts.append(institution_gap_html(institution, lang))
                else:
                    body_parts.append(near_misses_html(result, lang))
            else:
                body_parts.append(services_html(result.services, lang))
                body_parts.append(sources_html(result.citations, lang))
            # Stored as a translation key, so it follows a language switch.
            note = turn.get("freshness")
            if note:
                body_parts.append(
                    f"<p class='rp-freshness'>{html.escape(t(lang, note))}</p>")
            if not turn.get("streaming"):
                body_parts.append(actions_html(index, lang))

    # The speaker is carried by placement and by the mark, not by a caption
    # over every message; the caption stays for screen readers, which have
    # neither.
    return (
        f"<article class='rp-turn'>"
        f"<div class='rp-ask-bubble'>"
        f"<span class='rp-sr-only'>{html.escape(t(lang, 'you'))}</span>"
        f"<p class='rp-ask-text'>{question}</p></div>"
        f"<div class='rp-reply'>"
        f"<div class='rp-reply-head'>"
        f"<span class='rp-reply-mark' aria-hidden='true'>{brand.mark(17)}</span>"
        f"<span class='rp-who rp-who-assistant'>"
        f"{html.escape(t(lang, 'assistant'))}</span></div>"
        f"{''.join(p for p in body_parts if p)}"
        f"</div></article>"
    )


def clarification_html(surface: str, lang: str = "en") -> str:
    """Ask the one question that makes the rest answerable.

    Not an error and not styled as one: nothing has gone wrong, we just do not
    know yet which institution was meant, and guessing would produce a
    confident answer about the wrong one.
    """
    _ = surface
    return (
        f"<div class='rp-clarify' role='status'>"
        f"<p class='rp-clarify-head'>{html.escape(t(lang, 'clarify_head'))}</p>"
        f"<p class='rp-clarify-body'>{html.escape(t(lang, 'clarify_body'))}</p>"
        f"</div>"
    )


def institution_gap_html(institution: dict, lang: str = "en") -> str:
    """Say which body owns the answer, instead of the nearest official page.

    Reached when the question was about a named institution and this corpus —
    French public administration — had nothing that answered it. Offering the
    closest public-administration pages here would be offering official
    documents that were never about the question.
    """
    if not institution:
        return ""
    name = institution.get("name", "")
    where = " · ".join(part for part in (institution.get("commune", ""),
                                         institution.get("departement", ""))
                       if part)
    link = institution.get("url", "")
    tail = (
        f"<a class='rp-entity-link' href='{html.escape(link)}' target='_blank' "
        f"rel='noopener noreferrer'>"
        f"{html.escape(t(lang, 'entity_register', name=name))} &#8599;</a>"
        if link else ""
    )
    return (
        f"<div class='rp-entity'>"
        f"<p class='rp-entity-head'>"
        f"{html.escape(t(lang, 'entity_head', name=name))}</p>"
        f"<p class='rp-entity-body'>{html.escape(t(lang, 'entity_body'))}</p>"
        + (f"<p class='rp-entity-where'>{html.escape(where)}</p>" if where else "")
        + tail
        + "</div>"
    )


def actions_html(index: int, lang: str = "en") -> str:
    """Quiet controls, only after the answer has finished arriving."""
    return (
        "<div class='rp-actions'>"
        f"<button type='button' class='rp-action' data-copy-answer "
        f"data-done=\"{html.escape(t(lang, 'copied_answer'))}\" "
        f"aria-label='{html.escape(t(lang, 'copy_answer'))}'>"
        f"{icon('copy')}<span>{html.escape(t(lang, 'copy_answer'))}</span></button>"
        f"<button type='button' class='rp-action' data-vote='up' "
        f"aria-label='{html.escape(t(lang, 'helpful'))}'>"
        f"{icon('up')}<span>{html.escape(t(lang, 'helpful'))}</span></button>"
        f"<button type='button' class='rp-action' data-vote='down' "
        f"aria-label='{html.escape(t(lang, 'not_helpful'))}'>"
        f"{icon('down')}<span>{html.escape(t(lang, 'not_helpful'))}</span></button>"
        f"<span class='rp-action-thanks' hidden>"
        f"{html.escape(t(lang, 'thanks_feedback'))}</span>"
        "</div>"
    )


def error_html(message: str, lang: str = "en") -> str:
    """An error the reader can act on, not a stack trace."""
    friendly = t(lang, "err_network") if "unreachable" in message.lower() \
        else t(lang, "err_unknown")
    return (
        f"<div class='rp-error' role='alert'>"
        f"<strong>{html.escape(friendly)}</strong>"
        f"<span class='rp-error-detail'>{html.escape(message[:200])}</span>"
        f"</div>"
    )


def thread_html(turns: list[dict], lang: str, reply_lang: str) -> str:
    if not turns:
        return ""
    return ("<div class='rp-thread' role='log' aria-live='polite'>"
            + "".join(turn_html(turn, lang, reply_lang, i)
                      for i, turn in enumerate(turns))
            + "</div>")


def categories_html(lang: str = "en") -> str:
    """Entry points that are questions, not filters.

    A row, not a card: icon, what it covers, and the kind of thing it answers.
    Six identical white rectangles read as a menu of products; a ruled list
    reads as a contents page, which is what this is.
    """
    from app.ui.i18n import CATEGORIES

    rows = []
    for index, (key, slug, question) in enumerate(
            CATEGORIES.get(lang, CATEGORIES["en"])):
        rows.append(
            f"<button type='button' class='rp-cat' style='--i:{index}' "
            f"data-question=\"{html.escape(question, quote=True)}\">"
            f"<span class='rp-cat-icon rp-cat-{slug}' aria-hidden='true'></span>"
            f"<span class='rp-cat-text'>"
            f"<span class='rp-cat-label'>{html.escape(t(lang, key))}</span>"
            f"<span class='rp-cat-sub'>{html.escape(t(lang, key + '_sub'))}</span>"
            f"</span>"
            f"<span class='rp-cat-go' aria-hidden='true'>&#8594;</span>"
            f"</button>"
        )
    return (f"<nav class='rp-cats' aria-label=\"{html.escape(t(lang, 'cat_head'), quote=True)}\">"
            f"<h2 class='rp-cats-head'>{html.escape(t(lang, 'cat_head'))}</h2>"
            f"<div class='rp-cats-grid'>{''.join(rows)}</div></nav>")
