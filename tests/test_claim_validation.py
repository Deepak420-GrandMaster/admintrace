"""The evidence decides what an answer may say.

Found live: asked how to validate a long-stay visa in Antibes, the local model
answered that it must be done "three to four months before expiry" and that
"the clock starts the day you land". The first was a renewal deadline from a
préfecture renewal page; the second was in no evidence at all. Provenance
said, correctly, that the answer came from official pages. Nothing said it
misrepresented them. These tests are that check.

Most use a similarity stub that calls everything the same subject. That is
deliberate: it proves the verdicts below come from facts and procedure
identity, not from similarity — which, measured, cannot tell validation from
renewal (0.715 against 0.665). The tests that need meaning across English and
French load the real multilingual embedder.
"""

from __future__ import annotations

import dataclasses
import time
from datetime import date

import pytest

from app.answer import claimcheck
from app.answer.claimcheck import (
    Action, ClaimType, EvidenceText, Support, check_and_repair, classify_claim,
    extract_facts, filter_by_procedure, validate,
)
from app.answer.procedures import detect as procedure_of
from app.sources.jurisdiction import resolve_reply


def same_subject(claims, sentences):
    return [[0.9] * len(sentences) for _ in claims]


VALIDATION_Q = "how to validate the visa"

# The renewal sentence is the one the préfecture des Alpes-Maritimes actually
# publishes, as read live; the validation one is the regression fixture from
# the brief, deliberately free of any figure.
RENEWAL = EvidenceText(
    source_id="prefecture-alpes-maritimes",
    url="https://www.alpes-maritimes.gouv.fr/Demarches/Immigration-et-integration/"
        "Titres-de-sejour-et-documents-de-voyage/Demander-un-titre-de-sejour/"
        "Immigration-familiale/Renouvellement",
    title="Demande de renouvellement - Renouvellement - Immigration familiale",
    text=("Les demandes de renouvellement de VLS-TS et de titre de séjour "
          "doivent être déposées en ligne, 3 à 4 mois avant la fin de validité "
          "du titre de séjour."),
    version_id="v-renewal", jurisdiction_area="Alpes-Maritimes", is_local=True)

VALIDATION = EvidenceText(
    source_id="anef",
    url="https://administration-etrangers-en-france.interieur.gouv.fr/",
    title="Accueil | Administration numérique pour les étrangers en France (ANEF)",
    text="Je valide mon VLS-TS\nValidez votre VLS-TS en ligne après votre arrivée en France.",
    version_id="v-anef")


def texts(validation):
    return {c.text: c for c in validation.claims}


# ------------------------------------------------------------ normalising --

@pytest.mark.parametrize("a,b", [
    ("trois mois", "3 months"), ("un mois", "1 month"), ("quinze jours", "15 days"),
    ("two weeks", "14 days"), ("3 à 4 mois", "between three and four months"),
    ("six mois", "6 months"),
])
def test_durations_mean_the_same_thing_however_they_are_written(a, b):
    """§11–12. Words or digits, French or English, one duration."""
    assert extract_facts(a).quantities == extract_facts(b).quantities


def test_three_months_and_six_months_are_different():
    assert extract_facts("trois mois").quantities != extract_facts("6 months").quantities


@pytest.mark.parametrize("a,b", [("225 €", "€225"), ("225 euros", "225 EUR"),
                                  ("1 200 €", "€1200")])
def test_an_amount_is_the_same_amount_whichever_side_the_sign_is(a, b):
    assert extract_facts(a).amounts == extract_facts(b).amounts


def test_dates_compare_across_formats():
    assert extract_facts("30 September 2026").dates == extract_facts("le 30/09/2026").dates
    assert extract_facts("30 septembre 2026").dates == {"2026-09-30"}


def test_what_a_deadline_counts_from_is_read_in_both_languages():
    assert "before_expiry" in extract_facts("3 months before your visa expires").anchors
    assert "before_expiry" in extract_facts("3 mois avant la fin de validité").anchors
    assert "after_arrival" in extract_facts("the clock starts the day you land").anchors
    assert "after_arrival" in extract_facts("dans les trois mois suivant votre arrivée").anchors


# ------------------------------------------------------- procedure identity --

def test_a_renewal_that_names_the_vls_ts_is_still_a_renewal():
    """§13. The sentence that caused the bug, classified correctly."""
    assert procedure_of(RENEWAL.text) == {"residence_permit_renewal"}
    assert procedure_of("Je valide mon VLS-TS") == {"vls_ts_validation"}
    assert procedure_of("la fin de validité du titre") == frozenset()


def test_a_renewal_page_is_not_offered_as_evidence_for_validation():
    """§14. Filtered before the model ever sees it."""
    kept, dropped = filter_by_procedure([RENEWAL, VALIDATION],
                                        procedure_of(VALIDATION_Q))
    assert VALIDATION in kept and RENEWAL in dropped


# ---------------------------------------------- the exact Antibes regression --

MIXED_ANSWER = (
    "You validate your VLS-TS online on ANEF. "
    "Validate it 3 to 4 months before your visa expires. "
    "Don't leave this one — the clock starts the day you land.")


def test_a_renewal_deadline_is_not_accepted_as_a_validation_timing():
    """§15, §43. The exact mix, caught."""
    v = validate(MIXED_ANSWER, [RENEWAL, VALIDATION], question=VALIDATION_Q,
                 similarity=same_subject)
    claims = texts(v)

    deadline = claims["Validate it 3 to 4 months before your visa expires."]
    assert deadline.support == Support.CONTRADICTED.value
    assert "procedure_mix" in deadline.reason
    assert "renewal" in deadline.reason
    assert deadline.action == Action.REMOVED.value

    assert "3 to 4 months" not in v.text
    assert "ANEF" in v.text, "the supported claim was lost with the bad one"


def test_a_claim_that_names_two_procedures_is_rejected():
    """§16. "Validate by filing the renewal request" blends two."""
    v = validate("You validate your VLS-TS by submitting the renewal request online.",
                 [RENEWAL, VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    claim = v.claims[0]
    assert claim.support == Support.CONTRADICTED.value
    assert "procedure_mix" in claim.reason


def test_a_timing_that_appears_in_no_evidence_is_not_kept():
    """The second half of the bug: "the clock starts the day you land"."""
    renewal_only = dataclasses.replace(VALIDATION, text="Je valide mon VLS-TS")
    v = validate("Don't leave this one — the clock starts the day you land.",
                 [RENEWAL, renewal_only], question=VALIDATION_Q,
                 similarity=same_subject)
    claim = v.claims[0]
    assert claim.action == Action.REMOVED.value
    assert "after_arrival" in claim.reason


def test_a_timing_the_evidence_does_state_is_kept():
    v = validate("Validate your VLS-TS online after you arrive in France.",
                 [RENEWAL, VALIDATION], question=VALIDATION_Q,
                 similarity=same_subject)
    assert v.claims[0].support == Support.SUPPORTED.value


def test_the_exact_antibes_mix_is_caught_through_the_live_answer_path(monkeypatch, tmp_path):
    """§43. Must fail if claim validation is disabled.

    Reads the real settings — so running with CLAIM_VALIDATION=false makes
    this fail, which is the point. The companion test below proves the bad
    claim really would reach a reader without the check.
    """
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    result = _answer_through_live_path(monkeypatch, settings, MIXED_ANSWER)
    assert "3 to 4 months" not in result.text, "the renewal deadline reached the reader"
    assert result.validation["claims_contradicted"] >= 1


def test_without_validation_the_mix_would_reach_the_reader(monkeypatch, tmp_path):
    """Proves the test above is testing something."""
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path,
                                   claim_validation=False)
    result = _answer_through_live_path(monkeypatch, settings, MIXED_ANSWER,
                                       offer_renewal_page=True)
    assert "3 to 4 months" in result.text


def _answer_through_live_path(monkeypatch, settings, reply, offer_renewal_page=False):
    import app.answer.from_source as from_source
    from app.sources.live import Evidence, LiveResult
    from app.sources.registry import by_id
    from app.sources.store import Freshness

    def evidence(item):
        return Evidence(source_id=item.source_id, source_name=item.source_id,
                        domain="x", url=item.url, canonical_url=item.url,
                        title=item.title, text=item.text,
                        retrieved_at="2026-09-16T10:00:00+00:00",
                        content_hash="h", freshness=Freshness.FRESH,
                        version_id=item.version_id,
                        jurisdiction_area=item.jurisdiction_area)

    class Provider:
        def complete(self, *a, **k):
            return reply

    monkeypatch.setattr(from_source, "get_chat_provider", lambda s: Provider())
    monkeypatch.setattr(claimcheck, "embedding_similarity", same_subject)
    items = [evidence(RENEWAL), evidence(VALIDATION)]
    if not settings.claim_validation and offer_renewal_page:
        # With validation off, show the model everything, as before the fix.
        monkeypatch.setattr(claimcheck, "filter_by_procedure",
                            lambda ev, asked: (list(ev), []))
    live = LiveResult(entity_id="anef", source=by_id("anef"), evidence=items,
                      sources=[by_id("anef")], freshness=Freshness.FRESH)
    return from_source.answer_from_source(VALIDATION_Q, live, settings=settings)


# ----------------------------------------------------- facts, checked exactly --

NATIONAL_FEE = EvidenceText(source_id="service-public", url="https://sp/fee",
                            title="Titre de séjour : taxe",
                            text="La taxe pour ce titre est de 225 €.")


def test_a_wrong_amount_is_contradicted_not_rounded():
    """§10. €250 is not €225."""
    v = validate("The fee for this permit is €250.", [NATIONAL_FEE],
                 question="how much does the residence permit cost",
                 similarity=same_subject)
    assert v.claims[0].claim_type == ClaimType.FEE.value
    assert v.claims[0].support == Support.CONTRADICTED.value
    assert "value_mismatch" in v.claims[0].reason


def test_the_right_amount_is_supported_in_either_notation():
    v = validate("The fee for this permit is 225 euros.", [NATIONAL_FEE],
                 question="how much does the residence permit cost",
                 similarity=same_subject)
    assert v.claims[0].support == Support.SUPPORTED.value


def test_a_different_duration_on_the_same_procedure_is_contradicted():
    six = dataclasses.replace(RENEWAL, text=RENEWAL.text.replace("3 à 4 mois", "six mois"))
    v = validate("Renew it 3 to 4 months before your permit expires.", [six],
                 question="how do I renew my residence permit",
                 similarity=same_subject)
    assert v.claims[0].support == Support.CONTRADICTED.value


def test_a_date_is_matched_across_notations():
    dated = EvidenceText(source_id="mbs", url="https://mbs/admission", title="Admissions",
                         text="Le justificatif doit être fourni au plus tard le 30/09/2026.")
    ok = validate("You must provide the proof by 30 September 2026.", [dated],
                  question="what do I need for admission", similarity=same_subject)
    wrong = validate("You must provide the proof by 31 October 2026.", [dated],
                     question="what do I need for admission", similarity=same_subject)
    assert ok.claims[0].support == Support.SUPPORTED.value
    assert wrong.claims[0].support == Support.CONTRADICTED.value


# ------------------------------------------------------ portals, authorities --

def test_an_invented_portal_address_is_removed_but_the_sentence_survives():
    """§18. The URL goes; the supported instruction around it stays."""
    v = validate("Validate your VLS-TS online after you arrive in France at "
                 "https://administration-etrangers-en-france.interieur.gouv.fr/particuliers/#/validation.",
                 [VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    claim = v.claims[0]
    assert claim.action == Action.URL_REMOVED.value
    assert "particuliers" not in v.text
    assert "after you arrive" in v.text


def test_a_portal_the_evidence_gives_is_kept():
    v = validate(f"Start at {VALIDATION.url} and validate your VLS-TS.",
                 [VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    assert VALIDATION.url in v.text


def test_an_authority_must_be_named_by_the_evidence_not_inferred_from_its_domain():
    """§17. A .gouv.fr page is not a statement that the préfecture handles this."""
    v = validate("The préfecture handles this validation.", [VALIDATION],
                 question=VALIDATION_Q, similarity=same_subject)
    assert v.claims[0].action == Action.REMOVED.value
    assert "entity_not_in_evidence" in v.claims[0].reason


# ------------------------------------------------------------- temporality --

def test_a_rule_not_yet_in_force_is_not_stated_as_current():
    """§20."""
    future = EvidenceText(source_id="service-public", url="https://sp/rule", title="Règle",
                          text="À compter du 1er juillet 2026, la demande se fait en ligne.")
    claim = "The application is made online."
    june = validate(claim, [future], question="how do I apply", on=date(2026, 6, 15),
                    similarity=same_subject)
    september = validate(claim, [future], question="how do I apply",
                         on=date(2026, 9, 15), similarity=same_subject)
    assert june.claims[0].action == Action.REMOVED.value
    assert "not yet in force" in june.claims[0].reason
    assert september.claims[0].action == Action.KEPT.value


# --------------------------------------------------------------- conflicts --

def test_two_national_sources_with_different_figures_are_not_averaged():
    """§21. Unresolvable: neither is chosen, and the answer says less."""
    a = EvidenceText(source_id="service-public", url="https://sp/a", title="Taxe",
                     text="La taxe est de 225 €.")
    b = EvidenceText(source_id="anef", url="https://anef/b", title="Taxe",
                     text="La taxe est de 200 €.")
    v = validate("The tax is €225.", [a, b], question="what is the tax",
                 similarity=same_subject)
    assert v.claims[0].support == Support.CONTRADICTED.value
    assert v.conflicting


def test_a_local_authority_outranks_the_national_page_on_its_own_counter():
    national = EvidenceText(source_id="service-public", url="https://sp/a", title="Taxe",
                            text="La taxe est de 200 €.")
    local = EvidenceText(source_id="prefecture-alpes-maritimes", url="https://am/b",
                         title="Taxe", text="La taxe est de 225 €.",
                         jurisdiction_area="Alpes-Maritimes", is_local=True)
    kept = validate("The tax is €225.", [national, local], question="what is the tax",
                    similarity=same_subject)
    assert kept.claims[0].support == Support.SUPPORTED.value


# -------------------------------------------------------------- jurisdiction --

def test_another_departments_prefecture_cannot_support_an_antibes_answer():
    """§34."""
    rhone = dataclasses.replace(VALIDATION, source_id="prefecture-rhone",
                                jurisdiction_area="Rhône", is_local=True)
    alpes = dataclasses.replace(VALIDATION, source_id="prefecture-alpes-maritimes",
                                jurisdiction_area="Alpes-Maritimes", is_local=True)
    claim = "Validate your VLS-TS online after you arrive in France."
    place = resolve_reply("Antibes")
    assert validate(claim, [rhone], question=VALIDATION_Q, place=place,
                    similarity=same_subject).claims[0].action == Action.REMOVED.value
    assert validate(claim, [alpes], question=VALIDATION_Q, place=place,
                    similarity=same_subject).claims[0].action == Action.KEPT.value


# ------------------------------------------------------ MBS, CAF, wrong source --

MBS = EvidenceText(
    source_id="mbs", url="https://www.mbs-education.com/admissions",
    title="Admissions - MBS School of Business",
    text=("Les candidatures au Bachelor se font sur Parcoursup.\n"
          "Un test d'anglais est requis : TOEIC, IELTS ou MBS English Test."))


def test_an_mbs_answer_keeps_what_mbs_says_and_drops_what_it_does_not():
    """§31."""
    answer = ("You apply through Parcoursup. "
              "You need an English test such as TOEIC or IELTS. "
              "You also need 60 ECTS from a previous year.")
    v = validate(answer, [MBS], question="What do I need for admission?",
                 similarity=same_subject)
    assert "Parcoursup" in v.text
    assert "TOEIC" in v.text
    assert "ECTS" not in v.text, "a requirement MBS never stated survived"


def test_crous_information_is_never_turned_into_a_caf_requirement():
    """§32. CAF is blocked; its policy cannot be borrowed from the CROUS page."""
    crous = EvidenceText(source_id="etudiant-gouv", url="https://etudiant.gouv.fr/logement",
                         title="Logement Crous",
                         text="Constituez votre dossier social étudiant sur messervices.")
    v = validate("The CAF requires you to have a signed lease before applying.",
                 [crous], question="how do I get housing benefit",
                 similarity=same_subject)
    assert v.claims[0].action == Action.REMOVED.value
    assert "CAF" in v.claims[0].reason


def test_a_generic_page_cannot_support_institution_specific_claims():
    """§33. Service-Public says nothing about MBS's own admission route."""
    generic = EvidenceText(source_id="service-public", url="https://sp/etudes",
                           title="Études supérieures",
                           text="Les établissements fixent leurs propres conditions.")
    v = validate("You apply to MBS through Parcoursup.", [generic],
                 question="What do I need for admission to MBS?",
                 similarity=same_subject)
    assert v.claims[0].action == Action.REMOVED.value


# -------------------------------------------------------- repair, never blank --

def test_one_bad_claim_is_removed_and_the_rest_keeps_its_shape():
    """§24. Remove, don't discard — and keep the list a list."""
    answer = ("## How to do it\n"
              "- Validate your VLS-TS online after you arrive in France.\n"
              "- Validate it 3 to 4 months before your visa expires.")
    v = validate(answer, [RENEWAL, VALIDATION], question=VALIDATION_Q,
                 similarity=same_subject)
    assert v.text.startswith("## How to do it")
    assert "- Validate your VLS-TS online after you arrive in France." in v.text
    assert "3 to 4 months" not in v.text
    assert not v.needs_repair


def test_when_nothing_survives_one_repair_is_tried():
    calls = []

    def regenerate(usable):
        calls.append([e.source_id for e in usable])
        return "Validate your VLS-TS online after you arrive in France."

    v, repairs = check_and_repair("Validate it 3 to 4 months before your visa expires.",
                                  [RENEWAL, VALIDATION], question=VALIDATION_Q,
                                  regenerate=regenerate, similarity=same_subject)
    assert repairs == 1 and len(calls) == 1
    assert calls[0] == ["anef"], "the repair was shown the renewal page"
    assert "after you arrive" in v.text


def test_a_repair_that_fails_again_is_not_retried_and_is_never_blank():
    """§25. The honest outcome is 'could not verify', not an empty bubble."""
    v, repairs = check_and_repair("Validate it 3 to 4 months before your visa expires.",
                                  [RENEWAL, VALIDATION], question=VALIDATION_Q,
                                  regenerate=lambda usable: "Validate it 6 months before.",
                                  similarity=same_subject)
    assert repairs == 1
    assert v.text == "" and v.needs_repair


def test_a_repair_that_errors_leaves_the_honest_outcome(monkeypatch):
    def broken(usable):
        raise RuntimeError("provider down")

    v, repairs = check_and_repair("Validate it 3 to 4 months before your visa expires.",
                                  [RENEWAL, VALIDATION], question=VALIDATION_Q,
                                  regenerate=broken, similarity=same_subject)
    assert repairs == 1 and v.needs_repair


def test_validation_never_makes_an_answer_longer():
    """§29."""
    v = validate(MIXED_ANSWER, [RENEWAL, VALIDATION], question=VALIDATION_Q,
                 similarity=same_subject)
    assert len(v.text.split()) <= len(MIXED_ANSWER.split())


def test_connective_text_is_kept_and_not_counted_as_a_claim():
    v = validate("The page doesn't explain how long validation takes.",
                 [VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    assert v.claims[0].claim_type == ClaimType.NON_FACTUAL.value
    assert v.generated == 0 and v.text


# ------------------------------------------------------------------ privacy --

def test_the_claim_log_never_stores_what_the_answer_said(tmp_path):
    """The reader was promised their question is not kept."""
    from app.config import get_settings
    from app.sources import store

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path)
    v = validate(MIXED_ANSWER, [RENEWAL, VALIDATION], question=VALIDATION_Q,
                 similarity=same_subject)
    claimcheck.record(v, path="test", settings=settings)
    log = (store.root(settings) / "audit.jsonl").read_text(encoding="utf-8")
    assert "answer.claims" in log
    assert "clock starts" not in log and "before your visa expires" not in log
    assert "procedure_mix" in log


# -------------------------------------------------------------- performance --

def test_validation_is_milliseconds_not_another_model_call():
    """§36. With vectors in hand, checking is arithmetic."""
    evidence = [RENEWAL, VALIDATION, MBS, NATIONAL_FEE] * 5
    validate(MIXED_ANSWER, evidence, question=VALIDATION_Q, similarity=same_subject)
    started = time.perf_counter()
    for _ in range(10):
        validate(MIXED_ANSWER, evidence, question=VALIDATION_Q, similarity=same_subject)
    per_answer = (time.perf_counter() - started) / 10
    assert per_answer < 0.05, f"{per_answer * 1000:.0f} ms per answer"


def test_the_local_model_does_not_bypass_validation(monkeypatch, tmp_path):
    """§37. Fallback is not an exemption."""
    from app.config import get_settings

    settings = dataclasses.replace(get_settings(), data_dir=tmp_path,
                                   llm_provider="ollama",
                                   ollama_chat_model="qwen3:8b")
    result = _answer_through_live_path(monkeypatch, settings, MIXED_ANSWER)
    assert "3 to 4 months" not in result.text


# ------------------------------------------ meaning across languages (real) --

@pytest.fixture(scope="module")
def embedder():
    try:
        claimcheck.embedding_similarity(["warm"], ["up"])
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"embedding model unavailable: {exc}")
    return claimcheck.embedding_similarity


def test_an_english_claim_is_supported_by_a_french_source(embedder):
    """§35. The claim stays source-backed across the language boundary."""
    v = validate("Validate your VLS-TS online after you arrive in France.",
                 [VALIDATION], question=VALIDATION_Q, similarity=embedder)
    assert v.claims[0].support == Support.SUPPORTED.value


def test_a_french_answer_is_checked_the_same_way(embedder):
    v = validate("Validez votre VLS-TS en ligne après votre arrivée en France. "
                 "Faites-le 3 à 4 mois avant la fin de validité.",
                 [RENEWAL, VALIDATION], question="comment valider mon visa",
                 similarity=embedder)
    assert "après votre arrivée" in v.text
    assert "3 à 4 mois" not in v.text


def test_an_unrelated_claim_is_not_supported_by_meaning(embedder):
    v = validate("You can bring your dog on the train.", [VALIDATION],
                 question=VALIDATION_Q, similarity=embedder)
    assert v.claims[0].action == Action.REMOVED.value


# ------------------------------------ what the live MBS answer exposed --

# Lines as the MBS admissions page actually lays them out: the fee is a
# fragment of its own, under the Parcoursup heading, beside a tuition figure.
MBS_PAGE = EvidenceText(
    source_id="mbs", url="https://www.mbs-education.com/admissions-bachelor",
    title="Admissions Bachelor - MBS School of Business",
    text=("L’admission des candidats se fait sur concours, uniquement via Parcoursup.\n"
          "Candidater sur Parcoursup\n"
          "60€ (30€ pour les boursiers)\n"
          "Pas de niveau minimum exigé sauf pour le parcours anglophone, un score "
          "minimum de 85/160 sera exigé.\n"
          "Entretien de personnalité de 25 minutes.\n"
          "Frais de scolarité annuels : 2000 €\n"))


def fragments_look_unrelated(claims, sentences):
    """Realistic: a bare fee line reads as off-subject; full sentences do not."""
    return [[0.3 if len(s.split()) < 5 else 0.8 for s in sentences] for _ in claims]


def test_a_fee_on_its_own_table_line_is_supported_by_its_neighbours():
    """The €60 fee was removed live because its line was a bare fragment."""
    v = validate("You pay the €60 contest fee (€30 for scholarship holders) on Parcoursup.",
                 [MBS_PAGE], question="What do I need for admission?",
                 similarity=fragments_look_unrelated)
    assert v.claims[0].support == Support.SUPPORTED.value, v.claims[0].reason
    assert "€60" in v.text


def test_a_wrong_fee_is_contradicted_by_the_fee_line_not_the_tuition_line():
    """The diagnosis must point at the figure that is actually about fees.

    Live, a correct €60 fee was labelled contradicted by a 2000 € tuition line.
    A wrong fee should be contradicted — by the 60€ line, which is on subject.
    """
    v = validate("You pay a €90 contest fee.", [MBS_PAGE],
                 question="What do I need for admission?",
                 similarity=fragments_look_unrelated)
    claim = v.claims[0]
    assert claim.action == Action.REMOVED.value
    assert claim.support == Support.CONTRADICTED.value
    assert "60 EUR" in claim.reason and "2000" not in claim.reason, claim.reason


def test_an_unrelated_figure_alone_is_unsupported_not_contradicted():
    """With nothing on subject stating a figure, there is no contradiction."""
    page = dataclasses.replace(MBS_PAGE, text="Frais de scolarité annuels : 2000 €\n")
    v = validate("You pay a €90 contest fee.", [page],
                 question="What do I need for admission?",
                 similarity=lambda c, s: [[0.6] * len(s) for _ in c])
    assert v.claims[0].support == Support.UNSUPPORTED.value, v.claims[0].reason


def test_an_invented_conversion_in_brackets_is_dropped_and_the_real_score_kept():
    """Live: "(a score of 60/160 is roughly a 10/20)" appeared in no evidence."""
    v = validate("The English-only track needs at least 85/160 "
                 "(a score of 60/160 is roughly a 10/20).",
                 [MBS_PAGE], question="What do I need for admission?",
                 similarity=same_subject)
    claim = v.claims[0]
    assert claim.action == Action.CLAUSE_REMOVED.value, claim.reason
    assert "85/160" in v.text
    assert "10/20" not in v.text and "60/160" not in v.text


def test_a_score_the_page_does_not_state_is_not_kept():
    v = validate("The English-only track needs at least 90/160.", [MBS_PAGE],
                 question="What do I need for admission?", similarity=same_subject)
    assert v.claims[0].action == Action.REMOVED.value


def test_minutes_are_checked_like_any_other_duration():
    """Live: "a 25-minute interview" went unchecked; minutes were not a unit."""
    right = validate("There is a 25‑minute personality interview.", [MBS_PAGE],
                     question="What do I need for admission?", similarity=same_subject)
    wrong = validate("There is a 40-minute personality interview.", [MBS_PAGE],
                     question="What do I need for admission?", similarity=same_subject)
    assert right.claims[0].action == Action.KEPT.value
    assert wrong.claims[0].action == Action.REMOVED.value


def test_a_sentence_without_a_listed_verb_is_still_checked():
    """Live: "If you miss that deadline, you can't continue" was never checked."""
    v = validate("If you miss that deadline, you can't continue.", [MBS_PAGE],
                 question="What do I need for admission?",
                 similarity=lambda c, s: [[0.2] * len(s) for _ in c])
    assert v.claims[0].claim_type != ClaimType.NON_FACTUAL.value
    assert v.claims[0].action == Action.REMOVED.value


def test_a_list_introduction_is_not_a_claim():
    for intro in ("The test has two parts:", "So, in short, you need:"):
        assert classify_claim(intro) is ClaimType.NON_FACTUAL, intro


def test_a_known_host_written_with_accents_is_respelled():
    """Live: "administration‑étrangers‑en‑france…" is not a host that resolves."""
    v = validate("Validate your VLS-TS online after you arrive in France on "
                 "administration‑étrangers‑en‑france.interieur.gouv.fr.",
                 [VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    assert "administration-etrangers-en-france.interieur.gouv.fr" in v.text
    assert "étrangers‑en" not in v.text
    assert v.claims[0].action == Action.LINK_CORRECTED.value


def test_a_bare_domain_the_evidence_never_gave_is_removed():
    v = validate("Validate your VLS-TS online after you arrive in France on "
                 "visa-validation-help.com.",
                 [VALIDATION], question=VALIDATION_Q, similarity=same_subject)
    assert "visa-validation-help.com" not in v.text
    assert "after you arrive" in v.text


def test_a_date_is_not_read_as_a_score():
    assert extract_facts("avant le 30/09/2026").scores == frozenset()


# ----------------------------------------- only supported claims survive --

def partially(claims, sentences):
    return [[0.62] * len(sentences) for _ in claims]


def test_a_plausible_claim_the_evidence_only_half_supports_is_removed():
    """§28. Live: "you'll get a confirmation that your visa is now validated"."""
    v = validate("Once processed, you'll get a confirmation that your visa is validated.",
                 [VALIDATION], question=VALIDATION_Q, similarity=partially)
    claim = v.claims[0]
    assert claim.support == Support.PARTIALLY_SUPPORTED.value
    assert claim.action == Action.REMOVED.value


def test_a_caveat_that_names_a_body_is_still_checked():
    """Live: "the page only says housing aid is run by the CAF" went unchecked."""
    crous = EvidenceText(source_id="etudiant-gouv", url="https://etudiant.gouv.fr/logement",
                         title="Logement Crous", text="Déposez votre dossier sur messervices.")
    sentence = ("Le site ne précise pas la démarche, il indique seulement que les "
                "aides au logement sont gérées par la CAF.")
    assert classify_claim(sentence) is not ClaimType.NON_FACTUAL
    v = validate(sentence, [crous], question="aide au logement", similarity=same_subject)
    assert v.claims[0].action == Action.REMOVED.value
    assert "CAF" in v.claims[0].reason


@pytest.mark.parametrize("caveat", ["Voilà tout ce que les pages couvrent à ce sujet.",
                                     "That's all the pages cover on this."])
def test_a_plain_caveat_is_not_a_claim(caveat):
    assert classify_claim(caveat) is ClaimType.NON_FACTUAL


# -------------------------------------------- anchors in the uncertain band --

ANEF_HOME = EvidenceText(
    source_id="anef", url="https://administration-etrangers-en-france.interieur.gouv.fr/",
    title="Accueil | Administration numérique pour les étrangers en France (ANEF)",
    text="Je valide mon VLS-TS\nVisa de long séjour valant titre de séjour\n"
         "Je demande ou renouvelle un titre de séjour")


def test_a_paraphrase_that_quotes_the_page_survives_the_uncertain_band():
    """Live: a faithful claim quoting the service name scored 0.69 and was lost."""
    v = validate("The ANEF portal has a service titled “Je valide mon VLS‑TS” for "
                 "validating a long-stay visa.", [ANEF_HOME], question=VALIDATION_Q,
                 similarity=lambda c, s: [[0.62] * len(s) for _ in c])
    assert v.claims[0].action == Action.KEPT.value, v.claims[0].reason
    assert "quoted" in v.claims[0].reason


def test_a_quote_the_page_does_not_contain_anchors_nothing():
    v = validate("The portal has a service titled “Valider mon visa en un clic”.",
                 [ANEF_HOME], question=VALIDATION_Q,
                 similarity=lambda c, s: [[0.62] * len(s) for _ in c])
    assert v.claims[0].action == Action.REMOVED.value


def test_naming_the_caf_does_not_anchor_an_invented_caf_requirement():
    """A name shows the page is about the CAF, not that this rule is true."""
    page = EvidenceText(source_id="etudiant-gouv", url="https://etudiant.gouv.fr/logement",
                        title="Aides au logement",
                        text="Les aides au logement sont versées par la CAF.")
    v = validate("The CAF requires you to have a signed lease before applying.",
                 [page], question="aide au logement",
                 similarity=lambda c, s: [[0.62] * len(s) for _ in c])
    claim = v.claims[0]
    assert claim.claim_type == ClaimType.REQUIREMENT.value
    assert claim.action == Action.REMOVED.value


def test_naming_the_caf_can_anchor_a_pointer_to_it():
    page = EvidenceText(source_id="etudiant-gouv", url="https://etudiant.gouv.fr/logement",
                        title="Aides au logement",
                        text="Les aides au logement sont versées par la CAF.")
    v = validate("For the procedure itself, look at the CAF's own site.", [page],
                 question="aide au logement",
                 similarity=lambda c, s: [[0.62] * len(s) for _ in c])
    assert v.claims[0].action == Action.KEPT.value, v.claims[0].reason


@pytest.mark.parametrize("caveat", [
    "The page doesn’t give the step‑by‑step for validating a VLS‑TS.",
    "That's all the source says.",
    "Le site ne donne pas la liste des pièces.",
])
def test_more_caveat_phrasings_are_caveats(caveat):
    assert classify_claim(caveat) is ClaimType.NON_FACTUAL
