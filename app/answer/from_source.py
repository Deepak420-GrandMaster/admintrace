"""Answering from an institution's own website.

Same discipline as the corpus path, different evidence. The model is handed
pages that were fetched from one registered domain, told to use nothing else,
and told to say so when those pages do not answer the question. What changes
is only where the passages came from — not whether an answer has to come from
passages at all.

The citation carries the page's canonical URL and the time it was read, so a
reader can check the claim against the same wording we saw. A live source has
no equivalent of the corpus's published "last updated" date unless the page
states one, and this never invents one: when the page is silent, the citation
says when *we* read it and nothing more.
"""

from __future__ import annotations

import time

from app.answer import prompts
from app.answer.cite import Citation
from app.answer.generate import AnswerResult
from app.config import Settings, get_settings
from app.llm import ChatMessage, ProviderError, get_chat_provider
from app.sources.live import Evidence, LiveResult
from app.sources.store import Freshness

SOURCE_SYSTEM = """\
You answer using only the pages provided, which come from one institution's
own official website. That institution sets these rules, so its pages are the
authority for them.

{voice}

Rules you may not break:
- Use only what the provided pages say. Never add a requirement, a fee, a
  deadline or a document from anywhere else, including your own knowledge of
  this institution.
- If the pages do not answer the question, say so plainly and say what they do
  cover. Do not fill the gap.
- Refer to the institution by the name the pages use.
- Write in {language_name}.

Length is part of being useful. You were given several pages; that is so you
can find the relevant part, not so you can summarise all of them. Answer the
question that was asked and stop:
- a fact or a yes/no: two or three sentences;
- a list of what is needed: the list, and nothing around it;
- a procedure: the steps, briefly.
Never pad an answer to look thorough. If one sentence is the whole answer,
one sentence is the answer.
"""

SOURCE_USER = """\
QUESTION: {question}

PAGES FROM {institution} ({domain}):
{passages}

Answer the question from these pages only.
"""


def as_passages(evidence: list[Evidence], limit: int = 6000) -> str:
    """The pages, labelled so the model can attribute what it uses."""
    blocks = []
    for index, item in enumerate(evidence, start=1):
        body = item.text[:limit]
        blocks.append(f"[{index}] {item.title} — {item.canonical_url}\n{body}")
    return "\n\n---\n\n".join(blocks)


def _citation(item: Evidence) -> Citation:
    return Citation(
        fiche_id=item.domain,
        title_fr=item.title,
        url=item.canonical_url or item.url,
        # Only what the page itself stated. Absent, the label falls back to
        # "update date unavailable" rather than to the time we happened to read it.
        last_updated=(item.updated or "")[:10],
        last_updated_is_plausible=True,
        # The raw state, not a sentence: the interface translates it.
        situation_fr=item.freshness.value,
        section_title_fr="",
        score=1.0,
        excerpt=item.excerpt,
    )


def answer_from_source(question: str, live: LiveResult, *,
                       language: str = "en",
                       settings: Settings | None = None) -> AnswerResult:
    """Answer this question from what the institution's own pages say."""
    settings = settings or get_settings()
    started = time.monotonic()
    source = live.source
    institution = source.name if source else live.entity_id

    result = AnswerResult(
        question=question,
        language=language,
        text="",
        refused=True,
        citations=[_citation(item) for item in live.evidence],
    )

    if not live.evidence:
        result.error = live.error or "the official site could not be read"
        result.elapsed = time.monotonic() - started
        return result

    messages = [
        ChatMessage("system", SOURCE_SYSTEM.format(
            voice=prompts.VOICE,
            language_name=prompts.language_name(language))),
        ChatMessage("user", SOURCE_USER.format(
            question=question,
            institution=institution,
            domain=source.domain if source else "",
            passages=as_passages(live.evidence))),
    ]

    try:
        reply = get_chat_provider(settings).complete(
            messages, temperature=0.1, max_tokens=1200)
    except ProviderError as exc:
        result.error = str(exc)
        result.elapsed = time.monotonic() - started
        return result

    text = (reply or "").strip()
    result.text = text
    # The model was told to say when the pages do not cover the question; that
    # is a refusal, and the interface should treat it as one.
    lowered = text.lower()
    result.refused = not text or any(
        marker in lowered for marker in
        ("do not cover", "don't cover", "does not cover", "doesn't cover",
         "ne couvrent pas", "ne couvre pas", "n'indiquent pas", "ne précisent pas"))
    result.elapsed = time.monotonic() - started
    return result


def freshness_key(live: LiveResult) -> str:
    """The translation key for how current this evidence is.

    A key, not a sentence: the note is stored on the turn and re-rendered
    whenever the interface language changes, and a baked-in string would stay
    in whichever language the answer happened to arrive in.
    """
    return {
        Freshness.LIVE_VERIFIED: "fresh_live",
        Freshness.FRESH: "fresh_cached",
        Freshness.STALE: "fresh_stale",
        Freshness.UNAVAILABLE: "fresh_unavailable",
        Freshness.UNKNOWN: "fresh_unavailable",
    }.get(live.freshness, "fresh_cached")
