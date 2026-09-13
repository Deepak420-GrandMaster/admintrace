"""Repères — the local interface.

    uv run python -m app.ui.app     →  http://localhost:7860

Two languages are chosen separately, because they are separate questions. The
interface language is what you read the buttons in. The answer language is what
you want the reply written in, and someone may well ask in English and want the
reply in French to forward to a landlord, or the reverse.
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

import gradio as gr

from app.answer.generate import AnswerResult, answer_stream
from app.config import get_settings
from app.ingest.embed import fetch_all, get_collection
from app.ingest.fetch import read_manifest
from app.llm import get_chat_provider
from app.ui import brand, render
from app.ui.i18n import EXAMPLES, LANGUAGES, t

STYLES = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")

# Runs once in the page. Only two jobs: copy the French phrasing to the
# clipboard, and give the page its icon.
HEAD = f"""
<link rel="icon" href="{brand.FAVICON}">
<script>
document.addEventListener('click', async (event) => {{
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  const block = button.closest('.rp-say');
  if (!block) return;
  const parts = [...block.querySelectorAll('p')]
      .filter(p => !p.classList.contains('rp-say-note'))
      .map(p => p.innerText.trim());
  try {{
    await navigator.clipboard.writeText(parts.join('\\n'));
  }} catch (error) {{
    const area = document.createElement('textarea');
    area.value = parts.join('\\n');
    document.body.appendChild(area); area.select();
    document.execCommand('copy'); area.remove();
  }}
  const original = button.textContent;
  button.textContent = button.dataset.done || 'Copied';
  button.classList.add('rp-done');
  setTimeout(() => {{
    button.textContent = original;
    button.classList.remove('rp-done');
  }}, 1600);
}});
</script>
"""


def masthead(lang: str) -> str:
    return f"""
{brand.wordmark()}
<p class="rp-tagline">{html.escape(t(lang, 'tagline'))}</p>
<div class="rp-disclaimer">
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 4.6v4.2M8 11.2h.01" stroke="currentColor" stroke-width="1.6"
          stroke-linecap="round"/>
  </svg>
  <div><strong>{html.escape(t(lang, 'disclaimer_lead'))}</strong>
  {html.escape(t(lang, 'disclaimer_body'))}</div>
</div>
"""


def lang_caption(lang: str, key: str) -> str:
    return (f"<div class='rp-lang-label'>{brand.GLOBE}"
            f"{html.escape(t(lang, key))}</div>")


@lru_cache(maxsize=4)
def _corpus_dates(_fingerprint: int) -> tuple[str, str, int, str]:
    stored = fetch_all(get_collection(), ["metadatas"])
    dates, documents, ingested = [], set(), ""
    for meta in stored["metadatas"]:
        documents.add(meta.get("fiche_id", ""))
        ingested = meta.get("ingested_at", ingested)
        value = meta.get("last_updated")
        # The one mistyped source date must not become the displayed range.
        if value and meta.get("last_updated_is_plausible", True):
            dates.append(value)
    dates.sort()
    return (dates[0] if dates else "", dates[-1] if dates else "",
            len(documents), ingested)


def corpus_html(lang: str = "en") -> str:
    settings = get_settings()
    manifest = read_manifest(settings)
    try:
        chunk_count = get_collection(settings).count()
    except Exception:
        chunk_count = 0

    if not chunk_count:
        return ("<div class='rp-note'>The index is empty. Build it with "
                "<code>uv run python -m app.ingest.pipeline</code>.</div>")

    oldest, newest, documents, ingested = _corpus_dates(chunk_count)
    feed_version = (manifest or {}).get("feed_version", settings.feed_version)
    files = sum(s["document_count"] for s in (manifest or {}).get("segments", []))

    labels = {
        "en": ["source documents", "indexed passages", "files in the feed",
               "DILA feed version", "most recently updated", "oldest source"],
        "fr": ["documents sources", "passages indexés", "fichiers dans le flux",
               "version du flux DILA", "mise à jour la plus récente",
               "source la plus ancienne"],
    }[lang if lang in ("en", "fr") else "en"]
    values = [f"{documents:,}", f"{chunk_count:,}", f"{files:,}",
              feed_version, newest or "—", oldest or "—"]

    cards = "".join(
        f"<div class='rp-stat' style='--i:{i}'>"
        f"<div class='rp-stat-value'>{html.escape(str(value))}</div>"
        f"<div class='rp-stat-label'>{html.escape(label)}</div></div>"
        for i, (value, label) in enumerate(zip(values, labels))
    )

    provider = get_chat_provider(settings)
    ok, _ = provider.health()
    badge = ("rp-prov-official", "reachable") if ok else ("rp-prov-authored", "unavailable")

    source_note = {
        "en": ("<strong>Where this comes from.</strong> Content published by the "
               "Direction de l'information légale et administrative (DILA) as open "
               "data on data.gouv.fr, under the "
               "<a href='https://www.etalab.gouv.fr/licence-ouverte-open-licence' "
               "target='_blank' rel='noopener noreferrer'>Licence Ouverte</a>. "
               "Only the French text is indexed — French is the legal source of "
               "truth, and the government disclaims its own machine translations. "
               f"Index built {html.escape(ingested or '—')}."),
        "fr": ("<strong>D'où viennent ces données.</strong> Contenu publié par la "
               "Direction de l'information légale et administrative (DILA) en open "
               "data sur data.gouv.fr, sous "
               "<a href='https://www.etalab.gouv.fr/licence-ouverte-open-licence' "
               "target='_blank' rel='noopener noreferrer'>Licence Ouverte</a>. "
               "Seul le texte français est indexé : le français fait foi, et "
               "l'administration décline ses propres traductions automatiques. "
               f"Index construit le {html.escape(ingested or '—')}."),
    }[lang if lang in ("en", "fr") else "en"]

    model_note = {
        "en": "<strong>Models.</strong> Passages are embedded on this machine with "
              f"<code>{html.escape(settings.embed_model)}</code> — the corpus never "
              f"leaves it. Answers are written by <code>{html.escape(provider.model)}</code> "
              f"via <code>{html.escape(provider.name)}</code>",
        "fr": "<strong>Modèles.</strong> Les passages sont vectorisés sur cette "
              f"machine avec <code>{html.escape(settings.embed_model)}</code> — le "
              f"corpus ne la quitte jamais. Les réponses sont rédigées par "
              f"<code>{html.escape(provider.model)}</code> via "
              f"<code>{html.escape(provider.name)}</code>",
    }[lang if lang in ("en", "fr") else "en"]

    return (f"<div class='rp-stat-grid'>{cards}</div>"
            f"<div class='rp-note'>{source_note}</div>"
            f"<div class='rp-note' style='margin-top:10px'>{model_note} "
            f"<span class='rp-prov {badge[0]}'>{badge[1]}</span>.</div>")


def ask(question: str, ui_lang: str, answer_lang: str):
    """Stream the answer, its sources, and the retrieval trace."""
    question = (question or "").strip()
    if not question:
        yield (f"<div class='rp-note'>{html.escape(t(ui_lang, 'empty'))}</div>",
               "", f"<div class='rp-note'>{html.escape(t(ui_lang, 'debug_empty'))}</div>")
        return

    reply_lang = answer_lang if answer_lang in ("en", "fr") else ui_lang
    yield (render.skeleton(0, ui_lang), "", "")

    final = None
    for result in answer_stream(question, language=None if answer_lang == "auto"
                                else answer_lang):
        final = result
        if not result.text:
            yield (render.skeleton(1, ui_lang), "", "")
            continue
        yield (
            render.answer_html(result, streaming=True, lang=reply_lang),
            render.sources_html(result.citations, ui_lang),
            render.debug_html(result, ui_lang),
        )
    if final is not None:
        yield (
            render.answer_html(final, streaming=False, lang=reply_lang),
            render.sources_html(final.citations, ui_lang),
            render.debug_html(final, ui_lang),
        )


def build() -> gr.Blocks:
    settings = get_settings()
    start = "en"

    with gr.Blocks(title="Repères", analytics_enabled=False) as demo:
        head = gr.HTML(masthead(start))

        with gr.Row(elem_classes="rp-lang-bar"):
            site_caption = gr.HTML(lang_caption(start, "site_lang"))
            site_lang = gr.Radio(
                choices=[(name, code) for code, name in LANGUAGES],
                value=start, show_label=False, container=False,
                elem_classes="rp-switch",
            )
            reply_caption = gr.HTML(lang_caption(start, "answer_lang"))
            reply_lang = gr.Radio(
                choices=[(t(start, "lang_auto"), "auto"),
                         ("English", "en"), ("Français", "fr")],
                value="auto", show_label=False, container=False,
                elem_classes="rp-switch",
            )

        with gr.Tabs():
            with gr.Tab(t(start, "tab_ask")) as tab_ask:
                question = gr.Textbox(
                    placeholder=t(start, "placeholder"), lines=2,
                    elem_classes="rp-ask", show_label=False,
                )
                with gr.Row(elem_classes="rp-submit-row"):
                    submit = gr.Button(t(start, "submit"), variant="primary",
                                       elem_classes="rp-submit", scale=0)
                try_label = gr.HTML(
                    f"<div class='rp-chip-label'>{html.escape(t(start, 'try'))}</div>")
                with gr.Row(elem_classes="rp-examples"):
                    chips = [gr.Button(text, elem_classes="rp-chip", scale=0)
                             for text in EXAMPLES[start]]

                answer_box = gr.HTML(
                    f"<div class='rp-note'>{html.escape(t(start, 'empty'))}</div>")
                sources_box = gr.HTML()
                with gr.Accordion(t(start, "debug_title"), open=False) as debug_acc:
                    debug_box = gr.HTML(
                        f"<div class='rp-note'>"
                        f"{html.escape(t(start, 'debug_empty'))}</div>")

            with gr.Tab(t(start, "tab_glossary")) as tab_gloss:
                gloss_intro = gr.HTML(
                    f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                    f"{html.escape(t(start, 'glossary_intro'))}</p>")
                search = gr.Textbox(placeholder=t(start, "glossary_search"),
                                    show_label=False, elem_classes="rp-gloss-search")
                gloss_box = gr.HTML(render.glossary_html("", start))

            with gr.Tab(t(start, "tab_corpus")) as tab_corpus:
                corpus_intro = gr.HTML(
                    f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                    f"{html.escape(t(start, 'corpus_intro'))}</p>")
                corpus_box = gr.HTML(lambda: corpus_html(start))
                refresh = gr.Button(t(start, "refresh"),
                                    elem_classes="rp-chip", scale=0)

        outputs = [answer_box, sources_box, debug_box]
        inputs = [question, site_lang, reply_lang]
        submit.click(ask, inputs, outputs)
        question.submit(ask, inputs, outputs)
        for chip, text in zip(chips, EXAMPLES[start]):
            chip.click(lambda t=text: t, None, question).then(ask, inputs, outputs)

        search.change(render.glossary_html, [search, site_lang], gloss_box)
        refresh.click(corpus_html, site_lang, corpus_box)

        def switch_language(lang: str, current_search: str):
            """Re-label the whole interface without losing what is on screen."""
            return [
                masthead(lang),
                lang_caption(lang, "site_lang"),
                lang_caption(lang, "answer_lang"),
                gr.update(choices=[(t(lang, "lang_auto"), "auto"),
                                   ("English", "en"), ("Français", "fr")]),
                gr.update(placeholder=t(lang, "placeholder")),
                gr.update(value=t(lang, "submit")),
                f"<div class='rp-chip-label'>{html.escape(t(lang, 'try'))}</div>",
                *[gr.update(value=text) for text in EXAMPLES[lang]],
                gr.update(label=t(lang, "debug_title")),
                gr.update(label=t(lang, "tab_ask")),
                gr.update(label=t(lang, "tab_glossary")),
                gr.update(label=t(lang, "tab_corpus")),
                f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                f"{html.escape(t(lang, 'glossary_intro'))}</p>",
                gr.update(placeholder=t(lang, "glossary_search")),
                render.glossary_html(current_search or "", lang),
                f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                f"{html.escape(t(lang, 'corpus_intro'))}</p>",
                gr.update(value=t(lang, "refresh")),
                corpus_html(lang),
                f"<div class='rp-note'>{html.escape(t(lang, 'empty'))}</div>",
                f"<div class='rp-note'>{html.escape(t(lang, 'debug_empty'))}</div>",
            ]

        site_lang.change(
            switch_language,
            [site_lang, search],
            [head, site_caption, reply_caption, reply_lang, question, submit,
             try_label, *chips, debug_acc, tab_ask, tab_gloss, tab_corpus,
             gloss_intro, search, gloss_box, corpus_intro, refresh, corpus_box,
             answer_box, debug_box],
        )

        if not settings.debug_panel:
            debug_box.visible = False

    return demo


def main() -> None:
    build().launch(
        server_name="127.0.0.1",
        server_port=7860,
        css=STYLES,
        head=HEAD,
        theme=gr.themes.Base(),
        inbrowser=False,
        quiet=False,
    )


if __name__ == "__main__":
    main()
