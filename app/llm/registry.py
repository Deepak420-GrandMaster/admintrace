"""Choose the configured chat provider."""

from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.llm.base import ChatProvider


@lru_cache(maxsize=2)
def _build(provider: str, _fingerprint: str) -> ChatProvider:
    settings = get_settings()
    if provider == "groq":
        from app.llm.groq_provider import GroqProvider

        return GroqProvider(settings)
    if provider == "ollama":
        from app.llm.ollama_provider import OllamaProvider

        return OllamaProvider(settings)
    raise ValueError(f"Unknown chat provider: {provider}")


def get_chat_provider(settings: Settings | None = None) -> ChatProvider:
    settings = settings or get_settings()
    return _build(settings.llm_provider, settings.groq_model or settings.ollama_chat_model)
