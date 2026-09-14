"""Generate a grounded answer, or a refusal.

The gate decides which of the two happens. When it says refuse, no attempt is
made at a best-effort answer: the model is not even shown the rejected
passages as material to answer from, only told that related pages exist. A
plausible answer built from passages that did not match is indistinguishable,
to the reader, from a correct one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import Iterator

from app.answer import cite, prompts
from app.answer.cite import Citation, ServiceLink
from app.config import Settings, get_settings
from app.llm import ChatMessage, ProviderError, get_chat_provider
from app.query.normalize import PreparedQuery, prepare
from app.retrieval import hybrid
from app.retrieval.gate import GateDecision, apply_gate, verify_answerable
from app.retrieval.types import Retrieved


@dataclass
class AnswerResult:
    """An answer or a refusal, with everything needed to explain it."""

    question: str
    language: str
    text: str
    refused: bool
    carried_context: bool = False
    citations: list[Citation] = field(default_factory=list)
    services: list[ServiceLink] = field(default_factory=list)
    prepared: PreparedQuery | None = None
    gate: GateDecision | None = None
    error: str | None = None
    rate_limited: bool = False
    retry_after: float | None = None
    elapsed: float = 0.0

    @property
    def retrieved(self) -> list[Retrieved]:
        return self.gate.all_candidates if self.gate else []


def _search(prepared: PreparedQuery, settings: Settings) -> list[Retrieved]:
    return hybrid.search_many(list(prepared.queries), settings=settings)


def _messages(prepared: PreparedQuery, decision: GateDecision) -> list[ChatMessage]:
    language = prompts.language_name(prepared.language)

    if decision.should_refuse:
        near = decision.rejected[:3]
        if near:
            related = "Related pages that did not match closely enough:\n" + "\n".join(
                f"- {h.metadata.get('fiche_title_fr', '')}" for h in near
            )
        else:
            related = "No related pages were found."
        return [
            ChatMessage("system", prompts.REFUSAL_SYSTEM.format(language_name=language)),
            ChatMessage("user", prompts.REFUSAL_USER.format(
                question=prepared.original, related=related)),
        ]

    return [
        ChatMessage("system", prompts.answer_system(prepared.language)),
        ChatMessage("user", prompts.ANSWER_USER.format(
            language_name=language,
            question=prepared.original,
            passages=cite.as_passages(decision.passed),
        )),
    ]


# Openers that only make sense as a continuation of the last question.
_ELLIPTICAL = (
    "and ", "and,", "what about", "how about", "ok and", "also", "then ",
    "so ", "but ", "what if", "and what", "in that case",
    "et ", "et pour", "et si", "et quoi", "aussi", "alors", "donc ", "puis ",
    "mais ", "et dans ce cas", "dans ce cas",
)

# Words that carry no subject of their own. A question built only from these
# is asking about whatever was already being discussed.
_GENERIC = frozenset("""
document documents paper papers papier papiers piece pieces justificatif
justificatifs form forms formulaire formulaires
cost costs price prices fee fees cout couts coute coutent combien ca cela
prix tarif tarifs montant
time deadline delay delays delai delais duree long much many far often
souvent longtemps
where who when how what which why
quel quels quelle quelles quoi qui ou comment pourquoi quand
need needs needed require required faut besoin fournir apporter
do does did is are was were can could should must go
dois doit devons devez doivent aller suis est sont etre avoir ai
i me my mine we our you your it its they them the a an
je me mon ma mes nous notre vous votre il elle ce cette les des du de la le
next apres ensuite suite step steps etape etapes procedure demarche demarches
then also too encore aussi
""".split())


def classify_followup(question: str, previous: str | None) -> str:
    """CONTINUATION or NEW_TOPIC.

    A conversation on screen promises the assistant remembers. Retrieval does
    not, so a question that leans on the last one has to carry it — otherwise
    "what documents do I need?" is searched against nothing and answered about
    nothing.

    The test is whether the question names a subject of its own. "What
    documents do I need?" names none, so it belongs to whatever came before.
    "What is the weather in Paris?" names one, so it does not, and dragging a
    residence permit into it would produce a worse answer than admitting the
    corpus has nothing to say.
    """
    asked = (question or "").strip()
    if not previous or not asked:
        return "NEW_TOPIC"

    lowered = asked.lower()
    if lowered.startswith(_ELLIPTICAL):
        return "CONTINUATION"

    from app.retrieval.keyword import tokenize

    content = [word for word in tokenize(asked) if word not in _GENERIC]
    if not content:
        return "CONTINUATION"
    # A subject of its own, however short, stands alone.
    return "NEW_TOPIC"


def resolve_followup(question: str, previous: str | None) -> tuple[str, bool]:
    """Expand a question that only makes sense after the last one.

    Only the previous question is carried, never the answer: an answer is long,
    and folding it into a search query buries the thing actually being asked.
    """
    asked = (question or "").strip()
    if classify_followup(asked, previous) == "NEW_TOPIC":
        return asked, False
    return f"{previous.strip()} — {asked}", True


def _retrieve(question: str, settings: Settings) -> tuple[PreparedQuery, GateDecision]:
    prepared = prepare(question, settings)
    candidates = _search(prepared, settings)
    decision = apply_gate(candidates, settings)
    return prepared, verify_answerable(question, decision, settings)


def answer_stream(question: str, settings: Settings | None = None,
                  language: str | None = None,
                  previous_question: str | None = None) -> Iterator[AnswerResult]:
    """Yield the answer as it is written, then a final complete result.

    ``language`` forces the language the answer is written in. Retrieval still
    uses the language the question was actually asked in, because that is what
    decides whether the query needs translating before it meets a French
    corpus. Someone can reasonably ask in English and want to read the reply in
    French, or the reverse.
    """
    settings = settings or get_settings()
    started = time.time()

    question = (question or "").strip()
    if not question:
        yield AnswerResult(question, "en", "", refused=False,
                           error="Ask a question to get started.")
        return

    searchable, carried = resolve_followup(question, previous_question)
    prepared, decision = _retrieve(searchable, settings)

    wanted = language or settings.answer_language
    if wanted in {"en", "fr"} and wanted != prepared.language:
        prepared = replace(prepared, language=wanted)

    citations = [] if decision.should_refuse else cite.build(decision.passed)
    services = [] if decision.should_refuse else cite.service_links(decision.passed)

    partial = AnswerResult(
        question=question, language=prepared.language, text="",
        refused=decision.should_refuse, carried_context=carried,
        citations=citations, services=services, prepared=prepared, gate=decision,
    )
    yield partial

    provider = get_chat_provider(settings)
    collected: list[str] = []
    try:
        for piece in provider.stream(_messages(prepared, decision),
                                     temperature=0.0, max_tokens=2000):
            collected.append(piece)
            partial = AnswerResult(
                question=question, language=prepared.language,
                text="".join(collected), refused=decision.should_refuse,
                carried_context=carried, citations=citations, services=services, prepared=prepared,
                gate=decision, elapsed=time.time() - started,
            )
            yield partial
    except ProviderError as exc:
        yield AnswerResult(
            question=question, language=prepared.language,
            text="".join(collected), refused=decision.should_refuse,
            carried_context=carried, citations=citations, services=services, prepared=prepared,
            gate=decision, error=str(exc),
            rate_limited=getattr(exc, "rate_limited", False),
            retry_after=getattr(exc, "retry_after", None),
            elapsed=time.time() - started,
        )
        return

    yield AnswerResult(
        question=question, language=prepared.language, text="".join(collected).strip(),
        refused=decision.should_refuse, carried_context=carried, citations=citations, services=services,
        prepared=prepared, gate=decision, elapsed=time.time() - started,
    )


def answer(question: str, settings: Settings | None = None,
           language: str | None = None,
           previous_question: str | None = None) -> AnswerResult:
    """Answer without streaming. Same behaviour, collected."""
    result = AnswerResult(question, "en", "", refused=False)
    for result in answer_stream(question, settings, language, previous_question):
        pass
    return result
