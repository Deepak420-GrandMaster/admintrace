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

#: How many passages the answer model is shown. Six was costing ~6,000
#: prompt tokens a question against an 8,000-per-minute budget; four keeps
#: the supporting detail an answer needs without paying for the tail.
EVIDENCE_PASSAGES = 4
#: Ceiling on one answer, in tokens. Reserved against the provider's rate
#: limit whether or not it is spent, so it is sized to the longest answer the
#: length policy permits (300 words) plus reasoning, not left at a round
#: number well above anything that can occur.
ANSWER_TOKEN_CEILING = 900

from app.answer import cite, claimcheck, length, prompts, relevance
from app.answer import procedures as procedure_model
from app.query import dates as question_dates
from app import telemetry
from app.sources import purpose as purpose_table
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
    #: Candidate/selected split and why anything was dropped. Never displayed.
    selection: dict = field(default_factory=dict)
    services: list[ServiceLink] = field(default_factory=list)
    prepared: PreparedQuery | None = None
    gate: GateDecision | None = None
    error: str | None = None
    rate_limited: bool = False
    retry_after: float | None = None
    #: What claim validation decided about this answer. Never displayed.
    validation: dict = field(default_factory=dict)
    #: True when nothing the model wrote could be verified against the
    #: evidence. Rendered as an honest statement, never as blank text.
    unverified: bool = False
    elapsed: float = 0.0

    @property
    def retrieved(self) -> list[Retrieved]:
        return self.gate.all_candidates if self.gate else []


def _search(prepared: PreparedQuery, settings: Settings) -> list[Retrieved]:
    return hybrid.search_many(list(prepared.queries), settings=settings)


def _evidence_for(decision: GateDecision) -> list:
    """The passages the model is shown.

    The selected sources, capped. These are the same passages that will be
    cited under the answer, which was not previously guaranteed: the model
    saw everything the similarity gate passed while the citations listed only
    what survived relevance, so an answer could lean on a page the reader was
    never shown.
    """
    chosen = getattr(decision, "selected", None) or decision.passed
    return chosen[:EVIDENCE_PASSAGES]


def _messages(prepared: PreparedQuery, decision: GateDecision) -> list[ChatMessage]:
    language = prompts.language_name(prepared.language)

    if decision.should_refuse:
        # The titles of pages that failed the relevance gate are not offered to
        # the model any more. They were the "closest pages we found" list by
        # another route: removed from the interface, they came back as prose —
        # a free-tram answer for Antibes recommending "the pages above about RSA
        # benefits". A page that did not clear the bar is not a lead.
        related = "No official page answered this question."
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
            passages=cite.as_passages(_evidence_for(decision)),
        ) + prompts.procedure_note(procedure_model.label(
            procedure_model.detect(prepared.original)))),
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


def _retrieve(question: str, settings: Settings, *, place=None
              ) -> tuple[PreparedQuery, GateDecision, "relevance.Selection"]:
    """Retrieve, select, then judge — in that order.

    Selection runs before the answerability stage on purpose: a page that is
    about somewhere else or some other subject should not be part of what a
    model is asked to judge, and it should not be part of what the model is
    shown either. Judging first meant paying for an opinion about evidence we
    were about to drop.
    """
    prepared = prepare(question, settings)
    candidates = _search(prepared, settings)
    decision = apply_gate(candidates, settings)
    selection = relevance.select(
        decision.passed, place=place,
        purposes=tuple(p.id for p in purpose_table.detect(question)))
    decision = verify_answerable(question, decision, settings,
                                 selected=selection.selected)
    return prepared, decision, selection


def answer_stream(question: str, settings: Settings | None = None,
                  language: str | None = None,
                  previous_question: str | None = None,
                  place=None) -> Iterator[AnswerResult]:
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

    trace = telemetry.Trace(question_chars=len(question),
                            # Set up front: a failed answer still belongs to a
                            # class, and "unknown" hid which kind of question
                            # the provider was refusing.
                            response_class=length.classify(question).name,
                            provider=settings.llm_provider,
                            model=(settings.groq_model if settings.llm_provider == "groq"
                                   else settings.ollama_chat_model))
    searchable, carried = resolve_followup(question, previous_question)
    with trace.phase("retrieval"):
        prepared, decision, selection = _retrieve(searchable, settings, place=place)
    trace.model_calls = getattr(decision, "model_calls", 0)
    trace.evidence_verdict = getattr(decision, "evidence_verdict", "")
    trace.refused = decision.should_refuse

    wanted = language or settings.answer_language
    if wanted in {"en", "fr"} and wanted != prepared.language:
        prepared = replace(prepared, language=wanted)

    # What retrieval found is a candidate list, not a source list. A page has
    # to be about this question — not merely close to its words — before a
    # reader is shown it under an answer. Selected upstream, in _retrieve, so
    # the same list is what the model is shown and what is cited beneath it.
    chosen = selection.selected
    citations = [] if decision.should_refuse else cite.build(chosen)
    services = [] if decision.should_refuse else cite.service_links(chosen)

    partial = AnswerResult(
        question=question, language=prepared.language, text="",
        refused=decision.should_refuse, carried_context=carried,
        citations=citations, services=services, prepared=prepared, gate=decision,
        selection=selection.as_diagnostics(),
    )
    yield partial

    if getattr(decision, "provider_limited", False):
        # The answerability check already met the quota wall a moment ago.
        # Asking again now buys the same refusal at the price of a second
        # wait, so tell the reader immediately instead.
        yield AnswerResult(
            question=question, language=prepared.language, text="",
            refused=decision.should_refuse, carried_context=carried,
            citations=citations, services=services, prepared=prepared,
            gate=decision, selection=selection.as_diagnostics(),
            error=decision.answerability_verdict, rate_limited=True,
            elapsed=time.time() - started,
        )
        return

    provider = get_chat_provider(settings)
    generation_started = time.time()
    buffering = settings.claim_validation and not decision.should_refuse
    collected: list[str] = []
    try:
        for piece in provider.stream(_messages(prepared, decision),
                                     temperature=0.0,
                                     # Groq reserves max_tokens against the
                                     # per-minute budget as well as charging
                                     # the prompt — a 429 reads "Limit 8000,
                                     # Used 5445, Requested 6193" — so this
                                     # ceiling is paid for whether or not it
                                     # is used. Measured: a full answer to a
                                     # procedural question finishes in 546
                                     # completion tokens including reasoning,
                                     # with finish_reason "stop". 900 leaves
                                     # room and stops reserving 2000.
                                     max_tokens=ANSWER_TOKEN_CEILING):
            collected.append(piece)
            # Not shown while it is written. An answer is checked claim by
            # claim before a reader sees it, and streaming the unchecked draft
            # would put exactly the sentences validation removes on screen for
            # the seconds it takes to write them. The interface keeps its
            # "reading the official pages" state until the checked answer lands.
            partial = AnswerResult(
                question=question, language=prepared.language,
                text="" if buffering else "".join(collected),
                refused=decision.should_refuse,
                carried_context=carried, citations=citations, services=services, prepared=prepared,
                gate=decision, elapsed=time.time() - started,
            )
            yield partial
    except ProviderError as exc:
        trace.rate_limited = getattr(exc, "rate_limited", False)
        trace.retry_after = float(getattr(exc, "retry_after", 0) or 0)
        trace.total_time = round(time.time() - started, 3)
        trace.empty_answer = not "".join(collected).strip()
        telemetry.record(trace, settings)
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

    written = "".join(collected).strip()
    trace.llm_time = round(time.time() - generation_started, 3)
    trace.total_time = round(time.time() - started, 3)
    trace.word_count = len(written.split())
    if settings.llm_provider == "ollama":
        trace.provider_state = (
            telemetry.ProviderState.LOCAL_SLOW.value
            if trace.total_time >= telemetry.LOCAL_SLOW_SECONDS
            else telemetry.ProviderState.LOCAL_AVAILABLE.value)
    else:
        trace.provider_state = telemetry.ProviderState.GROQ_AVAILABLE.value

    if not written:
        # An answer that is blank is never shown as one. The reader waited;
        # telling them nothing happened, with the reason, is the only honest
        # thing left. This used to render as an empty bubble under a
        # question, which reads as the system having nothing to say.
        trace.empty_answer = True
        telemetry.record(trace, settings)
        yield AnswerResult(
            question=question, language=prepared.language, text="",
            refused=decision.should_refuse, carried_context=carried,
            citations=citations, services=services, prepared=prepared,
            gate=decision, selection=selection.as_diagnostics(),
            error="the model returned nothing", elapsed=time.time() - started,
        )
        return

    trace.empty_answer = False
    validation_summary: dict = {}
    if buffering:
        shown = _evidence_for(decision)

        def regenerate(usable):
            urls = {item.url for item in usable}
            return provider.complete([
                ChatMessage("system", prompts.REPAIR_SYSTEM.format(
                    language_name=prompts.language_name(prepared.language))),
                ChatMessage("user", prompts.REPAIR_USER.format(
                    question=question,
                    procedure=procedure_model.label(
                        procedure_model.detect(question)) or "not stated",
                    passages=cite.as_passages(
                        [h for h in shown
                         if (h.metadata or {}).get("source_url") in urls] or shown))),
            ], temperature=0.0, max_tokens=600)

        checked_at = time.time()
        validation, repairs = claimcheck.check_and_repair(
            written, claimcheck.from_hits(shown), question=question,
            on=question_dates.parse(question).on, place=place,
            regenerate=regenerate)
        trace.claim_validation_time = round(time.time() - checked_at, 3)
        trace.model_calls += repairs
        trace.claims_generated = validation.generated
        trace.claims_supported = validation.supported
        trace.claims_removed = validation.removed
        trace.claims_contradicted = validation.contradicted
        trace.supported_claim_ratio = validation.supported_ratio
        trace.repair_calls = repairs
        claimcheck.record(validation, path="corpus", settings=settings)
        validation_summary = validation.as_dict()
        if not validation.text:
            trace.total_time = round(time.time() - started, 3)
            telemetry.record(trace, settings)
            yield AnswerResult(
                question=question, language=prepared.language, text="",
                refused=True, carried_context=carried, citations=[],
                services=services, prepared=prepared, gate=decision,
                selection=selection.as_diagnostics(),
                validation=validation_summary, unverified=True,
                elapsed=time.time() - started)
            return
        written = validation.text
        backing = validation.source_urls()
        if backing:
            citations = [c for c in citations if c.url in backing] or citations
        trace.word_count = len(written.split())
        trace.total_time = round(time.time() - started, 3)

    telemetry.record(trace, settings)
    yield AnswerResult(
        question=question, language=prepared.language, text=written,
        refused=decision.should_refuse, carried_context=carried, citations=citations, services=services,
        prepared=prepared, gate=decision, selection=selection.as_diagnostics(),
        validation=validation_summary, elapsed=time.time() - started,
    )


def answer(question: str, settings: Settings | None = None,
           language: str | None = None,
           previous_question: str | None = None) -> AnswerResult:
    """Answer without streaming. Same behaviour, collected."""
    result = AnswerResult(question, "en", "", refused=False)
    for result in answer_stream(question, settings, language, previous_question):
        pass
    return result
