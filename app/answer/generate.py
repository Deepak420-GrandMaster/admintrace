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


def _retrieve(question: str, settings: Settings) -> tuple[PreparedQuery, GateDecision]:
    prepared = prepare(question, settings)
    candidates = _search(prepared, settings)
    decision = apply_gate(candidates, settings)
    return prepared, verify_answerable(question, decision, settings)


def answer_stream(question: str, settings: Settings | None = None,
                  language: str | None = None) -> Iterator[AnswerResult]:
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

    prepared, decision = _retrieve(question, settings)

    wanted = language or settings.answer_language
    if wanted in {"en", "fr"} and wanted != prepared.language:
        prepared = replace(prepared, language=wanted)

    citations = [] if decision.should_refuse else cite.build(decision.passed)
    services = [] if decision.should_refuse else cite.service_links(decision.passed)

    partial = AnswerResult(
        question=question, language=prepared.language, text="",
        refused=decision.should_refuse, citations=citations, services=services,
        prepared=prepared, gate=decision,
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
                citations=citations, services=services, prepared=prepared,
                gate=decision, elapsed=time.time() - started,
            )
            yield partial
    except ProviderError as exc:
        yield AnswerResult(
            question=question, language=prepared.language,
            text="".join(collected), refused=decision.should_refuse,
            citations=citations, services=services, prepared=prepared,
            gate=decision, error=str(exc),
            rate_limited=getattr(exc, "rate_limited", False),
            retry_after=getattr(exc, "retry_after", None),
            elapsed=time.time() - started,
        )
        return

    yield AnswerResult(
        question=question, language=prepared.language, text="".join(collected).strip(),
        refused=decision.should_refuse, citations=citations, services=services,
        prepared=prepared, gate=decision, elapsed=time.time() - started,
    )


def answer(question: str, settings: Settings | None = None,
           language: str | None = None) -> AnswerResult:
    result = AnswerResult(question, "en", "", refused=False)
    for result in answer_stream(question, settings, language):
        pass
    return result
