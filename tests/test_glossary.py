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
