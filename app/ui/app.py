"""Sésame — the local interface.

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
from app.query.detect import detect
from app.ui import brand, render
from app.directory import universities
from app.feedback import submit as submit_report
from app.ui.i18n import EXAMPLES, LANGUAGES, t

STYLES = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")

# Runs once in the page. Only two jobs: copy the French phrasing to the
# clipboard, and give the page its icon.
HEAD = f"""
<link rel="icon" href="{brand.FAVICON}">
<script>
(() => {{
  // Everything here is delegated from the document, because Gradio replaces
  // chunks of the page on every interaction and anything bound to an element
  // directly would be lost the first time an answer arrives.

  const copyLabelFallback = "Copied";

  document.addEventListener("click", async (event) => {{
    // --- copy the suggested French phrasing -------------------------------
    const copy = event.target.closest("[data-copy]");
    if (copy) {{
      const block = copy.closest(".rp-say");
      if (block) {{
        const text = [...block.querySelectorAll("p")]
          .filter((p) => !p.classList.contains("rp-say-note"))
          .map((p) => p.innerText.trim())
          .join("\\n");
        try {{
          await navigator.clipboard.writeText(text);
        }} catch (error) {{
          const area = document.createElement("textarea");
          area.value = text;
          document.body.appendChild(area);
          area.select();
          document.execCommand("copy");
          area.remove();
        }}
        const original = copy.textContent;
        copy.textContent = copy.dataset.done || copyLabelFallback;
        copy.classList.add("rp-done");
        setTimeout(() => {{
          copy.textContent = original;
          copy.classList.remove("rp-done");
        }}, 1600);
      }}
      return;
    }}

    // --- open a source card in place --------------------------------------
    // The arrow opens the official page; anywhere else shows the passage the
    // answer actually drew on, so the reader can check it without leaving.
    if (event.target.closest(".rp-source-link")) return;
    const card = event.target.closest("[data-expandable]");
    if (card) card.classList.toggle("rp-open");
  }});

  // --- press feedback on the primary actions ------------------------------
  document.addEventListener("pointerdown", (event) => {{
    const button = event.target.closest("button.rp-submit, button.rp-chip");
    if (!button) return;
    const box = button.getBoundingClientRect();
    const size = Math.max(box.width, box.height);
    const ripple = document.createElement("span");
    ripple.className = "rp-ripple";
    ripple.style.width = ripple.style.height = size + "px";
    ripple.style.left = event.clientX - box.left - size / 2 + "px";
    ripple.style.top = event.clientY - box.top - size / 2 + "px";
    button.appendChild(ripple);
    setTimeout(() => ripple.remove(), 640);
  }});

  // --- keyboard ------------------------------------------------------------
  document.addEventListener("keydown", (event) => {{
    const field = document.querySelector(".rp-ask textarea");
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {{
      event.preventDefault();
      if (field) {{ field.focus(); field.select(); }}
    }}
    if (event.key === "Escape" && document.activeElement === field) field.blur();
  }});

  // --- the light follows the hand -----------------------------------------
  // The reference swings a clock hand toward the cursor; the same idea, far
  // quieter: the page warms where the pointer is, so it feels answered before
  // it has answered anything.
  const spot = document.createElement("div");
  spot.className = "rp-spot";
  document.body.appendChild(spot);

  let frame = 0;
  document.addEventListener("pointermove", (event) => {{
    if (event.pointerType === "touch") return;
    if (frame) return;
    frame = requestAnimationFrame(() => {{
      frame = 0;
      spot.style.setProperty("--sx", event.clientX + "px");
      spot.style.setProperty("--sy", event.clientY + "px");
      spot.classList.add("rp-lit");

      // Cards light from where the pointer actually is, not from their middle.
      const card = event.target.closest(
        ".rp-source, .rp-service, .rp-gloss, .rp-stat");
      if (card) {{
        const box = card.getBoundingClientRect();
        card.style.setProperty("--mx", ((event.clientX - box.left) / box.width * 100) + "%");
        card.style.setProperty("--my", ((event.clientY - box.top) / box.height * 100) + "%");
      }}
    }});
  }}, {{ passive: true }});
  document.addEventListener("pointerleave", () => spot.classList.remove("rp-lit"));

  // --- popovers -------------------------------------------------------------
  // A hover should answer the question it raises: what does this source
  // actually say, and where will this link take me.
  const pop = document.createElement("div");
  pop.className = "rp-pop";
  document.body.appendChild(pop);
  let popTimer = 0;

  const showPop = (target, label, body, meta) => {{
    pop.innerHTML = "";
    const tag = document.createElement("span");
    tag.className = "rp-pop-label";
    tag.textContent = label;
    pop.appendChild(tag);
    pop.appendChild(document.createTextNode(body));
    if (meta) {{
      const m = document.createElement("span");
      m.className = "rp-pop-meta";
      m.textContent = meta;
      pop.appendChild(m);
    }}
    const box = target.getBoundingClientRect();
    const width = Math.min(360, window.innerWidth - 32);
    pop.style.width = width + "px";
    pop.style.left = Math.max(16, Math.min(box.left, window.innerWidth - width - 16)) + "px";
    const below = box.bottom + 12;
    pop.style.top = (below + 170 > window.innerHeight
      ? Math.max(16, box.top - 12 - 170) : below) + "px";
    pop.classList.add("rp-pop-on");
  }};

  const hidePop = () => {{
    clearTimeout(popTimer);
    pop.classList.remove("rp-pop-on");
  }};

  document.addEventListener("pointerover", (event) => {{
    const source = event.target.closest(".rp-source");
    const service = event.target.closest(".rp-service");
    const target = source || service;
    if (!target) return;
    clearTimeout(popTimer);
    popTimer = setTimeout(() => {{
      if (source) {{
        const excerpt = source.querySelector(".rp-excerpt");
        const title = source.querySelector(".rp-source-title");
        if (!excerpt || !excerpt.textContent.trim()) return;
        showPop(source, source.dataset.popLabel || "What this page says",
                excerpt.textContent.trim().slice(0, 300),
                title ? title.textContent.trim() : "");
      }} else {{
        const host = service.querySelector(".rp-service-host");
        showPop(service, service.dataset.popLabel || "Opens the official service",
                service.querySelector(".rp-service-title").textContent.trim(),
                host ? host.textContent.trim() : "");
      }}
    }}, 320);
  }});
  document.addEventListener("pointerout", (event) => {{
    if (event.target.closest(".rp-source, .rp-service")) hidePop();
  }});
  document.addEventListener("scroll", hidePop, {{ passive: true }});

  // --- the connection notice ----------------------------------------------
  // Gradio's stock wording ("Connection to the server was lost") reads like
  // something has gone badly wrong, to an audience already braced for bad
  // news. Same information, said the way a person would.
  const NOTICES = {{
    en: [
      "We've been put on hold. Reconnecting — no ticket number needed.",
      "Lost you for a second. Getting back in the queue…",
      "The connection went for a coffee. It'll be right back."
    ],
    fr: [
      "On nous a mis en attente. Reconnexion — sans ticket, promis.",
      "On s'est perdus une seconde. On se remet dans la file…",
      "La connexion est partie prendre un café. Elle revient."
    ]
  }};
  const STOCK = [
    "connection to the server was lost",
    "attempting reconnection",
    "connection errored out",
    "reconnecting"
  ];

  const softenNotices = () => {{
    const french = !!document.querySelector(".rp-theme-fr");
    const lines = french ? NOTICES.fr : NOTICES.en;
    document.querySelectorAll(".toast-body, .toast-text, [class*='toast']")
      .forEach((node) => {{
        if (node.dataset.rpSoftened) return;
        const said = (node.textContent || "").toLowerCase();
        if (!STOCK.some((phrase) => said.includes(phrase))) return;
        const title = node.querySelector(".toast-title, [class*='title']");
        if (title) title.textContent = french ? "Un instant" : "One moment";
        const body = node.querySelector(".toast-text, p") || node;
        body.textContent = lines[Math.floor(Math.random() * lines.length)];
        node.dataset.rpSoftened = "1";
        node.classList.add("rp-notice");
      }});
  }};

  // --- reveal sources as they come into view -------------------------------
  const watcher = new IntersectionObserver((entries) => {{
    entries.forEach((entry) => {{
      if (entry.isIntersecting) {{
        entry.target.classList.add("rp-in");
        watcher.unobserve(entry.target);
      }}
    }});
  }}, {{ rootMargin: "0px 0px -40px 0px", threshold: 0.05 }});

  const watch = () => (softenNotices(), document
    .querySelectorAll(".rp-source:not(.rp-reveal), .rp-gloss:not(.rp-reveal)")
    .forEach((node) => {{ node.classList.add("rp-reveal"); watcher.observe(node); }}));

  new MutationObserver(watch).observe(document.documentElement,
    {{ childList: true, subtree: true }});
  watch();
}})();
</script>
"""


def brand_block(lang: str) -> str:
    """Wordmark, tagline, and the marker the palette keys off."""
    return f"""
<span class="rp-theme rp-theme-{lang}"></span>
<div class="rp-masthead rp-stage-in">
  {brand.wordmark()}
  <h1 class="rp-headline">{html.escape(t(lang, 'headline'))}</h1>
  <p class="rp-tagline">{html.escape(t(lang, 'tagline'))}</p>
</div>
"""


def privacy_block(lang: str) -> str:
    """What actually happens to a question, read off the live configuration.

    Written from settings rather than hand-typed, so the claim cannot drift
    away from what the system does. Running fully locally and sending the
    question to a hosted model are materially different promises, and for
    people whose immigration status is at stake the difference is not a
    footnote.
    """
    settings = get_settings()
    if settings.llm_provider == "ollama":
        body = t(lang, "privacy_local")
    else:
        # The company the question is actually sent to, not whoever trained
        # the model. "openai/gpt-oss-120b" runs ON Groq; naming OpenAI here
        # would be a false statement about where someone's words go.
        body = t(lang, "privacy_hosted", provider=settings.llm_provider.title())
    return f"""
<div class="rp-privacy">
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M8 1.6 2.9 3.8v3.5c0 3 2.2 5.8 5.1 6.8 2.9-1 5.1-3.8 5.1-6.8V3.8L8 1.6Z"
          stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/>
    <path d="M5.9 8.1 7.3 9.6 10.3 6.4" stroke="currentColor" stroke-width="1.5"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  <div><strong>{html.escape(t(lang, 'privacy_lead'))}</strong> {html.escape(body)}</div>
</div>
"""


def credit_block(lang: str) -> str:
    """Who made it, and the invitation.

    The gaps in this thing are not gaps in the code — they are the things no
    official page will ever say (which bank actually accepts which paper, how
    long a prefecture really takes). Only the people who have just been
    through it know those, so the footer asks.
    """
    return f"""
<div class="rp-credit">
  <div class="rp-credit-made">
    <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 14S1.8 10.3 1.8 6.1A3.3 3.3 0 0 1 8 4.3a3.3 3.3 0 0 1 6.2 1.8
               C14.2 10.3 8 14 8 14Z" fill="currentColor"/>
    </svg>
    <span>{html.escape(t(lang, 'made_by'))}</span>
    <span class="rp-credit-year">{html.escape(t(lang, 'made_year'))}</span>
  </div>
  <p class="rp-credit-community">{html.escape(t(lang, 'community'))}</p>
</div>
"""


def disclaimer_block(lang: str) -> str:
    return f"""
{privacy_block(lang)}
<div class="rp-disclaimer rp-footer">
  <svg width="15" height="15" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <circle cx="8" cy="8" r="7" stroke="currentColor" stroke-width="1.5"/>
    <path d="M8 4.6v4.2M8 11.2h.01" stroke="currentColor" stroke-width="1.6"
          stroke-linecap="round"/>
  </svg>
  <div><strong>{html.escape(t(lang, 'disclaimer_lead'))}</strong>
  {html.escape(t(lang, 'disclaimer_body'))}</div>
</div>
{credit_block(lang)}
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


def institution_choices() -> list[tuple[str, str]]:
    """Every institution in the register, for the picker."""
    return [(t("en", "study_none"), "")] + [
        (f"{i.name}" + (f" · {i.commune}" if i.commune else ""), i.uai)
        for i in universities.load()
    ]


def institution_context(uai: str, lang: str = "en") -> str:
    """What the register actually says about where someone studies.

    Only facts that are in the register. It does not say which bank to use:
    no official source records that, it changes by campus and by year, and
    newcomers are the most targeted group there is for financial offers.
    """
    if not uai:
        return ""
    place = universities.by_uai(uai)
    if place is None:
        return ""

    rows = []
    if place.departement:
        rows.append((t(lang, "study_dept"),
                     f"{place.departement} ({place.departement_id})"))
    if place.academie:
        rows.append((t(lang, "study_aca"), place.academie))
    cells = "".join(
        f"<span class='rp-place-cell'><span class='rp-place-key'>"
        f"{html.escape(key)}</span>{html.escape(value)}</span>"
        for key, value in rows
    )
    site = (f"<a class='rp-place-site' href='{html.escape(place.url)}' "
            f"target='_blank' rel='noopener noreferrer'>"
            f"{html.escape(t(lang, 'study_site'))} &#8599;</a>"
            if place.url else "")
    return (
        f"<div class='rp-place'>"
        f"<div class='rp-place-name'>{html.escape(place.label)}{site}</div>"
        f"<div class='rp-place-grid'>{cells}</div>"
        f"<div class='rp-place-why'>{html.escape(t(lang, 'study_why'))}</div>"
        f"</div>"
    )


def ask(question: str, ui_lang: str, answer_lang: str, uai: str = "",
        _context: dict | None = None):
    """Stream the answer, its sources, and the retrieval trace."""
    hidden = gr.update(visible=False)
    shown = gr.update(visible=get_settings().debug_panel)

    question = (question or "").strip()
    if not question:
        # Say nothing rather than show three empty panels.
        yield (hidden, hidden, hidden, gr.update(), hidden, {})
        return

    reply_lang = answer_lang if answer_lang in ("en", "fr") else ui_lang

    # Where someone studies narrows the question in a way the corpus can use:
    # a number of fiches branch per département, and without the département
    # those branches are never reached.
    place = universities.by_uai(uai) if uai else None
    if place is not None and place.departement:
        question = f"{question} ({place.departement})"

    yield (gr.update(value=render.skeleton(0, ui_lang), visible=True),
           hidden, hidden, gr.update(), hidden, {})

    final = None
    for result in answer_stream(question, language=None if answer_lang == "auto"
                                else answer_lang):
        final = result
        if not result.text:
            yield (gr.update(value=render.skeleton(1, ui_lang), visible=True),
                   hidden, hidden, gr.update(), hidden, {})
            continue
        yield (
            gr.update(value=render.answer_html(result, streaming=True,
                                               lang=reply_lang), visible=True),
            gr.update(value=render.services_html(result.services, ui_lang),
                      visible=bool(result.services)),
            gr.update(value=render.sources_html(result.citations, ui_lang),
                      visible=bool(result.citations)),
            render.debug_html(result, ui_lang),
            shown,
            _context_of(result, question, uai),
        )
    if final is not None:
        yield (
            gr.update(value=render.answer_html(final, streaming=False,
                                               lang=reply_lang), visible=True),
            gr.update(value=render.services_html(final.services, ui_lang),
                      visible=bool(final.services)),
            gr.update(value=render.sources_html(final.citations, ui_lang),
                      visible=bool(final.citations)),
            render.debug_html(final, ui_lang),
            shown,
            _context_of(final, question, uai),
        )


def build() -> gr.Blocks:
    settings = get_settings()
    start = "en"

    with gr.Blocks(title="Sésame — French paperwork, in plain words",
                   analytics_enabled=False) as demo:
        with gr.Row(elem_classes="rp-topbar"):
            head = gr.HTML(brand_block(start))
            site_lang = gr.Radio(
                choices=[(name, code) for code, name in LANGUAGES],
                value=start, show_label=False, container=False,
                elem_classes="rp-switch rp-switch-site",
            )


        with gr.Tabs():
            with gr.Tab("01 · " + t(start, "tab_ask")) as tab_ask:
                question = gr.Textbox(
                    placeholder=t(start, "placeholder"), lines=2,
                    elem_classes="rp-ask", show_label=False,
                )
                with gr.Row(elem_classes="rp-submit-row"):
                    submit = gr.Button(t(start, "submit"), variant="primary",
                                       elem_classes="rp-submit", scale=0)
                    reply_caption = gr.HTML(lang_caption(start, "answer_lang"))
                    reply_lang = gr.Radio(
                        choices=[(t(start, "lang_auto"), "auto"),
                                 ("English", "en"), ("Français", "fr")],
                        value="auto", show_label=False, container=False,
                        elem_classes="rp-switch",
                    )
                with gr.Row(elem_classes="rp-study-row"):
                    study = gr.Dropdown(
                        choices=institution_choices(), value="",
                        label=t(start, "study_label"), filterable=True,
                        elem_classes="rp-study", scale=1,
                    )
                place_box = gr.HTML(visible=False)

                gr.HTML("<div class='rp-hint'><span class='rp-kbd'>\u2318</span>"
                        "<span class='rp-kbd'>K</span> to jump to the question"
                        " \u00b7 <span class='rp-kbd'>Enter</span> to ask</div>")
                try_label = gr.HTML(
                    f"<div class='rp-chip-label'>{html.escape(t(start, 'try'))}</div>")
                with gr.Row(elem_classes="rp-examples"):
                    chips = [gr.Button(text, elem_classes="rp-chip", scale=0)
                             for text in EXAMPLES[start]]

                answer_box = gr.HTML(visible=False)
                services_box = gr.HTML(visible=False)
                sources_box = gr.HTML(visible=False)
                # Off unless DEBUG_PANEL is switched on. It is a developer's
                # view of retrieval, not something a person with a deadline
                # and a form to fill in needs to see.
                with gr.Accordion(t(start, "debug_title"), open=False,
                                  visible=False) as debug_acc:
                    debug_box = gr.HTML()

            with gr.Tab("02 · " + t(start, "tab_glossary")) as tab_gloss:
                gloss_intro = gr.HTML(
                    f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                    f"{html.escape(t(start, 'glossary_intro'))}</p>")
                search = gr.Textbox(placeholder=t(start, "glossary_search"),
                                    show_label=False, elem_classes="rp-gloss-search")
                gloss_box = gr.HTML(render.glossary_html("", start))

            with gr.Tab("03 · " + t(start, "tab_corpus")) as tab_corpus:
                corpus_intro = gr.HTML(
                    f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                    f"{html.escape(t(start, 'corpus_intro'))}</p>")
                corpus_box = gr.HTML(lambda: corpus_html(start))
                refresh = gr.Button(t(start, "refresh"),
                                    elem_classes="rp-chip", scale=0)

        with gr.Accordion(t(start, "report_open"), open=False,
                          elem_classes="rp-report") as report_panel:
            report_intro = gr.HTML(
                f"<p class='rp-report-intro'>{html.escape(t(start, 'report_intro'))}</p>")
            report_text = gr.Textbox(
                placeholder=t(start, "report_placeholder"), lines=4,
                show_label=False, elem_classes="rp-report-text")
            report_keeps = gr.HTML(
                f"<p class='rp-report-keeps'>{html.escape(t(start, 'report_keeps'))}</p>")
            report_send = gr.Button(t(start, "report_send"),
                                    elem_classes="rp-chip", scale=0)
            report_result = gr.HTML(visible=False)

        def send_report(text: str, lang: str, context: dict):
            text = (text or "").strip()
            if not text:
                return (gr.update(
                    value=f"<div class='rp-note'>{html.escape(t(lang, 'report_empty'))}</div>",
                    visible=True), gr.update())
            context = context or {}
            report, path = submit_report(
                text,
                reporter_language=detect(text).language,
                question=context.get("question", ""),
                answer_language=context.get("answer_language", ""),
                refused=context.get("refused"),
                sources=context.get("sources") or [],
                institution=context.get("institution", ""),
            )
            note = "" if report.is_triaged else (
                f"<div class='rp-report-note'>"
                f"{html.escape(t(lang, 'report_untriaged'))}</div>")
            headline = (f"<div class='rp-report-headline'>"
                        f"{html.escape(report.title)}</div>"
                        if report.is_triaged else "")
            body = (
                f"<div class='rp-report-done'>"
                f"<div class='rp-report-thanks'>"
                f"{html.escape(t(lang, 'report_thanks'))}</div>"
                f"{headline}{note}"
                f"<div class='rp-report-path'>"
                f"{html.escape(t(lang, 'report_saved_as'))} "
                f"<code>{html.escape(path.name)}</code></div></div>"
            )
            return gr.update(value=body, visible=True), gr.update(value="")

        # The disclaimer closes the page rather than interrupting it. It sits
        # below every tab and is never dismissible, so it stays permanently
        # visible — it simply no longer stands between someone and the question
        # they came to ask.
        disclaimer = gr.HTML(disclaimer_block(start))

        last_context = gr.State({})
        outputs = [answer_box, services_box, sources_box, debug_box, debug_acc,
                   last_context]
        report_send.click(send_report, [report_text, site_lang, last_context],
                          [report_result, report_text])
        inputs = [question, site_lang, reply_lang, study]
        submit.click(ask, inputs, outputs)
        question.submit(ask, inputs, outputs)
        for chip, text in zip(chips, EXAMPLES[start]):
            chip.click(lambda t=text: t, None, question).then(ask, inputs, outputs)

        def show_place(uai: str, lang: str):
            body = institution_context(uai, lang)
            return gr.update(value=body, visible=bool(body))

        study.change(show_place, [study, site_lang], place_box)
        search.change(render.glossary_html, [search, site_lang], gloss_box)
        refresh.click(corpus_html, site_lang, corpus_box)

        def switch_language(lang: str, current_search: str):
            """Re-label the whole interface without losing what is on screen."""
            return [
                brand_block(lang),
                disclaimer_block(lang),
                lang_caption(lang, "answer_lang"),
                gr.update(choices=[(t(lang, "lang_auto"), "auto"),
                                   ("English", "en"), ("Français", "fr")]),
                gr.update(placeholder=t(lang, "placeholder")),
                gr.update(value=t(lang, "submit")),
                f"<div class='rp-chip-label'>{html.escape(t(lang, 'try'))}</div>",
                *[gr.update(value=text) for text in EXAMPLES[lang]],
                gr.update(label=t(lang, "debug_title")),
                gr.update(label="01 · " + t(lang, "tab_ask")),
                gr.update(label="02 · " + t(lang, "tab_glossary")),
                gr.update(label="03 · " + t(lang, "tab_corpus")),
                gr.update(label=t(lang, "report_open")),
                f"<p class='rp-report-intro'>{html.escape(t(lang, 'report_intro'))}</p>",
                gr.update(placeholder=t(lang, "report_placeholder")),
                f"<p class='rp-report-keeps'>{html.escape(t(lang, 'report_keeps'))}</p>",
                gr.update(value=t(lang, "report_send")),
                f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                f"{html.escape(t(lang, 'glossary_intro'))}</p>",
                gr.update(placeholder=t(lang, "glossary_search")),
                render.glossary_html(current_search or "", lang),
                f"<p class='rp-tagline' style='margin:2px 0 14px'>"
                f"{html.escape(t(lang, 'corpus_intro'))}</p>",
                gr.update(value=t(lang, "refresh")),
                corpus_html(lang),
            ]

        site_lang.change(
            switch_language,
            [site_lang, search],
            [head, disclaimer, reply_caption, reply_lang, question, submit,
             try_label, *chips, debug_acc, tab_ask, tab_gloss, tab_corpus,
             report_panel, report_intro, report_text, report_keeps, report_send,
             gloss_intro, search, gloss_box, corpus_intro, refresh, corpus_box],
        )

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
