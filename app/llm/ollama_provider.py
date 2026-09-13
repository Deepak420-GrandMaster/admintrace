"""Local chat provider, through an Ollama daemon.

Kept deliberately interchangeable with the hosted provider so that running
entirely offline is a configuration change and nothing more.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterator

from app.config import Settings
from app.llm.base import ChatMessage, ProviderError


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.ollama_chat_model

    def _post(self, payload: dict):
        request = urllib.request.Request(
            f"{self._settings.ollama_base_url.rstrip('/')}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            return urllib.request.urlopen(request, timeout=300)
        except urllib.error.HTTPError as exc:
            raise ProviderError(
                f"Ollama refused the request ({exc.code}): "
                f"{exc.read().decode('utf-8', 'replace')[:300]}"
            ) from None
        except urllib.error.URLError as exc:
            raise ProviderError(
                f"Ollama is unreachable at {self._settings.ollama_base_url}: "
                f"{exc.reason}. Is the daemon running?"
            ) from None

    def complete(self, messages: list[ChatMessage], *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        return "".join(self.stream(messages, temperature=temperature,
                                   max_tokens=max_tokens))

    def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0,
               max_tokens: int = 1024) -> Iterator[str]:
        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": True,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        with self._post(payload) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                try:
                    body = json.loads(line)
                except ValueError:
                    continue
                piece = body.get("message", {}).get("content")
                if piece:
                    yield piece
                if body.get("done"):
                    return

    def health(self) -> tuple[bool, str]:
        try:
            self.complete([ChatMessage("user", "Répondre uniquement: OK")],
                          max_tokens=5)
            return True, f"ollama · {self.model}"
        except ProviderError as exc:
            return False, str(exc)
