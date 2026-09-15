"""Claré — the local interface.

    uv run python -m app.ui.app     →  http://localhost:7860

Two languages are chosen separately, because they are separate questions. The
interface language is what you read the buttons in. The answer language is what
you want the reply written in, and someone may well ask in English and want the
reply in French to forward to a landlord, or the reverse.
"""

from __future__ import annotations

import html
import time
from functools import lru_cache
from pathlib import Path

import gradio as gr

from app.answer.generate import answer_stream
from app.config import get_settings
from app.ingest.embed import fetch_all, get_collection
from app.ingest.fetch import read_manifest
from app.llm import get_chat_provider
from app.query.detect import detect
from app.query import entity
from app.retrieval.answerability import Answerability, classify
from app.answer.from_source import answer_from_source, freshness_key
from app.answer import length as answer_length
from app.answer.validate import validate as validate_answer
from app.query import dates as question_dates
from app.sources import live as live_sources
from app.sources import route as source_route
from app.sources.jurisdiction import resolve as resolve_place
from app.sources.registry import for_entity
from app.ui import brand, render
from app.directory import universities
from app.feedback import submit as submit_report
from app.ui.i18n import LANGUAGES, t

STYLES = (Path(__file__).resolve().parent / "styles.css").read_text(encoding="utf-8")

# Runs once in the page. Only two jobs: copy the French phrasing to the
# clipboard, and give the page its icon.
# Gradio writes the title client-side, so a crawler or a link preview that
# does not run JavaScript sees nothing. These are served with the document.
# Runs once in the page. Written as a plain template with __TOKENS__ rather
# than an f-string: the body is mostly JavaScript and CSS braces, and doubling
# every one of them to survive f-string interpolation is how this file grows
# bugs that only appear in the browser.
_HEAD_TEMPLATE = r"""
<title>Claré — French administration, made clear</title>
<meta name="description" content="Understand French administrative
 procedures in plain English or French. Every answer comes from an official
 government page, with the link and the date it was last updated.">
<meta name="robots" content="index, follow">
<meta property="og:type" content="website">
<meta property="og:title" content="Claré — French administration, made clear">
<meta property="og:description" content="Understand what to do, what you need,
 and where to go. Answers from official French government sources only.">
<meta property="og:locale" content="en_GB">
<meta property="og:locale:alternate" content="fr_FR">
<meta name="twitter:card" content="summary">
<meta name="theme-color" content="#faf8f5">
<link rel="icon" type="image/svg+xml" href="__FAVICON__">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<!-- Public Sans is drawn for government use and stays legible at small sizes
     for someone reading their second language; Newsreader carries the
     headline and nothing else. -->
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Public+Sans:ital,wght@0,400;0,500;0,600;0,700;1,400&family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=JetBrains+Mono:wght@400;600&display=swap">
<script>
(() => {
  // --- metadata ------------------------------------------------------------
  // Gradio writes its own og:title of "Gradio" into the document. Ours is
  // served too, but two of the same tag is ambiguous to a link preview, so
  // the placeholder is removed once the page is up.
  ["og:title", "og:description"].forEach((property) => {
    const tags = [...document.querySelectorAll(`meta[property="${property}"]`)];
    const real = tags.filter((tag) =>
      (tag.getAttribute("content") || "").trim() !== "Gradio");
    tags.forEach((tag) => { if (!real.includes(tag)) tag.remove(); });
    real.slice(1).forEach((tag) => tag.remove());
  });

  // --- document language ---------------------------------------------------
  // Screen readers pronounce from the document's lang; leaving it as the
  // page's default makes a French interface read in an English voice.
  const isFrench = () => !!document.querySelector(".rp-theme-fr");
  const syncLang = () => {
    const want = isFrench() ? "fr" : "en";
    if (document.documentElement.lang !== want)
      document.documentElement.lang = want;
  };

  // --- skip link -----------------------------------------------------------
  // Both labels come from the translation table; the old version chose
  // between two hard-coded English/French strings here.
  const SKIP = { en: "__SKIP_EN__", fr: "__SKIP_FR__" };
  const addSkip = () => {
    let link = document.querySelector(".rp-skip");
    if (!link) {
      if (!document.querySelector("#rp-question textarea")) return;
      link = document.createElement("a");
      link.className = "rp-skip";
      link.href = "#rp-question";
      link.addEventListener("click", (event) => {
        event.preventDefault();
        const field = document.querySelector("#rp-question textarea");
        if (field) field.focus();
      });
      document.body.prepend(link);
    }
    const label = isFrench() ? SKIP.fr : SKIP.en;
    if (link.textContent !== label) link.textContent = label;
  };

  // Everything here is delegated from the document, because Gradio replaces
  // chunks of the page on every interaction and anything bound to an element
  // directly would be lost the first time an answer arrives.

  const writeClipboard = async (text) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch (error) {
      const area = document.createElement("textarea");
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
  };

  const flash = (node, label, done) => {
    const original = label.textContent;
    label.textContent = node.dataset.done || "Copied";
    node.classList.add("rp-done");
    setTimeout(() => {
      label.textContent = original;
      node.classList.remove("rp-done");
    }, 1600);
  };

  document.addEventListener("click", async (event) => {
    // --- copy the suggested French phrasing --------------------------------
    const copy = event.target.closest("[data-copy]");
    if (copy) {
      const block = copy.closest(".rp-say");
      if (block) {
        await writeClipboard([...block.querySelectorAll("p")]
          .filter((p) => !p.classList.contains("rp-say-note"))
          .map((p) => p.innerText.trim())
          .join("\n"));
        flash(copy, copy);
      }
      return;
    }

    // --- copy the whole answer ---------------------------------------------
    const copyAnswer = event.target.closest("[data-copy-answer]");
    if (copyAnswer) {
      const answer = copyAnswer.closest(".rp-reply").querySelector(".rp-answer");
      if (answer) {
        await writeClipboard(answer.innerText.trim());
        flash(copyAnswer, copyAnswer.querySelector("span"));
      }
      return;
    }

    // --- was it any use ----------------------------------------------------
    const vote = event.target.closest("[data-vote]");
    if (vote) {
      const row = vote.closest(".rp-actions");
      row.querySelectorAll("[data-vote]").forEach((button) => {
        button.classList.remove("rp-done");
        button.disabled = true;
      });
      vote.classList.add("rp-done");
      const thanks = row.querySelector(".rp-action-thanks");
      if (thanks) thanks.hidden = false;
      return;
    }

    // --- open a source in place --------------------------------------------
    // The arrow opens the official page; anywhere else shows the passage the
    // answer actually drew on, so the reader can check it without leaving.
    if (event.target.closest(".rp-source-link")) return;
    const card = event.target.closest("[data-expandable]");
    if (card) card.classList.toggle("rp-open");
  });

  // --- a category is a question --------------------------------------------
  // Svelte binds the field, so setting .value alone is invisible to it; the
  // input event is what makes the change real.
  document.addEventListener("click", (event) => {
    const category = event.target.closest("[data-question]");
    if (!category) return;
    const field = document.querySelector("#rp-question textarea");
    const button = document.querySelector("button.rp-submit");
    if (!field) return;
    field.value = category.dataset.question;
    field.dispatchEvent(new Event("input", { bubbles: true }));
    requestAnimationFrame(() => button && button.click());
  });

  // --- the composer knows whether it has anything to send -------------------
  const syncComposer = () => {
    const field = document.querySelector("#rp-question textarea");
    const composer = document.querySelector(".rp-composer");
    if (field && composer)
      composer.classList.toggle("rp-ready", field.value.trim().length > 0);
  };
  document.addEventListener("input", (event) => {
    if (event.target.closest("#rp-question")) syncComposer();
  });

  // --- keyboard ------------------------------------------------------------
  // Enter sends, Shift+Enter breaks the line. Gradio only wires Enter on a
  // single-line box, and the composer is deliberately several lines tall.
  document.addEventListener("keydown", (event) => {
    const field = document.querySelector("#rp-question textarea");
    if (event.target === field && event.key === "Enter" && !event.shiftKey
        && !event.metaKey && !event.ctrlKey && !event.altKey
        && !event.isComposing) {
      const button = document.querySelector("button.rp-submit");
      if (field.value.trim() && button) {
        event.preventDefault();
        button.click();
        return;
      }
    }
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (field) { field.focus(); field.select(); }
    }
    if (event.key === "Escape") {
      if (document.activeElement === field) { field.blur(); return; }
      // Escape closes the report panel, the way a dialog would.
      const report = document.querySelector(".rp-report > .label-wrap.open");
      if (report) report.click();
    }
  });

  // --- the report control --------------------------------------------------
  // The button carries only the bug glyph. The label Gradio renders stays in
  // the DOM, clipped, so a screen reader still has it; the tooltip and the
  // accessible name are set from the same translation table the rest of the
  // interface uses, rather than being written twice in here.
  const REPORT = { en: "__REPORT_EN__", fr: "__REPORT_FR__" };
  const dressReport = () => {
    const label = document.querySelector(".rp-report > .label-wrap");
    if (!label) return;
    const text = isFrench() ? REPORT.fr : REPORT.en;
    if (label.getAttribute("aria-label") !== text) {
      label.setAttribute("aria-label", text);
      label.setAttribute("data-tip", text);
    }
  };

  // Cancel closes the sheet by pressing the control that opened it, so there
  // is one open/close path rather than two that can disagree.
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".rp-report-cancel")) return;
    const label = document.querySelector(".rp-report > .label-wrap");
    if (label) label.click();
  });

  // --- the conditions a report happened under ------------------------------
  // A user-agent string and a window size. No cookies, no storage, nothing
  // that identifies a person — and only ever read when a report is sent.
  const setHidden = (id, value) => {
    const field = document.querySelector("#" + id + " textarea, #" + id + " input");
    if (!field || field.value === value) return;
    field.value = value;
    field.dispatchEvent(new Event("input", { bubbles: true }));
  };
  const captureContext = () => {
    setHidden("rp-browser", navigator.userAgent || "");
    setHidden("rp-viewport", window.innerWidth + "x" + window.innerHeight);
  };
  window.addEventListener("resize", captureContext, { passive: true });

  // --- landing or conversation ---------------------------------------------
  // One class on the body; the whole layout change hangs off it in CSS
  // rather than off a second set of Python-side visibility flags.
  let turnCount = 0;
  const answered = () => {
    const turns = document.querySelectorAll(".rp-thread .rp-turn");
    document.body.classList.toggle("rp-answered", turns.length > 0);
    if (turns.length > turnCount) {
      turnCount = turns.length;
      const latest = turns[turns.length - 1];
      if (latest) requestAnimationFrame(() =>
        latest.scrollIntoView({ behavior: "smooth", block: "start" }));
    } else if (turns.length < turnCount) {
      turnCount = turns.length;
    }
  };

  // --- the connection notice -----------------------------------------------
  // Gradio's stock wording ("Connection to the server was lost") reads like
  // something has gone badly wrong, to an audience already braced for bad
  // news. Same information, said the way a person would.
  const NOTICES = {
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
  };
  const STOCK = [
    "connection to the server was lost",
    "attempting reconnection",
    "connection errored out",
    "reconnecting"
  ];

  const softenNotices = () => {
    const french = isFrench();
    const lines = french ? NOTICES.fr : NOTICES.en;
    document.querySelectorAll(".toast-body, .toast-text, [class*='toast']")
      .forEach((node) => {
        if (node.dataset.rpSoftened) return;
        const said = (node.textContent || "").toLowerCase();
        if (!STOCK.some((phrase) => said.includes(phrase))) return;
        const title = node.querySelector(".toast-title, [class*='title']");
        if (title) title.textContent = french ? "Un instant" : "One moment";
        const body = node.querySelector(".toast-text, p") || node;
        body.textContent = lines[Math.floor(Math.random() * lines.length)];
        node.dataset.rpSoftened = "1";
        node.classList.add("rp-notice");
      });
  };

  // --- reveal sources as they come into view -------------------------------
  const watcher = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add("rp-in");
        watcher.unobserve(entry.target);
      }
    });
  }, { rootMargin: "0px 0px -40px 0px", threshold: 0.05 });

  const watch = () => {
    syncLang();
    addSkip();
    dressReport();
    captureContext();
    softenNotices();
    syncComposer();
    answered();
    document.querySelectorAll(".rp-source:not(.rp-reveal), .rp-gloss:not(.rp-reveal)")
      .forEach((node) => { node.classList.add("rp-reveal"); watcher.observe(node); });
  };

  new MutationObserver(watch).observe(document.documentElement,
    { childList: true, subtree: true });
  watch();
})();
</script>
"""

HEAD = (
    _HEAD_TEMPLATE
    .replace("__FAVICON__", brand.FAVICON)
    .replace("__SKIP_EN__", t("en", "skip"))
    .replace("__SKIP_FR__", t("fr", "skip"))
    .replace("__REPORT_EN__", t("en", "report_open"))
    .replace("__REPORT_FR__", t("fr", "report_open"))
)


def brand_block(lang: str) -> str:
    """Wordmark, positioning line, and the marker the palette keys off.

    Left edge of the container, and nothing else on this side. The language
    control is a separate component pinned to the right of the same row, so
    the two sit on one line and the header has an actual left and right.
    """
    return f"""
<span class="rp-theme rp-theme-{lang}"></span>
<div class="rp-masthead">
  {brand.wordmark()}
  <p class="rp-promise">{html.escape(t(lang, 'promise'))}</p>
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
    return (f'<p class="rp-privacy">'
            f'<strong>{html.escape(t(lang, "privacy_lead"))}</strong> '
            f'{html.escape(body)}</p>')


def credit_block(lang: str) -> str:
    """Who made it, and the invitation.

    The gaps in this thing are not gaps in the code — they are the things no
    official page will ever say (which bank actually accepts which paper, how
    long a prefecture really takes). Only the people who have just been
    through it know those, so the footer asks.
    """
    return f"""
<div class="rp-credit">
  <p class="rp-credit-community">{html.escape(t(lang, 'community'))}</p>
  <p class="rp-credit-made">
    <span>{html.escape(t(lang, 'made_by'))}</span>
    <span class="rp-credit-year">{html.escape(t(lang, 'made_year'))}</span>
  </p>
</div>
"""


def disclaimer_block(lang: str) -> str:
    """The two things a reader is owed, and who made it.

    These were three separate bordered notices stacked down the page, each
    shouting at the same volume as the answer above them. They are footnotes.
    They are now set as footnotes.
    """
    return f"""
<footer class="rp-footer">
  <div class="rp-footer-brand">
    <span class="rp-footer-name">Clar<span class="rp-accent">&#233;</span></span>
    <span class="rp-footer-promise">{html.escape(t(lang, 'promise'))}</span>
  </div>
  <div class="rp-footer-notes">
    {privacy_block(lang)}
    <p class="rp-disclaimer">
      <strong>{html.escape(t(lang, 'disclaimer_lead'))}</strong>
      {html.escape(t(lang, 'disclaimer_body'))}
    </p>
  </div>
  {credit_block(lang)}
</footer>
"""


def hero_block(lang: str) -> str:
    """The question, asked of the person, on the left edge of the page.

    A landing page that opens with a statement about itself asks the reader to
    care about the product first. Opening with their question puts the cursor
    where their attention already is.

    Left-aligned, not centred: the masthead, the headline, the composer and
    the topic list then share one left edge, and the page reads as a column
    someone set rather than as a stack of independently centred blocks.
    """
    return f"""
<header class="rp-hero rp-stage-in">
  <p class="rp-eyebrow">{html.escape(t(lang, 'eyebrow'))}</p>
  <h1 class="rp-headline">{html.escape(t(lang, 'headline'))}</h1>
  <p class="rp-tagline">{html.escape(t(lang, 'subhead'))}</p>
</header>
"""


def trust_block(lang: str) -> str:
    """One quiet line under the composer, where the doubt actually lands.

    Not a badge and not a seal: this claims nothing about who we are, only
    what every answer carries with it.
    """
    return f"""
<p class="rp-trust">
  <svg width="14" height="14" viewBox="0 0 16 16" fill="none" aria-hidden="true">
    <path d="M8 1.7 3 3.8v3.4c0 2.9 2.1 5.6 5 6.6 2.9-1 5-3.7 5-6.6V3.8L8 1.7Z"
          stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/>
    <path d="M5.9 8.1 7.3 9.5 10.2 6.5" stroke="currentColor"
          stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
  </svg>
  <span>{html.escape(t(lang, 'trust'))}</span>
</p>
"""


def lang_caption(lang: str, key: str) -> str:
    return (f"<div class='rp-lang-label' title=\""
            f"{html.escape(t(lang, 'lang_auto_help'), quote=True)}\">"
            f"{brand.GLOBE}{html.escape(t(lang, key))}</div>")


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


@lru_cache(maxsize=8)
def _provider_status(_bucket: int) -> tuple[bool, str]:
    """Provider reachability, asked at most once every few minutes.

    Rendering a panel should not cost a network round trip, and it certainly
    should not cost one per language switch.
    """
    try:
        return get_chat_provider().health()
    except Exception as exc:  # a status panel must never take the page down
        return False, str(exc)


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
    ok, _ = _provider_status(int(time.time()) // 300)
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


# Words that mean someone is asking as a student. Matched on the folded
# tokens, so accents, case and elision do not matter.
STUDY_SIGNALS = frozenset("""
student students study studies studying university college campus school
enrol enroll enrolment enrollment tuition scholarship grant diploma degree
master bachelor phd doctorate internship erasmus
etudiant etudiante etudiants etude etudes universite fac faculte ecole
scolarite inscription bourse diplome licence doctorat stage
crous cvec parcoursup
""".split())


def looks_like_study(question: str) -> bool:
    """Is this person asking as a student?

    Decided from their own words rather than guessed at, because the picker
    only earns its place when the answer would actually change.
    """
    from app.retrieval.keyword import tokenize

    return bool(STUDY_SIGNALS & set(tokenize(question)))


def _context_of(result, question: str, uai: str) -> dict:
    """What a bug report should carry: enough to reproduce, nothing about who."""
    place = universities.by_uai(uai) if uai else None
    return {
        "question": question,
        "answer_language": result.language,
        "refused": result.refused,
        "sources": [c.fiche_id for c in result.citations],
        "institution": place.name if place else "",
    }


def store_audit_validation(checked, question: str) -> None:
    """Record an answer that failed its own checks, so it can be found later.

    Never silent: a mismatch that is only logged is a mismatch that keeps
    happening, and these are precisely the failures that read like successes.
    """
    from app.sources import store as source_store
    source_store.audit("answer.validation_failed", None,
                       question=question[:200],
                       failed=[f.check for f in checked.failures],
                       detail=checked.why()[:300])


def ask(question: str, ui_lang: str, answer_lang: str, uai: str = "",
        turns: list | None = None):
    """Answer a question inside the conversation, streaming as it is written.

    Yields, in order: the thread with the question showing and a thinking
    state, then the same thread with the answer filling in, then the finished
    turn with its sources and controls.
    """
    hidden = gr.update(visible=False)
    turns = list(turns or [])
    question = (question or "").strip()
    reply_lang = answer_lang if answer_lang in ("en", "fr") else ui_lang

    if not question:
        yield (gr.update(), gr.update(), gr.update(), gr.update(),
               hidden, hidden, turns, {}, gr.update())
        return

    previous = turns[-1]["question"] if turns else None

    # Who is this about? A question leaning on "this school" is not answerable
    # until something has named one, and retrieving on the words that are left
    # returns official pages that were never about it.
    history = [turn.get("question", "") for turn in turns]
    who = entity.resolve(question, history)
    if classify(who, refused=False, citations=0) is Answerability.NEEDS_CLARIFICATION:
        turns.append({"question": question, "state": "clarify",
                      "result": None, "streaming": False,
                      "surface": who.surface})
        yield (gr.update(value=render.thread_html(turns, ui_lang, ui_lang),
                         visible=True),
               hidden, hidden, gr.update(), gr.update(), hidden, turns, {},
               gr.update(value="", placeholder=t(ui_lang, "followup_ph")))
        return

    # Carried into the turn so a refusal can name the body that owns the
    # answer instead of offering the nearest public-administration page.
    about = None
    live_source = None
    if who.institution is not None:
        about = {"name": who.institution.name,
                 "commune": who.institution.commune,
                 "departement": who.institution.departement,
                 "url": who.institution.url}
        # Is this body's own website a registered, verified source we may
        # read? The lookup is local and costs nothing; only a hit leads to
        # any network access at all.
        for candidate_id in entity.canonical_ids(who.institution):
            if any(s.live_query_enabled and s.verified
                   for s in for_entity(candidate_id)):
                live_source = candidate_id
                break

    # Where someone studies narrows the question in a way the corpus can use:
    # a number of fiches branch per departement.
    place = universities.by_uai(uai) if uai else None
    asked = f"{question} ({place.departement})" if place and place.departement else question
    if who.from_context and who.institution is not None:
        # "this school" carries nothing into a search; the name does.
        asked = f"{asked} ({who.institution.name})"

    turns.append({"question": question, "state": "thinking",
                  "result": None, "streaming": False, "institution": about})


    def thread():
        return gr.update(value=render.thread_html(turns, ui_lang, reply_lang),
                         visible=True)

    # A reply to a clarification completes the question it answered, rather
    # than replacing it. "I live in Montpellier." on its own is about nothing;
    # after "what do I need to renew my residence permit?" it is the rest of
    # that question, and routing has to see both.
    # turns[-1] is the turn just appended for this question, so the one that
    # asked the clarification is the one before it.
    routing_question = question
    previous_turn = turns[-2] if len(turns) >= 2 else None
    if previous_turn and previous_turn.get("state") in ("clarify", "clarify_place"):
        routing_question = f"{previous_turn.get('question', '')} {question}".strip()

    # Which authority actually speaks to this question? An institution's own
    # rule, a préfecture's counter, a national body — or none of them, in
    # which case the corpus answers as before.
    place = resolve_place(
        " ".join([*history, question]),
        hint_department=(who.institution.departement
                         if who.institution is not None else ""))
    routing = source_route.plan(routing_question, entity_id=live_source or "",
                                place=place)

    if routing.needs_place:
        # The answer genuinely differs by préfecture. Averaging the country
        # here is how somebody arrives at a counter with the wrong folder.
        turns[-1] = {**turns[-1], "state": "clarify_place", "result": None}
        yield (thread(), hidden, hidden, gr.update(), gr.update(), hidden,
               turns, {},
               gr.update(value="", placeholder=t(ui_lang, "followup_ph")))
        return

    if routing.live_steps:
        yield (thread(), hidden, hidden, gr.update(), gr.update(),
               gr.update(visible=False), turns, {},
               gr.update(value="", placeholder=t(ui_lang, "followup_ph")))

        found = live_sources.gather_plan(routing, routing_question, per_source=2)
        if found.ok:
            # Last check before anyone reads it: is this evidence actually
            # evidence for *this* question? A Montpellier question answered
            # from the Rhône préfecture reads exactly like a good answer.
            asked_about = question_dates.parse(routing_question)
            checked = validate_answer(
                found.evidence,
                entity_source_id=(routing.steps[0].source.id
                                  if routing.steps and routing.steps[0].is_institution
                                  else ""),
                expected_area=(place.department.name
                               if place.known and place.department else ""),
                on=asked_about.on)
            if not checked.ok:
                store_audit_validation(checked, routing_question)

            answered = answer_from_source(routing_question, found,
                                          language=reply_lang)
            # Measured, never truncated: cutting at a word count is how a
            # condition that changes who qualifies gets dropped.
            length = answer_length.check(routing_question, answered.text)
            if length.over:
                from app.sources import store as source_store
                source_store.audit("answer.too_long", None,
                                   question=routing_question[:160],
                                   answer_class=length.answer_class.value,
                                   words=length.words, ceiling=length.ceiling)
            local = found.local_source
            turns[-1] = {"question": question, "state": "done",
                         "result": answered, "streaming": False,
                         "institution": about,
                         "freshness": freshness_key(found),
                         "live_source_name": found.source.name if found.source else "",
                         "local_authority": local.name if local is not None else "",
                         "authority_unverified": (routing.unreachable_deciders[0]
                                                  if routing.unreachable_deciders
                                                  else ""),
                         "live_domain": found.source.domain if found.source else "",
                         "source_versions": found.source_versions}
            yield (thread(), hidden, hidden, gr.update(),
                   gr.update(visible=get_settings().debug_panel),
                   gr.update(visible=False), turns,
                   _context_of(answered, question, uai), gr.update())
            return

        if routing.live_steps and not routing.fall_back_to_corpus:
            # The body that owns this answer exists and could not be read.
            # Saying so beats answering from something that does not own it.
            authority = routing.live_steps[0].source.name
            turns[-1] = {**turns[-1], "state": "authority_down",
                         "result": None, "authority": authority}
            yield (thread(), hidden, hidden, gr.update(), gr.update(), hidden,
                   turns, {}, gr.update())
            return
        # Otherwise fall through: the corpus is a legitimate answer for this
        # topic, and it is better than nothing.
        turns[-1] = {**turns[-1], "freshness": freshness_key(found)}

    offer_study = gr.update(visible=looks_like_study(question) and not uai)
    # The landing copy steps aside once there is a conversation to read.
    yield (thread(), hidden, hidden, gr.update(), gr.update(), offer_study,
           turns, {},
           gr.update(value="", placeholder=t(ui_lang, "followup_ph")))

    final = None
    for result in answer_stream(asked, language=None if answer_lang == "auto"
                                else answer_lang, previous_question=previous):
        final = result
        turns[-1] = {"question": question,
                     "state": "answering" if result.text else "thinking",
                     "result": result if result.text else None,
                     "streaming": True, "institution": about}
        yield (thread(), hidden, hidden, render.debug_html(result, ui_lang),
               gr.update(), offer_study, turns,
               _context_of(result, question, uai), gr.update())

    if final is not None:
        turns[-1] = {"question": question, "state": "done",
                     "result": final, "streaming": False, "institution": about}
        yield (thread(), hidden, hidden, render.debug_html(final, ui_lang),
               gr.update(visible=get_settings().debug_panel), offer_study,
               turns, _context_of(final, question, uai), gr.update())


def reset_thread(ui_lang: str):
    """Back to the landing state, with nothing left over.

    "Nothing left over" now includes the institution panel and the study
    prompt, which used to survive a restart and sit under a blank page.
    """
    hidden = gr.update(visible=False)
    return (gr.update(value="", visible=False), gr.update(visible=True),
            gr.update(visible=True), [], hidden, hidden, hidden,
            gr.update(value="", placeholder=t(ui_lang, "placeholder")))


def build() -> gr.Blocks:
    settings = get_settings()
    start = "en"

    with gr.Blocks(title="Claré — French administration, made clear",
                   analytics_enabled=False) as demo:
        # Masthead: brand hard left, language hard right, one row.
        with gr.Row(elem_classes="rp-topbar"):
            head = gr.HTML(brand_block(start))
            site_lang = gr.Radio(
                choices=[(name, code) for code, name in LANGUAGES],
                value=start, show_label=False, container=False,
                elem_classes="rp-switch rp-switch-site",
            )

        # Three sections, named rather than numbered. They were "01 · Ask",
        # "02 · Words", "03 · Sources", which framed a conversation as a form
        # to be completed in order. Words and Sources are real pages with real
        # content, so they stay — as a contents bar, not as steps.
        with gr.Tabs(elem_classes="rp-sections"):
            with gr.Tab(t(start, "tab_ask")) as tab_ask:
                # Source order is the layout in both states: the landing
                # hides the thread, the conversation hides the hero and the
                # topic list, and the composer sits under whichever is showing
                # — a headline on arrival, the last answer afterwards.
                hero = gr.HTML(hero_block(start))

                restart = gr.Button(t(start, "new_question"),
                                    elem_classes="rp-restart", scale=0)

                thread_box = gr.HTML(visible=False)

                with gr.Column(elem_classes="rp-composer"):
                    question = gr.Textbox(
                        placeholder=t(start, "placeholder"), lines=3,
                        max_lines=8, elem_classes="rp-ask", show_label=False,
                        elem_id="rp-question",
                    )
                    # The send control lives inside the composer's own footer
                    # row rather than as a separate block underneath it, so
                    # the question and the act of asking are one object.
                    with gr.Row(elem_classes="rp-composer-bar"):
                        reply_caption = gr.HTML(lang_caption(start, "answer_lang"))
                        reply_lang = gr.Radio(
                            choices=[(t(start, "lang_auto"), "auto"),
                                     ("English", "en"), ("Français", "fr")],
                            value="auto", show_label=False, container=False,
                            elem_classes="rp-switch rp-switch-reply",
                        )
                        submit = gr.Button(t(start, "submit"), variant="primary",
                                           elem_classes="rp-submit", scale=0)

                trust = gr.HTML(trust_block(start))

                cats = gr.HTML(render.categories_html(start))

                # Offered when the question is about studying, and not before.
                with gr.Column(visible=False, elem_classes="rp-study-ask") as study_ask:
                    study_why = gr.HTML(
                        f"<p class='rp-study-prompt'>"
                        f"{html.escape(t(start, 'study_prompt'))}</p>")
                    study_search = gr.Textbox(
                        placeholder=t(start, "study_placeholder"),
                        show_label=False, elem_classes="rp-study-search")
                    study = gr.Radio(
                        choices=[], value=None, show_label=False,
                        container=False, elem_classes="rp-study-hits")
                place_box = gr.HTML(visible=False)

                # A developer's view of retrieval, off unless switched on.
                with gr.Accordion(t(start, "debug_title"), open=False,
                                  visible=False) as debug_acc:
                    debug_box = gr.HTML()

            with gr.Tab(t(start, "tab_glossary")) as tab_gloss:
                gloss_intro = gr.HTML(
                    f"<p class='rp-section-intro'>"
                    f"{html.escape(t(start, 'glossary_intro'))}</p>")
                search = gr.Textbox(placeholder=t(start, "glossary_search"),
                                    show_label=False, elem_classes="rp-gloss-search")
                gloss_box = gr.HTML(render.glossary_html("", start))

            with gr.Tab(t(start, "tab_corpus")) as tab_corpus:
                corpus_intro = gr.HTML(
                    f"<p class='rp-section-intro'>"
                    f"{html.escape(t(start, 'corpus_intro'))}</p>")
                corpus_box = gr.HTML()
                refresh = gr.Button(t(start, "refresh"),
                                    elem_classes="rp-chip", scale=0)

        with gr.Accordion(t(start, "report_open"), open=False,
                          elem_classes="rp-report") as report_panel:
            report_intro = gr.HTML(
                f"<p class='rp-report-intro'>{html.escape(t(start, 'report_intro'))}</p>")
            report_text = gr.Textbox(
                placeholder=t(start, "report_placeholder"), lines=4,
                show_label=False, elem_classes="rp-report-text")
            report_doing_label = gr.HTML(
                f"<p class='rp-report-field'>{html.escape(t(start, 'report_doing'))}</p>")
            report_doing = gr.Textbox(
                placeholder=t(start, "report_doing_placeholder"), lines=2,
                show_label=False, elem_classes="rp-report-text")
            report_keeps = gr.HTML(
                f"<p class='rp-report-keeps'>{html.escape(t(start, 'report_keeps'))}</p>")
            with gr.Row(elem_classes="rp-report-actions"):
                report_cancel = gr.Button(t(start, "report_cancel"),
                                          elem_classes="rp-report-cancel", scale=0)
                report_send = gr.Button(t(start, "report_send"),
                                        elem_classes="rp-chip rp-report-send", scale=0)
            report_result = gr.HTML(visible=False)

        def send_report(text: str, doing: str, lang: str, context: dict,
                        browser: str, viewport: str):
            """Store the report, then say exactly what happened to it.

            Three outcomes, three different sentences: stored and sent, stored
            but the notification failed, or simply stored because no mail is
            configured. Saying "sent" for the last two would be the easiest
            lie in the product and the one that costs a reporter the most.
            """
            text = (text or "").strip()
            if not text:
                return (gr.update(
                    value=f"<div class='rp-note'>{html.escape(t(lang, 'report_empty'))}</div>",
                    visible=True), gr.update(), gr.update())
            context = context or {}
            report, path = submit_report(
                text,
                reporter_language=detect(text).language,
                question=context.get("question", ""),
                answer_language=context.get("answer_language", ""),
                refused=context.get("refused"),
                sources=context.get("sources") or [],
                institution=context.get("institution", ""),
                what_doing=doing or "",
                browser=(browser or "")[:300],
                viewport=(viewport or "")[:40],
                conversation_context=context.get("conversation", ""),
            )

            if report.emailed:
                thanks = t(lang, "report_emailed")
                delivery = ""
            elif report.email_error:
                thanks = t(lang, "report_thanks")
                delivery = (f"<div class='rp-report-note'>"
                            f"{html.escape(t(lang, 'report_email_failed'))}</div>")
            else:
                thanks = t(lang, "report_thanks")
                delivery = ""

            note = "" if report.is_triaged else (
                f"<div class='rp-report-note'>"
                f"{html.escape(t(lang, 'report_untriaged'))}</div>")
            headline = (f"<div class='rp-report-headline'>"
                        f"{html.escape(report.title)}</div>"
                        if report.is_triaged else "")
            body = (
                f"<div class='rp-report-done'>"
                f"<div class='rp-report-thanks'>{html.escape(thanks)}</div>"
                f"{headline}{note}{delivery}"
                f"<div class='rp-report-path'>"
                f"{html.escape(t(lang, 'report_saved_as'))} "
                f"<code>{html.escape(report.bug_id or path.name)}</code></div></div>"
            )
            return (gr.update(value=body, visible=True),
                    gr.update(value=""), gr.update(value=""))

        # Filled by the page so a report carries the conditions it happened
        # under. A user-agent string and a window size — no cookies, no
        # storage, nothing that identifies a person. These are hidden in CSS
        # rather than with visible=False, because Gradio does not render an
        # invisible component into the DOM at all and the page could not
        # reach it.
        report_browser = gr.Textbox(show_label=False, container=False,
                                    elem_id="rp-browser",
                                    elem_classes="rp-offscreen")
        report_viewport = gr.Textbox(show_label=False, container=False,
                                     elem_id="rp-viewport",
                                     elem_classes="rp-offscreen")

        # The disclaimer closes the page rather than interrupting it. It sits
        # below every tab and is never dismissible, so it stays permanently
        # visible — it simply no longer stands between someone and the question
        # they came to ask.
        disclaimer = gr.HTML(disclaimer_block(start))

        last_context = gr.State({})
        turns_state = gr.State([])
        outputs = [thread_box, hero, cats, debug_box, debug_acc, study_ask,
                   turns_state, last_context, question]
        report_send.click(
            send_report,
            [report_text, report_doing, site_lang, last_context,
             report_browser, report_viewport],
            [report_result, report_text, report_doing])
        inputs = [question, site_lang, reply_lang, study, turns_state]
        submit.click(ask, inputs, outputs)
        question.submit(ask, inputs, outputs)
        restart.click(reset_thread, site_lang,
                      [thread_box, hero, cats, turns_state, debug_acc,
                       study_ask, place_box, question])

        def suggest(query: str):
            """Live matches, previewed in place.

            Nine thousand institutions do not belong in a dropdown, and a
            picker whose resting state reads "Not a student" asks everyone to
            deny being one. Typing is the interface.
            """
            hits = universities.search(query or "", limit=6)
            if not hits:
                return gr.update(choices=[], value=None)
            return gr.update(
                choices=[(f"{h.name} · {h.commune} · {h.departement}", h.uai)
                         for h in hits],
                value=None,
            )

        def show_place(uai: str, lang: str):
            body = institution_context(uai, lang)
            return gr.update(value=body, visible=bool(body))

        study_search.change(suggest, study_search, study)
        study.change(show_place, [study, site_lang], place_box)
        # Built when the tab is opened. It reads every chunk's metadata, so
        # rebuilding it on each language change froze the whole page.
        tab_corpus.select(corpus_html, site_lang, corpus_box)
        search.change(render.glossary_html, [search, site_lang], gloss_box)
        refresh.click(corpus_html, site_lang, corpus_box)

        def switch_language(lang: str, current_search: str, turns: list):
            """Re-label the whole interface without losing what is on screen.

            Every user-facing string is re-read from the translation table, so
            nothing can be left behind in the other language — including the
            conversation already on screen, whose labels, freshness note and
            controls are chrome and must follow the interface.

            The answers themselves are not re-rendered into the new language.
            They were written from sources in a particular language, and
            translating them here would be inventing a text nobody wrote.
            """
            turns = turns or []
            return [
                brand_block(lang),
                hero_block(lang),
                disclaimer_block(lang),
                lang_caption(lang, "answer_lang"),
                gr.update(choices=[(t(lang, "lang_auto"), "auto"),
                                   ("English", "en"), ("Français", "fr")]),
                gr.update(placeholder=t(lang, "placeholder")),
                gr.update(value=t(lang, "submit")),
                gr.update(value=t(lang, "new_question")),
                render.categories_html(lang),
                trust_block(lang),
                gr.update(label=t(lang, "debug_title")),
                gr.update(label=t(lang, "tab_ask")),
                gr.update(label=t(lang, "tab_glossary")),
                gr.update(label=t(lang, "tab_corpus")),
                f"<p class='rp-study-prompt'>"
                f"{html.escape(t(lang, 'study_prompt'))}</p>",
                gr.update(placeholder=t(lang, "study_placeholder")),
                gr.update(label=t(lang, "report_open")),
                f"<p class='rp-report-intro'>{html.escape(t(lang, 'report_intro'))}</p>",
                gr.update(placeholder=t(lang, "report_placeholder")),
                f"<p class='rp-report-field'>{html.escape(t(lang, 'report_doing'))}</p>",
                gr.update(placeholder=t(lang, "report_doing_placeholder")),
                f"<p class='rp-report-keeps'>{html.escape(t(lang, 'report_keeps'))}</p>",
                gr.update(value=t(lang, "report_cancel")),
                gr.update(value=t(lang, "report_send")),
                f"<p class='rp-section-intro'>"
                f"{html.escape(t(lang, 'glossary_intro'))}</p>",
                gr.update(placeholder=t(lang, "glossary_search")),
                render.glossary_html(current_search or "", lang),
                f"<p class='rp-section-intro'>"
                f"{html.escape(t(lang, 'corpus_intro'))}</p>",
                gr.update(value=t(lang, "refresh")),
                gr.update(value=render.thread_html(turns, lang, lang),
                          visible=bool(turns)),
            ]

        site_lang.change(
            switch_language,
            [site_lang, search, turns_state],
            [head, hero, disclaimer, reply_caption, reply_lang, question,
             submit, restart, cats, trust, debug_acc, tab_ask, tab_gloss,
             tab_corpus, study_why, study_search, report_panel, report_intro,
             report_text, report_doing_label, report_doing, report_keeps,
             report_cancel, report_send, gloss_intro, search,
             gloss_box, corpus_intro, refresh, thread_box],
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
