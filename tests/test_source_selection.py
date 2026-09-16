"""What retrieval finds is not what a reader is shown.

A page can be official, current, correct, and about somebody else. The
selection stage exists to keep that page out of an answer, and these tests
are the specific pages that used to get through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.answer.relevance import (
    Rejection, check, is_source_materially_relevant, select,
)
from app.config import PROJECT_ROOT
from app.retrieval.types import Retrieved
from app.sources.jurisdiction import resolve_reply
from app.sources.route import plan


def page(title: str, *, theme: str = "", situation: str = "",
         fiche: str = "F0001", url: str = "https://service-public.gouv.fr/x"):
    return Retrieved(chunk_id=fiche + "-1", text=title,
                     metadata={"fiche_title_fr": title, "theme": theme,
                               "situation_fr": situation, "fiche_id": fiche,
                               "source_url": url},
                     dense_score=0.8)


# ------------------------------------------------- the free-tram scenario --

def test_a_transport_question_is_local_and_says_so():
    """H. Fares are a network's, not the state's."""
    routing = plan("how to get free tram in france",
                   place=resolve_reply("france"))
    assert routing.topic.id == "public_transport"
    assert routing.needs_place, "a national answer would be averaging networks"


def test_a_transport_question_does_not_fall_back_to_the_corpus():
    """The corpus has pages sharing this vocabulary and answering none of it."""
    routing = plan("how to get free tram in Antibes",
                   place=resolve_reply("Antibes"))
    assert routing.topic.id == "public_transport"
    assert not routing.fall_back_to_corpus


def test_naming_the_city_stops_the_transport_question_asking_again():
    """I."""
    routing = plan("how to get free tram in france Antibes",
                   place=resolve_reply("Antibes"))
    assert not routing.needs_place


@pytest.mark.parametrize("title,theme", [
    ("Transport gratuit pour les seniors à Paris", "Transports"),
    ("Accès senior Paris : titre de transport", "Transports"),
])
def test_a_paris_page_is_not_an_answer_about_antibes(title, theme):
    """J. Official, current, correct — and about somebody else."""
    verdict = check(page(title, theme=theme), place=resolve_reply("Antibes"))
    assert not verdict.ok
    assert verdict.reason == Rejection.WRONG_JURISDICTION.value


def test_a_page_about_another_purpose_is_rejected():
    """A page that plainly belongs to a different purpose is not a source."""
    housing = page("Demande de logement social : constituer le dossier")
    verdict = check(housing, purposes=("public_transport",))
    assert not verdict.ok
    assert verdict.reason == Rejection.WRONG_PURPOSE.value


def test_an_rsa_page_can_never_be_a_candidate_for_a_tram_question():
    """The stronger guarantee, and the one that actually protects this.

    The purpose check only fires on a page whose own subject is detectable,
    and "Revenu de solidarité active" matches no transport vocabulary to
    contradict. What keeps RSA out of a tram answer is structural: the
    public_transport topic has no corpus fallback, so the public
    administration corpus is never consulted for it and RSA is never a
    candidate in the first place. Claiming the relevance gate catches this
    would be claiming more than it does.
    """
    routing = plan("how to get free tram in Antibes",
                   place=resolve_reply("Antibes"))
    assert not routing.fall_back_to_corpus
    assert all(step.source.id != "service-public" for step in routing.steps)


def test_a_page_about_the_right_place_survives():
    here = page("Titre de séjour : démarches à Antibes")
    assert is_source_materially_relevant(here, place=resolve_reply("Antibes"))


def test_a_page_naming_no_place_is_not_rejected_for_it():
    """A national rule page is not "somewhere else"; it is everywhere."""
    national = page("Renouveler un titre de séjour")
    assert is_source_materially_relevant(national, place=resolve_reply("Antibes"))


# ------------------------------------------------- candidates vs selected --

def test_candidates_and_selected_are_kept_apart():
    """§19: what was found and what is shown are different lists."""
    candidates = [
        page("Renouveler un titre de séjour", fiche="F1"),
        page("Transport gratuit pour les seniors à Paris", theme="Transports",
             fiche="F2"),
    ]
    selection = select(candidates, place=resolve_reply("Antibes"))
    assert len(selection.candidates) == 2
    assert len(selection.selected) == 1
    assert selection.selected[0].metadata["fiche_id"] == "F1"


def test_every_rejection_carries_a_reason():
    """§48: "these pages are unrelated" has to be answerable."""
    selection = select([page("Transport gratuit seniors à Paris",
                             theme="Transports")],
                       place=resolve_reply("Antibes"))
    assert selection.rejected
    _, verdict = selection.rejected[0]
    assert verdict.reason in {r.value for r in Rejection}
    assert verdict.detail

    diagnostics = selection.as_diagnostics()
    assert diagnostics["candidate_count"] == 1
    assert diagnostics["selected_count"] == 0
    assert diagnostics["reason_counts"]


def test_the_same_page_is_not_shown_twice():
    twice = [page("Titre de séjour", fiche="F9"),
             page("Titre de séjour", fiche="F9")]
    selection = select(twice)
    assert len(selection.selected) == 1
    assert selection.rejected[0][1].reason == Rejection.DUPLICATE.value


# ------------------------------------------- the fallback that was removed --

def test_the_closest_pages_block_cannot_appear():
    """K. Not hidden behind a flag — gone, with no path back to it."""
    banned = ("near_misses_html", "Closest pages we found",
              "Les pages les plus proches",
              "Search service-public.gouv.fr for this")
    offenders = []
    for path in list((PROJECT_ROOT / "app").rglob("*.py")) + \
            [PROJECT_ROOT / "app" / "ui" / "styles.css"]:
        text = path.read_text(encoding="utf-8")
        for phrase in banned:
            if phrase in text:
                offenders.append(f"{path.name}: {phrase}")
    assert not offenders, offenders


def test_a_refusal_states_the_gap_instead_of_listing_pages():
    from app.ui.i18n import STRINGS
    from app.ui.render import source_gap_html

    for lang in ("en", "fr"):
        assert "source_gap" in STRINGS[lang]
        rendered = source_gap_html(None, lang)
        assert "rp-source-gap" in rendered
        assert "http" not in rendered, "a refusal must not link a search"


def test_no_automatic_search_link_to_service_public():
    """§25: an external search is not a result, and never offered unasked."""
    render = (PROJECT_ROOT / "app" / "ui" / "render.py").read_text(encoding="utf-8")
    assert "service-public.gouv.fr/particuliers/recherche" not in render
