"""Deciding what the backend already knows, before spending a model call.

Every question used to cost two calls to the model: one asking "do these
passages answer this?", then one writing the answer. Measured against the
free tier that was 9,429 requested tokens for a question — Groq reserves
``max_tokens`` as well as the prompt — against a limit of 8,000 a minute. A
single question could not fit inside its own minute, so the second one
queued, and a reader waited two minutes to be told nothing.

The first call was also asking about things the backend had already decided.
Whether a source is authoritative, current, in the right jurisdiction, about
the right entity, materially relevant, and carries claims with provenance —
all of that is known here, deterministically, before any model sees it. Only
one question genuinely needs a model: *does this text, which is on the right
subject from the right authority, actually contain the answer?*

So the gate below answers what it can and says when it cannot:

``REFUSE``   nothing authoritative and relevant survived. No call needed; the
             answer is a stated source gap.
``ANSWER``   the evidence is strong and consistent. Go straight to writing,
             and validate the claims afterwards against the provenance layer,
             which is a better check than asking the model to mark its own
             homework beforehand.
``ASK_MODEL`` genuinely ambiguous — on-topic but possibly not answering. This
             is the case the answerability check was built for, and it still
             runs. It is meant to be the minority.

The point is not to remove the check. It is to stop paying for it on
questions whose answer the backend already had.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from app.config import Settings, get_settings


class Verdict(str, Enum):
    REFUSE = "refuse"
    ANSWER = "answer"
    ASK_MODEL = "ask_model"


# Measured, not guessed, over supported and unsupported questions against
# this corpus:
#
#   supported    best 0.58 0.61 0.66 0.68 0.69 0.71   passages 4-6
#   unsupported  best 0.39 0.43 0.50 0.50 0.53 0.58   passages 3-4
#
# The best-score ranges overlap exactly — "can I use a phone bill as proof of
# address" (supported) and "can I bring my dog on the TGV" (not) both score
# 0.58. That is the finding the answerability check was built on, and
# measuring it again only confirmed that no score alone separates the two.
#
# What does separate them here is how *much* of the corpus answers: a
# supported question is answered by five or six passages across several
# fiches, an unsupported one by three that merely share vocabulary. So the
# bar below is deliberately high and takes all three signals together. It
# settles the clearly-supported questions — the ones people actually ask —
# and sends everything else to the model, which is what that stage is for.
#: Best passage must clear this absolute score, not merely the gate's floor.
DECISIVE_SCORE = 0.65
#: And this many passages must support it, which is the signal that separated
#: the two sets most cleanly.
DECISIVE_SUPPORT = 5
#: Across at least this many distinct fiches, so one page repeated is not
#: mistaken for corroboration.
DECISIVE_DOCUMENTS = 3


@dataclass
class Assessment:
    verdict: Verdict
    reason: str = ""
    detail: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def needs_model(self) -> bool:
        return self.verdict is Verdict.ASK_MODEL


def assess(decision, *, selected: list | None = None,
           settings: Settings | None = None) -> Assessment:
    """What the backend can settle about this evidence on its own."""
    settings = settings or get_settings()
    checks: list[tuple[str, bool, str]] = []

    def note(name: str, passed: bool, detail: str = "") -> bool:
        checks.append((name, passed, detail))
        return passed

    passed = list(getattr(decision, "passed", []) or [])
    chosen = list(selected if selected is not None else passed)

    # 1. Did the similarity gate already refuse, for its own reasons?
    if getattr(decision, "should_refuse", False):
        note("similarity_gate", False, getattr(decision, "reason", ""))
        return Assessment(Verdict.REFUSE, "the similarity gate refused",
                          getattr(decision, "reason", ""), checks)
    note("similarity_gate", True)

    # 2. Is anything left after material relevance?
    if not chosen:
        note("materially_relevant", False, "no candidate survived selection")
        return Assessment(Verdict.REFUSE, "no relevant source",
                          "every candidate was about something else", checks)
    note("materially_relevant", True, f"{len(chosen)} source(s)")

    # 3. How strongly does the evidence actually support an answer?
    threshold = settings.relevance_threshold
    scores = sorted((getattr(h, "dense_score", 0.0) for h in chosen),
                    reverse=True)
    best = scores[0] if scores else 0.0
    supporting = sum(1 for s in scores if s >= threshold)
    if not note("support", supporting >= 1, f"{supporting} above threshold"):
        return Assessment(Verdict.REFUSE, "nothing cleared the threshold",
                          f"best {best:.2f} against {threshold:.2f}", checks)

    decisive = best >= DECISIVE_SCORE and supporting >= DECISIVE_SUPPORT
    note("decisive", decisive,
         f"best {best:.2f} against {DECISIVE_SCORE}, "
         f"{supporting} supporting against {DECISIVE_SUPPORT}")

    # 4. Several documents agreeing is worth more than one page repeated.
    documents = {(getattr(h, "metadata", {}) or {}).get("fiche_id", "")
                 for h in chosen}
    documents.discard("")
    independent = len(documents) >= DECISIVE_DOCUMENTS
    note("independent_sources", independent, f"{len(documents)} document(s)")

    if decisive and independent:
        return Assessment(Verdict.ANSWER, "evidence is decisive",
                          f"{supporting} passages from {len(documents)} "
                          f"documents, best {best:.2f}", checks)

    # On topic, but not clearly enough to skip the one question a model is
    # genuinely better at than a similarity score.
    return Assessment(Verdict.ASK_MODEL, "evidence is on topic but not decisive",
                      f"best {best:.2f}, {supporting} supporting, "
                      f"{len(documents)} document(s)", checks)
