"""Chunking behaviour: the structural rules beat the size target.

These tests build their own documents rather than reading the corpus, so they
are fast, deterministic, and contain no claim about French administration.
"""

from __future__ import annotations

import pytest

from app.config import get_settings
from app.ingest.chunk import chunk_document
from app.ingest.parse import Block, Document, Section


def make_document(sections: list[Section], doc_id: str = "F0001") -> Document:
    return Document(
        doc_id=doc_id,
        doc_kind="fiche",
        publication_type="Fiche d'information conditionnée",
        title_fr="Titre de la fiche",
        audience="Particuliers",
        segment="part",
        theme="Thème",
        sub_theme="Sous-thème",
        last_updated="2026-01-01",
        last_major_update="2026-01-01",
        last_updated_is_plausible=True,
        source_url="https://example.invalid/F0001",
        sections=tuple(sections),
        source_file=f"{doc_id}.xml",
    )


def section(blocks: list[Block], title: str = "Section", situation: str | None = None):
    return Section(title, situation, (title,), tuple(blocks))


def replace_segment(document: Document, segment: str) -> Document:
    from dataclasses import replace

    return replace(document, segment=segment)


@pytest.fixture
def settings():
    return get_settings()


def test_long_document_list_is_never_split(settings):
    """A list of required documents is useless in halves, so it stays whole."""
    items = tuple(f"Document numéro {i} à fournir" for i in range(300))
    doc = make_document([section([Block("list", "", items)])])

    chunks = chunk_document(doc, settings)

    assert len(chunks) == 1, "the list was split across chunks"
    assert chunks[0].token_count > settings.chunk_target_tokens, (
        "this list should exceed the size target, proving the rule overrides it"
    )
    for item in items:
        assert item in chunks[0].text


def test_numbered_procedure_is_never_split(settings):
    items = tuple(f"Étape {i} de la démarche" for i in range(1, 201))
    doc = make_document([section([Block("ordered_list", "", items)])])

    chunks = chunk_document(doc, settings)

    assert len(chunks) == 1
    assert chunks[0].contains_numbered_procedure
    numbers = [int(line.split(".")[0]) for line in chunks[0].text.splitlines()
               if line[:1].isdigit()]
    assert numbers == list(range(1, 201)), "the sequence was broken"


def test_mutually_exclusive_situations_never_share_a_chunk(settings):
    doc = make_document([
        section([Block("paragraph", "Règle applicable la première année.")],
                title="Démarche", situation="Première situation"),
        section([Block("paragraph", "Règle applicable au renouvellement.")],
                title="Démarche", situation="Seconde situation"),
    ])

    chunks = chunk_document(doc, settings)

    situations = [c.situation_fr for c in chunks]
    assert len(set(situations)) == 2
    for chunk in chunks:
        others = {s for s in situations if s != chunk.situation_fr}
        for other in others:
            assert other not in chunk.text


def test_every_chunk_carries_its_document_title(settings):
    """A chunk read in isolation must still say what it belongs to."""
    doc = make_document([
        section([Block("paragraph", "Texte " * 400)], situation="Une situation"),
    ])

    for chunk in chunk_document(doc, settings):
        assert chunk.text.startswith(doc.title_fr)
        assert "Situation : Une situation" in chunk.text


def test_no_chunk_can_exceed_the_embedding_limit(settings):
    """Anything longer would be truncated when embedded, losing content silently."""
    nested = tuple(f"[Cas {i}] " + "Contenu détaillé. " * 300 for i in range(40))
    doc = make_document([section([Block("cases", "", nested)])])

    chunks = chunk_document(doc, settings)

    assert chunks
    for chunk in chunks:
        assert chunk.token_count <= settings.embed_max_tokens


def test_required_metadata_is_present_on_every_chunk(settings):
    required = {
        "chunk_id", "fiche_id", "fiche_title_fr", "section_title_fr",
        "source_url", "last_updated", "audience", "theme",
        "feed_version", "ingested_at",
    }
    doc = make_document([section([Block("paragraph", "Un paragraphe.")])])

    for chunk in chunk_document(doc, settings):
        metadata = chunk.metadata()
        assert required <= set(metadata)
        assert metadata["source_url"]
        assert metadata["fiche_title_fr"]
        # The vector store only accepts scalars.
        for value in metadata.values():
            assert isinstance(value, (str, int, float, bool))


def test_chunk_ids_are_unique(settings):
    doc = make_document([
        section([Block("paragraph", "Texte " * 500)], title=f"Section {i}")
        for i in range(5)
    ])
    ids = [c.chunk_id for c in chunk_document(doc, settings)]
    assert len(ids) == len(set(ids))


def test_identical_documents_in_both_feeds_are_deduplicated():
    """The same fiche published twice must not compete with itself in results."""
    from app.ingest.parse import deduplicate

    body = [section([Block("paragraph", "Un texte identique.")])]
    first = make_document(body)
    second = replace_segment(make_document(body), "pro")

    kept, dropped = deduplicate([first, second])

    assert len(kept) == 1
    assert len(dropped) == 1
    assert set(kept[0].segments) == {"part", "pro"}


def test_same_id_with_different_content_is_kept_not_merged():
    """A real divergence must never be silently discarded as a duplicate."""
    from app.ingest.parse import deduplicate

    first = make_document([section([Block("paragraph", "Première version.")])])
    second = replace_segment(
        make_document([section([Block("paragraph", "Version différente.")])]), "pro"
    )

    kept, dropped = deduplicate([first, second])

    assert len(kept) == 2
    assert not dropped
    assert len({d.doc_id for d in kept}) == 2


def test_colliding_chunk_ids_raise_rather_than_overwrite(settings):
    """A repeated id would overwrite a chunk in the store with no error."""
    from app.ingest.chunk import chunk_documents

    body = [section([Block("paragraph", "Texte.")])]
    duplicate_pair = [make_document(body), make_document(body)]

    with pytest.raises(ValueError, match="duplicate chunk ids"):
        chunk_documents(duplicate_pair, settings)
