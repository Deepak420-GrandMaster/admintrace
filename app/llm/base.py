"""The interface every chat provider implements."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator, Protocol


class ProviderError(RuntimeError):
    """The chat model could not be reached or refused the request."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class ChatProvider(Protocol):
    """Minimal surface: complete a conversation, or stream that completion."""

    name: str
    model: str

    def complete(self, messages: list[ChatMessage], *,
                 temperature: float = 0.0, max_tokens: int = 1024) -> str: ...

    def stream(self, messages: list[ChatMessage], *,
               temperature: float = 0.0,
               max_tokens: int = 1024) -> Iterator[str]: ...

    def health(self) -> tuple[bool, str]: ...
