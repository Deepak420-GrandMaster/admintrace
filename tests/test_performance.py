"""What an answer is allowed to cost.

The measurements behind these: Groq reserves ``max_tokens`` against its
per-minute budget as well as charging the prompt, so a 429 reads "Limit 8000,
Used 5445, Requested 6193". Two calls per question — one asking whether the
passages answered, one writing the answer — came to ~9,400 requested tokens
against a limit of 8,000 a minute. A single question could not fit inside its
own minute, so the next one queued and a reader waited two minutes for
nothing. These tests keep that from coming back.
"""

from __future__ import annotations

import pytest

from app.answer import evidence
from app.answer.evidence import (
    DECISIVE_DOCUMENTS, DECISIVE_SCORE, DECISIVE_SUPPORT, Verdict, assess,
)
from app.config import get_settings
from app.retrieval.types import Retrieved


def hit(score: float, fiche: str) -> Retrieved:
    return Retrieved(chunk_id=f"{fiche}-1", text="x",
                     metadata={"fiche_id": fiche, "fiche_title_fr": "t"},
                     dense_score=score)


class FakeDecision:
    def __init__(self, passed, should_refuse=False, reason=""):
        self.passed = passed
        self.rejected = []
        self.should_refuse = should_refuse
        self.reason = reason


# ------------------------------------------------- what costs a model call --

def test_strong_evidence_is_settled_without_a_model():
    """§36: a well-supported question spends one call, not two."""
    passed = [hit(DECISIVE_SCORE + 0.05, f"F{i}") for i in range(DECISIVE_SUPPORT)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ANSWER
    assert not verdict.needs_model


def test_nothing_relevant_is_refused_without_a_model():
    """A refusal the backend can already make costs nothing."""
    verdict = assess(FakeDecision([hit(0.8, "F1")]), selected=[])
    assert verdict.verdict is Verdict.REFUSE
    assert not verdict.needs_model
    assert "about something else" in verdict.detail


def test_a_gate_refusal_needs_no_second_opinion():
    decision = FakeDecision([], should_refuse=True, reason="below threshold")
    verdict = assess(decision, selected=[])
    assert verdict.verdict is Verdict.REFUSE
    assert not verdict.needs_model


def test_weak_evidence_still_asks_the_model():
    """The judgement a similarity score cannot make is still bought.

    Measured over this corpus, supported and unsupported questions overlap
    exactly on best score — "can I use a phone bill as proof of address"
    (supported) and "can I bring my dog on the TGV" (not) both score 0.58.
    So anything short of decisive goes to the model, which is the stage that
    exists for precisely this case.
    """
    passed = [hit(DECISIVE_SCORE - 0.05, f"F{i}") for i in range(DECISIVE_SUPPORT)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL
    assert verdict.needs_model


def test_one_page_repeated_is_not_corroboration():
    """Five passages from one fiche is one source, not five."""
    passed = [hit(0.9, "F1") for _ in range(DECISIVE_SUPPORT + 2)]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL, \
        "one document was mistaken for several agreeing sources"


def test_a_single_strong_passage_does_not_carry_an_answer():
    passed = [hit(0.95, "F1")]
    verdict = assess(FakeDecision(passed), selected=passed)
    assert verdict.verdict is Verdict.ASK_MODEL


def test_the_decisive_bar_stays_high():
    """A regression guard on the constants themselves.

    Lowering these is how the expensive check quietly stops running on
    questions that needed it. The values came from measurement; changing
    them should mean measuring again.
    """
    assert DECISIVE_SCORE >= 0.60, "the decisive score bar was lowered"
    assert DECISIVE_SUPPORT >= 4, "fewer supporting passages now skip the check"
    assert DECISIVE_DOCUMENTS >= 2, "one document could now carry an answer"


# -------------------------------------------------------- the token budget --

def test_the_answer_ceiling_is_sized_to_the_length_policy():
    """§11: not a 2000-token budget for a 300-word answer.

    The ceiling is reserved against the provider's rate limit whether or not
    it is spent, so an over-generous one is paid for on every question.
    """
    from app.answer.generate import ANSWER_TOKEN_CEILING, EVIDENCE_PASSAGES

    assert ANSWER_TOKEN_CEILING <= 1200, "the ceiling is back above the policy"
    # 300 words plus reasoning still has to fit; measured at 546.
    assert ANSWER_TOKEN_CEILING >= 700, "too tight for a COMPLEX answer"
    assert EVIDENCE_PASSAGES <= 5, "the prompt is carrying the whole result set"


def test_the_model_is_shown_what_will_be_cited():
    """The prompt and the citations must be the same evidence.

    They were not: the model saw everything the similarity gate passed while
    the citations listed only what survived relevance, so an answer could
    lean on a page the reader was never shown.
    """
    from app.answer.generate import _evidence_for

    class D:
        passed = [hit(0.9, "F1"), hit(0.8, "F2")]
        selected = [hit(0.9, "F1")]

    shown = _evidence_for(D())
    assert [h.metadata["fiche_id"] for h in shown] == ["F1"]


# ------------------------------------------------------------- telemetry ----

def test_a_trace_times_its_phases():
    from app.telemetry import Trace

    trace = Trace()
    with trace.phase("retrieval"):
        pass
    assert "retrieval" in trace.phases
    assert trace.retrieval_time >= 0


def test_targets_exist_for_every_response_class():
    from app.answer.length import AnswerClass
    from app.telemetry import TARGET_SECONDS

    for klass in AnswerClass:
        assert klass.name in TARGET_SECONDS, klass.name


def test_provider_health_counts_what_went_wrong():
    from app.telemetry import provider_health

    health = provider_health([
        {"rate_limited": True, "retry_after": 30, "provider": "groq"},
        {"rate_limited": False, "provider": "ollama", "empty_answer": True},
        {"rate_limited": False, "provider": "groq"},
    ])
    assert health["rate_limit_count"] == 1
    assert health["fallback_count"] == 1
    assert health["empty_answers"] == 1
    assert health["rate_limit_wait_max"] == 30


def test_percentiles_are_not_nonsense_on_small_samples():
    from app.telemetry import percentile

    assert percentile([], 0.5) == 0.0
    assert percentile([4.0], 0.95) == 4.0
    assert percentile([1.0, 2.0, 3.0], 0.5) == 2.0


# ------------------------------------------- the live-source failure path --

def _live_result():
    from app.sources.live import Evidence, LiveResult
    from app.sources.registry import by_id
    from app.sources.store import Freshness

    source = by_id("mbs")
    page = Evidence(source_id="mbs", source_name=source.name,
                    domain=source.domain, url=source.base_url,
                    canonical_url=source.base_url, title="Admissions",
                    text=("Conditions d'admission.\n"
                          "Les candidatures se font sur Parcoursup.\n") * 20,
                    retrieved_at="2026-09-16T10:00:00+00:00",
                    content_hash="abc", freshness=Freshness.FRESH,
                    version_id="v1")
    return LiveResult(entity_id="mbs", source=source, evidence=[page],
                      sources=[source], freshness=Freshness.FRESH)


class _Provider:
    def __init__(self, *, raises=None, returns=""):
        self.raises, self.returns = raises, returns

    def complete(self, *args, **kwargs):
        if self.raises:
            raise self.raises
        return self.returns


def test_a_quota_refusal_on_a_live_answer_is_reported_as_one(monkeypatch, tmp_path):
    """The rate limit must survive the trip to the renderer.

    This path used to keep only the error text, so the renderer could not
    tell a quota refusal from a crash and printed the provider's raw message
    — organisation id included — instead of the rate-limit notice.
    """
    import dataclasses

    import app.answer.from_source as from_source
    from app.config import get_settings
    from app.llm.base import ProviderError

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    limited = ProviderError("Groq rate limit reached", rate_limited=True,
                            retry_after=292.0)
    monkeypatch.setattr(from_source, "get_chat_provider",
                        lambda s: _Provider(raises=limited))

    result = from_source.answer_from_source(
        "What are the admission requirements at MBS?", _live_result(),
        settings=settings)
    assert result.rate_limited, "the quota refusal was flattened into an error"
    assert result.retry_after == 292.0
    assert not result.text


def test_an_empty_live_reply_is_a_failure_not_a_refusal(monkeypatch, tmp_path):
    """§33. Blank is the provider failing, not the pages declining.

    Reading it as a refusal would tell the reader "the sources don't cover
    this" about sources that were never actually consulted.
    """
    import dataclasses

    import app.answer.from_source as from_source
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    monkeypatch.setattr(from_source, "get_chat_provider",
                        lambda s: _Provider(returns="   \n "))

    result = from_source.answer_from_source(
        "What are the admission requirements at MBS?", _live_result(),
        settings=settings)
    assert result.error, "an empty reply produced no error"
    assert not result.refused, "a provider failure was shown as a source gap"


def test_a_live_answer_is_timed_like_any_other(monkeypatch, tmp_path):
    """§30. The live path recorded nothing, so its latency was invisible."""
    import dataclasses

    import app.answer.from_source as from_source
    from app import telemetry
    from app.config import get_settings

    from app.answer import claimcheck

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    monkeypatch.setattr(from_source, "get_chat_provider",
                        lambda s: _Provider(returns="Apply through Parcoursup."))
    # Same-subject by construction; this test is about timing, not meaning,
    # and loading the real embedder would cost it ten seconds.
    monkeypatch.setattr(claimcheck, "embedding_similarity",
                        lambda a, b: [[0.9] * len(b) for _ in a])
    result = from_source.answer_from_source(
        "What are the admission requirements at MBS?", _live_result(),
        settings=settings)

    rows = telemetry.traces(settings)
    assert rows, "a live-source answer left no timing behind"
    assert rows[-1]["evidence_verdict"] == "live_source"
    # A supported answer costs exactly one model call: no repair was needed.
    assert result.text == "Apply through Parcoursup."
    assert rows[-1]["model_calls"] == 1
    assert rows[-1]["claims_supported"] == rows[-1]["claims_generated"] == 1


def test_a_provider_error_never_prints_account_details_to_a_reader():
    """The organisation id is an account detail, not an error message."""
    from app.ui.render import error_html

    raw = ("Groq rate limit reached and the limit has 292s left to run. Rate "
           "limit reached for model `openai/gpt-oss-120b` in organization "
           "`org_01k1gkac99fcs9bny7wes1ka8x` service tier `on_demand`. Need "
           "more tokens? Upgrade to Dev Tier today at "
           "https://console.groq.com/settings/billing")
    shown = error_html(raw)
    assert "org_01k1" not in shown
    assert "console.groq.com" not in shown
    assert "Upgrade" not in shown
    # The useful part survives.
    assert "292s left" in shown


def test_a_rate_limit_is_not_also_counted_as_an_empty_answer():
    """Two counters for one event reads as two problems."""
    from app.telemetry import provider_health

    health = provider_health([
        {"rate_limited": True, "empty_answer": True, "retry_after": 60},
        {"rate_limited": False, "empty_answer": True},
    ])
    assert health["rate_limit_count"] == 1
    assert health["empty_answers"] == 1, "a quota refusal was double-counted"
