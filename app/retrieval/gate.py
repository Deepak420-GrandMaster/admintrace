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

from dataclasses import dataclass, field

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


def verify_answerable(question: str, decision: GateDecision,
                      settings: Settings | None = None) -> GateDecision:
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

    from app.answer.cite import as_passages
    from app.llm import ChatMessage, ProviderError, get_chat_provider

    try:
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
        return decision

    first = verdict.splitlines()[0].strip().upper() if verdict else ""
    decision.answerability_verdict = verdict[:300]
    if first.startswith("NO"):
        detail = " ".join(verdict.splitlines()[1:]).strip()
        decision.rejected = decision.passed + decision.rejected
        decision.passed = []
        decision.should_refuse = True
        decision.refused_by = "answerability"
        decision.reason = (
            "The passages retrieved are on a related topic but do not answer "
            "the question." + (f" {detail}" if detail else "")
        )
    return decision


def retrieve_and_gate(query: str, settings: Settings | None = None,
                      limit: int | None = None) -> GateDecision:
    settings = settings or get_settings()
    candidates = hybrid.search(query, limit=limit, settings=settings)
    return verify_answerable(query, apply_gate(candidates, settings), settings)
