"""Repères — the local interface.

    uv run python -m app.ui.app     →  http://localhost:7860
"""

from __future__ import annotations

import html
from functools import lru_cache
from pathlib import Path

import gradio as gr

from app.answer.generate import answer_stream
from app.config import get_settings
from app.ingest.embed import fetch_all, get_collection
from app.ingest.fetch import read_manifest
from app.llm import get_chat_provider
from app.query import glossary
from app.ui import render

STYLES = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")

EXAMPLES = [
    "I'm a student — when do I need to renew my residence permit?",
    "Je viens d'arriver, comment valider mon VLS-TS ?",
    "How much deposit can a landlord ask for?",
    "There are no appointment slots at my préfecture. What can I do?",
]

MASTHEAD = """
<div class="rp-masthead">
  <h1 class="rp-wordmark">Rep<span class="rp-accent">è</span>res</h1>
  <p class="rp-tagline">
    Answers for people who have recently arrived in France, taken only from
    official French government sources. Ask in English or French.
  </p>
</div>
<div class="rp-disclaimer">
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 4.6v4.2M8 11.2h.01" stroke="currentColor" stroke-width="1.6"
          stroke-linecap="round"/>
  </svg>
  <div><strong>This is not legal advice.</strong> Repères reports what official
  sources say and cites them. It cannot tell you what to do about a refusal, an
  appeal, or your individual case. For that, contact the administration
  concerned.</div>
</div>
"""


def corpus_html() -> str:
    settings = get_settings()
    manifest = read_manifest(settings)
    try:
        collection = get_collection(settings)
        chunk_count = collection.count()
    except Exception:
        chunk_count = 0

    if not chunk_count:
        return (
            "<div class='rp-note'>The index is empty. Build it with "
            "<code>uv run python -m app.ingest.pipeline</code>.</div>"
        )

    oldest, newest, documents, ingested = _corpus_dates(chunk_count)
    feed_version = (manifest or {}).get("feed_version", settings.feed_version)
    files = sum(s["document_count"] for s in (manifest or {}).get("segments", []))

    stats = [
        (f"{documents:,}", "source documents"),
        (f"{chunk_count:,}", "indexed passages"),
        (f"{files:,}", "files in the feed"),
        (feed_version, "DILA feed version"),
        (newest or "—", "most recently updated source"),
        (oldest or "—", "oldest source"),
    ]
    cards = "".join(
        f"<div class='rp-stat' style='--i:{i}'>"
        f"<div class='rp-stat-value'>{html.escape(str(value))}</div>"
        f"<div class='rp-stat-label'>{label}</div></div>"
        for i, (value, label) in enumerate(stats)
    )

    provider = get_chat_provider(settings)
    ok, detail = provider.health()
    status = ("rp-prov-official", "reachable") if ok else ("rp-prov-authored", "unavailable")

    return f"""
      <div class="rp-stat-grid">{cards}</div>
      <div class="rp-note">
        <strong>Source.</strong> Content published by the Direction de
        l'information légale et administrative (DILA) as open data on
        data.gouv.fr, under the
        <a href="https://www.etalab.gouv.fr/licence-ouverte-open-licence"
           target="_blank" rel="noopener noreferrer">Licence Ouverte</a>.
        Only the French source text is indexed: French is the legal source of
        truth, and the machine-translated versions are disclaimed by the
        government that publishes them. Index built {html.escape(ingested or "—")}.
      </div>
      <div class="rp-note" style="margin-top:10px">
        <strong>Models.</strong> Passages are embedded locally with
        <code>{html.escape(settings.embed_model)}</code>; the corpus never leaves
        this machine. Answers are written by
        <code>{html.escape(provider.model)}</code> via
        <code>{html.escape(provider.name)}</code>
        <span class="rp-prov {status[0]}">{status[1]}</span>
        — questions are sent there.
      </div>
    """


@lru_cache(maxsize=4)
def _corpus_dates(_fingerprint: int) -> tuple[str, str, int, str]:
    """Date range and document count, read from the indexed metadata."""
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


def ask(question: str):
    """Stream the answer, its sources, and the retrieval trace."""
    question = (question or "").strip()
    if not question:
        yield ("<div class='rp-note'>Type a question to begin.</div>", "",
               render.debug_html(_blank()))
        return

    yield (render.skeleton("Searching the official sources…"), "", "")

    final = None
    for result in answer_stream(question):
        final = result
        streaming = bool(result.text)
        yield (
            render.answer_html(result, streaming=streaming),
            render.sources_html(result.citations),
            render.debug_html(result),
        )
    if final is not None:
        yield (
            render.answer_html(final, streaming=False),
            render.sources_html(final.citations),
            render.debug_html(final),
        )


def _blank():
    from app.answer.generate import AnswerResult

    return AnswerResult("", "en", "", refused=False)


def build() -> gr.Blocks:
    settings = get_settings()

    # Gradio 6 takes css and theme at launch time, not construction time.
    with gr.Blocks(title="Repères", analytics_enabled=False) as demo:
        gr.HTML(MASTHEAD)

        with gr.Tabs():
            with gr.Tab("Ask"):
                question = gr.Textbox(
                    placeholder="Ask in English or French — for example, "
                                "how to renew a student residence permit",
                    lines=2, elem_classes="rp-ask", show_label=False,
                )
                with gr.Row(elem_classes="rp-submit-row"):
                    submit = gr.Button("Ask", variant="primary",
                                       elem_classes="rp-submit", scale=0)
                gr.HTML("<div class='rp-chip-label'>Try:</div>")
                with gr.Row(elem_classes="rp-examples"):
                    chips = [
                        gr.Button(text, elem_classes="rp-chip", scale=0)
                        for text in EXAMPLES
                    ]

                answer_box = gr.HTML()
                sources_box = gr.HTML()
                with gr.Accordion("How this answer was found", open=False):
                    debug_box = gr.HTML()

                outputs = [answer_box, sources_box, debug_box]
                submit.click(ask, question, outputs)
                question.submit(ask, question, outputs)
                for chip, text in zip(chips, EXAMPLES):
                    chip.click(lambda t=text: t, None, question).then(
                        ask, question, outputs
                    )

            with gr.Tab("Glossary"):
                gr.HTML(
                    "<p class='rp-tagline' style='margin:2px 0 14px'>"
                    "The French words you will meet at a counter or in a letter. "
                    "Definitions marked <em>official</em> are published in the "
                    "source corpus; the rest are written for Repères.</p>"
                )
                search = gr.Textbox(placeholder="Search a term…", show_label=False,
                                    elem_classes="rp-gloss-search")
                gloss_box = gr.HTML(render.glossary_html())
                search.change(render.glossary_html, search, gloss_box)

            with gr.Tab("Corpus"):
                gr.HTML(
                    "<p class='rp-tagline' style='margin:2px 0 14px'>"
                    "What Repères has indexed, and where it came from.</p>"
                )
                corpus_box = gr.HTML(corpus_html)
                gr.Button("Refresh", elem_classes="rp-chip", scale=0).click(
                    corpus_html, None, corpus_box
                )

        if not settings.debug_panel:
            debug_box.visible = False

    return demo


def main() -> None:
    build().launch(
        server_name="127.0.0.1",
        server_port=7860,
        css=STYLES,
        theme=gr.themes.Base(),
        inbrowser=False,
        quiet=False,
    )


if __name__ == "__main__":
    main()
