"""Glossary loading, provenance, and query expansion."""

from __future__ import annotations

from app.query import glossary


def test_every_entry_is_complete():
    terms = glossary.load()
    assert terms
    for term in terms:
        assert term.fr and term.en
        assert term.explanation_en and term.explanation_fr
        assert term.source in {"authored", "service-public.gouv.fr"}


def test_official_entries_name_the_definition_they_came_from():
    """An official explanation and one we wrote must never look the same."""
    for term in glossary.load():
        if term.is_official:
            assert term.definition_id, f"{term.fr} claims an official source with no id"
            assert "service-public.gouv.fr" in term.provenance
        else:
            assert term.definition_id is None
            assert "Claré" in term.provenance


def test_english_question_is_expanded_with_french_vocabulary():
    """The corpus is French; the English word is not what a document uses."""
    expanded, terms = glossary.expand("What proof of address do I need?")
    assert "justificatif de domicile" in expanded
    assert any(t.fr == "justificatif de domicile" for t in terms)


def test_expansion_keeps_the_original_question():
    original = "How much deposit can a landlord ask for?"
    expanded, _ = glossary.expand(original)
    assert expanded.startswith(original)


def test_expansion_does_not_duplicate_a_term_already_present():
    expanded, _ = glossary.expand("Qu'est-ce qu'un justificatif de domicile ?")
    assert expanded.lower().count("justificatif de domicile") == 1


def test_unrelated_text_is_left_alone():
    text = "Quel temps fait-il aujourd'hui ?"
    expanded, terms = glossary.expand(text)
    assert expanded == text
    assert terms == []


def test_terms_are_found_in_either_language():
    assert any(t.fr == "dépôt de garantie" for t in glossary.find_terms("security deposit"))
    assert any(t.fr == "dépôt de garantie" for t in glossary.find_terms("dépôt de garantie"))


def test_a_word_is_explained_in_the_language_being_read():
    """A French reader must not be handed the English gloss first.

    The card carries both explanations; which one leads is the whole point.
    Leading in English on a French page, with the French hidden behind a
    click, is backwards in a product whose promise is being understood.
    """
    import re

    from app.ui.render import glossary_html

    def leading(html_text: str) -> str:
        return re.search(r"rp-gloss-def'>(.*?)</div>", html_text).group(1)

    french = leading(glossary_html("titre de séjour", "fr"))
    english = leading(glossary_html("titre de séjour", "en"))
    assert french != english, "both languages lead with the same explanation"
    assert "The document proving" in english
    # Rendered French, not the English string that happens to sit beside it.
    assert "document" in french.lower()


def test_glossary_provenance_labels_are_translated():
    from app.ui.render import glossary_html

    assert "Définition officielle" in glossary_html("titre de séjour", "fr") \
        or "Rédigé pour" in glossary_html("titre de séjour", "fr")
    assert "Official definition" in glossary_html("titre de séjour", "en") \
        or "Written for" in glossary_html("titre de séjour", "en")
