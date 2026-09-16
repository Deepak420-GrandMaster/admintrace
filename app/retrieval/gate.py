"""Relevance gating, and the decision to refuse.

Refusing is a real answer here, not a failure. A confident reply assembled
from passages that do not actually address the question is worse than saying
plainly that the corpus does not cover it, because the person reading it has
no way to tell the difference and will act on it.

The gate judges every candidate on cosine similarity: a bounded, comparable
number. BM25 scores order results but never decide whether something is
relevant enough to show, since they are unbounded and corpus-relative.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings, get_settings
from app.retrieval import hybrid
from app.retrieval.types import Retrieved


ANSWERABILITY_SYSTEM = """\
You decide one thing: whether the passages contain the information needed to \
answer the question. You never answer the question yourself.

Reply with exactly one word on the first line, YES or NO, then one short \
sentence saying what is missing if the answer is NO.

Answer YES only if the passages state what was asked. Passages that are on a \
related topic, or that describe the normal process while the question asks \
what to do when that process is unavailable, do not answer the question: \
reply NO. When genuinely unsure, reply NO.
"""

ANSWERABILITY_USER = """\
QUESTION: {question}

PASSAGES:
{passages}

Do these passages contain what is needed to answer that question?
"""


@dataclass
class GateDecision:
    """What survived, what did not, and why."""

    passed: list[Retrieved] = field(default_factory=list)
    rejected: list[Retrieved] = field(default_factory=list)
    threshold: float = 0.0
    should_refuse: bool = False
    reason: str = ""
    # Which stage decided: "similarity", "answerability", or "" when nothing
    # refused. Shown in the debug panel so the decision is never a black box.
    refused_by: str = ""
    answerability_verdict: str = ""
    verdict_cached: bool = False
    #: What the deterministic gate settled, and why. "ask_model" is the only
    #: value that costs a model call.
    evidence_verdict: str = ""
    evidence_detail: str = ""
    #: The materially relevant subset, set once selection has run. The model
    #: is shown these and the reader is cited these — the same list.
    selected: list = field(default_factory=list)
    #: How many model calls this decision has spent. Asserted by tests.
    model_calls: int = 0
    #: True when the provider refused on quota while judging. The answer call
    #: would hit the same limit, so the caller stops instead of waiting twice.
    provider_limited: bool = False

    @property
    def best_score(self) -> float:
        candidates = self.passed or self.rejected
        return max((c.dense_score for c in candidates), default=0.0)

    @property
    def all_candidates(self) -> list[Retrieved]:
        return self.passed + self.rejected


def apply_gate(candidates: list[Retrieved],
               settings: Settings | None = None) -> GateDecision:
    settings = settings or get_settings()
    threshold = settings.relevance_threshold

    passed = [c for c in candidates if c.dense_score >= threshold]
    rejected = [c for c in candidates if c.dense_score < threshold]

    if not candidates:
        reason = "Nothing was retrieved for this question."
    elif not passed:
        best = max(c.dense_score for c in candidates)
        reason = (
            f"Nothing cleared the relevance threshold "
            f"(best match {best:.3f}, threshold {threshold:.2f})."
        )
    else:
        reason = ""

    return GateDecision(
        passed=passed,
        rejected=rejected,
        threshold=threshold,
        should_refuse=not passed,
        reason=reason,
        refused_by="similarity" if not passed and candidates else
                   ("similarity" if not candidates else ""),
    )


def _normalise(text: str) -> str:
    """Fold a question to its content, so trivial variation is not a new question.

    Case, accents, punctuation and spacing carry no meaning for this decision.
    Without folding them, "What proof of address do I need?" and "what proof
    of address do i need" are two different cache entries and can be judged
    differently, which is exactly the inconsistency this exists to stop.
    """
    folded = unicodedata.normalize("NFD", (text or "").lower())
    folded = "".join(c for c in folded if unicodedata.category(c) != "Mn")
    return " ".join(re.findall(r"[a-z0-9]+", folded))


def _verdict_key(question: str, decision: GateDecision) -> str:
    """Identity of this judgement: the question, and the evidence it saw.

    Two calls with the same question and the same passages must reach the same
    answer. Keying on both means a re-index or a retrieval change re-opens the
    question honestly, while an identical request never flips.
    """
    passages = sorted(hit.chunk_id for hit in decision.passed)
    material = json.dumps([_normalise(question), passages], ensure_ascii=False)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _verdict_store(settings: Settings) -> Path:
    settings.ensure_dirs()
    return settings.cache_dir / "answerability.json"


def _load_verdicts(settings: Settings) -> dict[str, dict]:
    path = _verdict_store(settings)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_verdict(settings: Settings, key: str, entry: dict) -> None:
    path = _verdict_store(settings)
    store = _load_verdicts(settings)
    store[key] = entry
    # Bounded: this is a consistency cache, not an archive.
    if len(store) > 4000:
        store = dict(list(store.items())[-3000:])
    try:
        path.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _read_verdict(reply: str) -> tuple[bool | None, str]:
    """Read YES or NO out of the reply without trusting its shape.

    Returning None means the reply could not be read, which is different from
    reading it as a refusal — an unparsable answer must not silently become a
    "no" and deny someone an answer the sources do support.
    """
    text = (reply or "").strip()
    if not text:
        return None, ""
    first = text.splitlines()[0].strip().upper()
    first = re.sub(r"[^A-Z]", "", first)
    if first.startswith("YES"):
        return True, text
    if first.startswith("NO"):
        return False, text
    upper = text.upper()
    if "YES" in upper and "NO" not in upper:
        return True, text
    if "NO" in upper and "YES" not in upper:
        return False, text
    return None, text


def verify_answerable(question: str, decision: GateDecision,
                      settings: Settings | None = None,
                      selected: list | None = None) -> GateDecision:
    """Second stage: do the surviving passages actually answer the question?

    Similarity cannot do this job. Measured against this corpus, a question the
    sources genuinely do not answer scores *higher* than the weakest question
    they do answer, because the corpus contains passages on the same topic. No
    threshold separates them, so topical closeness is asked to carry a
    judgement it was never capable of making.

    This stage only ever removes an answer. It cannot rescue passages the
    similarity gate already dropped, and it is told not to answer the question
    itself.
    """
    settings = settings or get_settings()
    if decision.should_refuse or not decision.passed or not settings.answerability_check:
        return decision

    # What the backend already knows, it does not pay a model to re-decide.
    # This used to run on every question, at ~3,200 requested tokens a time
    # against an 8,000-per-minute budget that the answer call alone nearly
    # filled. It still runs where it earns its place: evidence that is on
    # topic without clearly answering is the one judgement a similarity
    # score cannot make, and is exactly what this stage was built for.
    from app.answer import evidence as evidence_gate

    if selected is not None:
        decision.selected = list(selected)
    settled = evidence_gate.assess(decision, selected=selected,
                                   settings=settings)
    decision.evidence_verdict = settled.verdict.value
    decision.evidence_detail = settled.detail
    if settled.verdict is evidence_gate.Verdict.REFUSE:
        _apply_refusal(decision, settled.detail)
        decision.refused_by = "evidence"
        return decision
    if settled.verdict is evidence_gate.Verdict.ANSWER:
        decision.answerability_verdict = f"settled without a model: {settled.detail}"
        return decision

    from app.answer.cite import as_passages
    from app.llm import ChatMessage, ProviderError, get_chat_provider

    # A judgement already made on this question and this evidence is reused.
    # The model behind it is not reliably deterministic even at temperature
    # zero, and a question that is answered on Monday and refused on Tuesday
    # is worse than either outcome consistently.
    key = _verdict_key(question, decision)
    remembered = _load_verdicts(settings).get(key)
    if remembered is not None:
        decision.answerability_verdict = remembered.get("verdict", "")
        decision.verdict_cached = True
        if remembered.get("refuse"):
            _apply_refusal(decision, remembered.get("detail", ""))
        return decision

    try:
        decision.model_calls += 1
        verdict = get_chat_provider(settings).complete(
            [
                ChatMessage("system", ANSWERABILITY_SYSTEM),
                ChatMessage("user", ANSWERABILITY_USER.format(
                    question=question, passages=as_passages(decision.passed))),
            ],
            temperature=0.0,
            max_tokens=600,
        ).strip()
    except ProviderError as exc:
        # If the check cannot run, the similarity decision stands. Failing
        # closed here would turn a provider outage into blanket refusal.
        decision.answerability_verdict = f"unavailable ({exc})"
        # A rate limit is not transient within one question: the answer call
        # is about to meet the same wall, and retrying it doubles the delay
        # before the reader is told. Carry the fact forward so the caller can
        # stop now rather than spend the same wait twice.
        decision.provider_limited = getattr(exc, "rate_limited", False)
        return decision

    answerable, raw = _read_verdict(verdict)
    decision.answerability_verdict = raw[:300]

    if answerable is None:
        # Unreadable is not a refusal: the similarity gate already judged the
        # evidence relevant, and denying an answer on a malformed reply would
        # punish the reader for the model's formatting.
        decision.answerability_verdict = f"unreadable reply — kept: {raw[:180]}"
        return decision

    detail = " ".join(raw.splitlines()[1:]).strip()
    if not answerable:
        _apply_refusal(decision, detail)

    _save_verdict(settings, key, {
        "refuse": not answerable, "detail": detail, "verdict": raw[:300],
    })
    return decision


def _apply_refusal(decision: GateDecision, detail: str) -> None:
    decision.rejected = decision.passed + decision.rejected
    decision.passed = []
    decision.should_refuse = True
    decision.refused_by = "answerability"
    decision.reason = (
        "The passages retrieved are on a related topic but do not answer "
        "the question." + (f" {detail}" if detail else "")
    )


def retrieve_and_gate(query: str, settings: Settings | None = None,
                      limit: int | None = None) -> GateDecision:
    settings = settings or get_settings()
    candidates = hybrid.search(query, limit=limit, settings=settings)
    return verify_answerable(query, apply_gate(candidates, settings), settings)
