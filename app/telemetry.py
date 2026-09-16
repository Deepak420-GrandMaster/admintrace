"""How long an answer took, and what it spent getting there.

Written because the honest answer to "why is it slow" turned out to be
nothing anyone had guessed. The model was answering in a quarter of a second;
the time was spent queueing behind a per-minute token limit that one question
was exhausting on its own. None of that was visible, so it was argued about
instead of measured.

One line per answered question, appended to the same audit log as everything
else. Nothing about the reader is recorded — not their question, not their
place, not what they were told. The question's length is kept because prompt
size is the thing that drives cost; its content is not.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum


class ProviderState(str, Enum):
    """What the answer path could actually reach when it ran."""

    GROQ_AVAILABLE = "groq_available"
    GROQ_RATE_LIMITED = "groq_rate_limited"
    GROQ_UNAVAILABLE = "groq_unavailable"
    LOCAL_AVAILABLE = "local_available"
    LOCAL_SLOW = "local_slow"


#: Targets, not guarantees, by response class. Recorded against the outcome so
#: a regression is visible without anyone having to remember the number.
TARGET_SECONDS = {
    "MICRO": 5.0,
    "STANDARD": 10.0,
    "PROCEDURAL": 15.0,
    "COMPLEX": 30.0,
}
#: Past this, the local model is reported as slow rather than merely local.
LOCAL_SLOW_SECONDS = 20.0


@dataclass
class Trace:
    """One question's cost, phase by phase."""

    clarification_time: float = 0.0
    retrieval_time: float = 0.0
    source_validation_time: float = 0.0
    llm_time: float = 0.0
    total_time: float = 0.0
    provider: str = ""
    provider_state: str = ""
    model: str = ""
    model_calls: int = 0
    evidence_verdict: str = ""
    response_class: str = ""
    word_count: int = 0
    question_chars: int = 0
    prompt_chars: int = 0
    rate_limited: bool = False
    retry_after: float = 0.0
    refused: bool = False
    empty_answer: bool = False
    phases: dict = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str):
        started = time.perf_counter()
        try:
            yield
        finally:
            spent = time.perf_counter() - started
            self.phases[name] = round(spent, 3)
            if hasattr(self, f"{name}_time"):
                setattr(self, f"{name}_time", round(spent, 3))

    def within_target(self) -> bool | None:
        target = TARGET_SECONDS.get((self.response_class or "").upper())
        if target is None:
            return None
        return self.total_time <= target


def record(trace: Trace, settings=None) -> None:
    """Append one trace to the audit log. Never raises into an answer."""
    try:
        from app.sources import store

        store.audit("answer.timing", settings,
                    at=datetime.now(tz=timezone.utc).isoformat(timespec="seconds"),
                    **{k: v for k, v in asdict(trace).items() if k != "phases"},
                    phases=trace.phases)
    except Exception:  # noqa: BLE001 - telemetry must never break an answer
        pass


def traces(settings=None) -> list[dict]:
    """Every recorded answer timing, oldest first."""
    from app.sources import store

    path = store.root(settings) / "audit.jsonl"
    if not path.exists():
        return []
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"answer.timing"' not in line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        return []
    return out


def percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = fraction * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarise(rows: list[dict], by: str = "response_class") -> dict:
    """Average, p50, p95 and max, grouped by one field."""
    groups: dict[str, list[float]] = {}
    for row in rows:
        key = str(row.get(by) or "unknown")
        total = row.get("total_time")
        if isinstance(total, (int, float)):
            groups.setdefault(key, []).append(float(total))
    return {
        key: {
            "n": len(values),
            "avg": round(sum(values) / len(values), 2),
            "p50": round(percentile(values, 0.50), 2),
            "p95": round(percentile(values, 0.95), 2),
            "max": round(max(values), 2),
        }
        for key, values in sorted(groups.items())
    }


def provider_health(rows: list[dict]) -> dict:
    """How often the provider refused us, and how long it asked us to wait."""
    limited = [r for r in rows if r.get("rate_limited")]
    waits = [float(r.get("retry_after") or 0) for r in limited]
    return {
        "answers": len(rows),
        "rate_limit_count": len(limited),
        "rate_limit_share": (round(len(limited) / len(rows), 3) if rows else 0.0),
        "rate_limit_wait_total": round(sum(waits), 1),
        "rate_limit_wait_max": round(max(waits), 1) if waits else 0.0,
        "fallback_count": sum(1 for r in rows
                              if str(r.get("provider")) == "ollama"),
        "provider_failures": sum(1 for r in rows if r.get("empty_answer")),
        "empty_answers": sum(1 for r in rows if r.get("empty_answer")),
    }
