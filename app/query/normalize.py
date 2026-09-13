"""Turn an English question into a French search query.

The corpus is French. Searching it with English words leans entirely on the
embedding model's cross-lingual alignment and gives keyword search nothing to
match, so the question is restated in French first.

Translation is a retrieval step, never a content step: its output is used to
search, never shown as an answer, and it cannot introduce a fact because
nothing it produces reaches the user.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.config import Settings, get_settings
from app.llm import ChatMessage, ProviderError, get_chat_provider
from app.query import glossary
from app.query.detect import detect

TRANSLATE_SYSTEM = (
    "You rewrite a question into a French search query for a French "
    "government information website. Reply with the query only: no quotes, no "
    "explanation, no preamble. Use the vocabulary French administrations use. "
    "Do not answer the question. Do not add any detail the question does not "
    "contain."
)


@dataclass(frozen=True)
class PreparedQuery:
    original: str
    language: str
    confidence: float
    search_query: str
    expanded_query: str
    glossary_terms: tuple[str, ...]
    translation_failed: bool = False

    @property
    def queries(self) -> tuple[str, ...]:
        """Distinct query forms worth searching with."""
        seen: list[str] = []
        for candidate in (self.search_query, self.expanded_query):
            if candidate and candidate not in seen:
                seen.append(candidate)
        return tuple(seen)


@lru_cache(maxsize=512)
def _translate(text: str, _model: str) -> str:
    provider = get_chat_provider()
    return provider.complete(
        [ChatMessage("system", TRANSLATE_SYSTEM), ChatMessage("user", text)],
        temperature=0.0,
        max_tokens=400,
    ).strip().strip('"')


def prepare(question: str, settings: Settings | None = None) -> PreparedQuery:
    settings = settings or get_settings()
    detection = detect(question)

    search_query = question
    failed = False
    if detection.language == "en":
        try:
            translated = _translate(question, settings.groq_model or settings.ollama_chat_model)
            if translated:
                search_query = translated
        except ProviderError:
            # Without a translation the multilingual embeddings still work, so
            # searching in English is degraded but not broken.
            failed = True

    expanded, terms = glossary.expand(search_query)
    # The original wording can carry an exact term the translation dropped.
    if detection.language == "en":
        _, original_terms = glossary.expand(question)
        extra = [t.fr for t in original_terms if t.fr not in expanded]
        if extra:
            expanded = f"{expanded} {' '.join(extra)}"
            terms = list({t.fr: t for t in [*terms, *original_terms]}.values())

    return PreparedQuery(
        original=question,
        language=detection.language,
        confidence=detection.confidence,
        search_query=search_query,
        expanded_query=expanded,
        glossary_terms=tuple(t.fr for t in terms),
        translation_failed=failed,
    )
