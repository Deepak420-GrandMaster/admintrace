"""Groq chat provider, over its OpenAI-compatible endpoint.

Uses the standard library rather than an SDK: the surface needed here is one
POST and one server-sent-event stream, and a dependency earns its place by
doing more than that.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Iterator

from app.config import Settings
from app.llm.base import ChatMessage, ProviderError

USER_AGENT = "reperes/0.1"

# Reasoning models spend part of the token budget thinking before they emit a
# single character of answer. Left at its default, a short request returns
# nothing at all: the budget is gone before the answer starts. Only these
# models accept the parameter; sending it to another one is rejected.
REASONING_MODELS = ("gpt-oss",)

# Free tiers are metered per minute, and one question costs two calls: the
# answerability check and the answer itself. Hitting the limit is ordinary
# rather than exceptional, and the service says exactly how long to wait, so
# waiting is better than failing in front of the user.
MAX_RATE_LIMIT_RETRIES = 2
MAX_RATE_LIMIT_WAIT = 35.0
_RETRY_AFTER = re.compile(r"try again in ([\d.]+)s", re.IGNORECASE)


class _RateLimited(Exception):
    """Transient: the service told us to come back shortly."""

    def __init__(self, retry_after: float, detail: str) -> None:
        super().__init__(detail)
        self.retry_after = retry_after
        self.detail = detail


class GroqProvider:
    name = "groq"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.model = settings.groq_model

    # -- internals ---------------------------------------------------------

    def _request(self, payload: dict, stream: bool) -> urllib.request.addinfourl:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return self._request_once(payload, stream)
            except _RateLimited as limited:
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise ProviderError(
                        f"Groq rate limit reached and still limited after "
                        f"{attempt + 1} attempts. {limited.detail}"
                    ) from None
                time.sleep(min(limited.retry_after, MAX_RATE_LIMIT_WAIT))
        raise ProviderError("Groq rate limit reached")  # unreachable

    def _request_once(self, payload: dict, stream: bool) -> urllib.request.addinfourl:
        request = urllib.request.Request(
            f"{self._settings.groq_base_url.rstrip('/')}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._settings.groq_api_key}",
                "Content-Type": "application/json",
                "User-Agent": USER_AGENT,
                "Accept": "text/event-stream" if stream else "application/json",
            },
        )
        try:
            return urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            try:
                detail = json.loads(detail)["error"]["message"]
            except Exception:
                pass
            if exc.code == 429:
                match = _RETRY_AFTER.search(detail)
                wait = float(match.group(1)) if match else 10.0
                raise _RateLimited(wait, detail) from None
            # The key must never reach a log line or a traceback.
            raise ProviderError(f"Groq refused the request ({exc.code}): {detail}") from None
        except urllib.error.URLError as exc:
            raise ProviderError(f"Groq is unreachable: {exc.reason}") from None

    def _payload(self, messages: list[ChatMessage], temperature: float,
                 max_tokens: int, stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
            # A refusal that flips between identical runs is not a gate. This
            # does not guarantee determinism, but without it the same question
            # was observed to be answered once and refused once.
            "seed": 7,
        }
        if any(marker in self.model for marker in REASONING_MODELS):
            payload["reasoning_effort"] = self._settings.groq_reasoning_effort
        return payload

    # -- interface ---------------------------------------------------------

    def complete(self, messages: list[ChatMessage], *, temperature: float = 0.0,
                 max_tokens: int = 1024) -> str:
        with self._request(
            self._payload(messages, temperature, max_tokens, stream=False), stream=False
        ) as response:
            body = json.load(response)
        try:
            # Reasoning models return their scratchpad in a separate field;
            # only the answer itself is content.
            return (body["choices"][0]["message"].get("content") or "").strip()
        except (KeyError, IndexError) as exc:
            raise ProviderError(f"Unexpected response shape from Groq: {exc}") from None

    def stream(self, messages: list[ChatMessage], *, temperature: float = 0.0,
               max_tokens: int = 1024) -> Iterator[str]:
        with self._request(
            self._payload(messages, temperature, max_tokens, stream=True), stream=True
        ) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (ValueError, KeyError, IndexError):
                    continue
                piece = delta.get("content")
                if piece:
                    yield piece

    def health(self) -> tuple[bool, str]:
        try:
            self.complete(
                [ChatMessage("user", "Répondre uniquement: OK")], max_tokens=5
            )
            return True, f"groq · {self.model}"
        except ProviderError as exc:
            return False, str(exc)
