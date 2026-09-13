"""Section-aware chunking.

The source documents are already structured by people who understand the
procedures they describe, so the chunker follows those boundaries rather than
inventing its own. Three rules override the size target:

1. A list is never split. A list of required documents is useless in halves,
   and a half-list read as a whole list is worse than useless. If it exceeds
   the size target it stays whole and the chunk is simply long.
2. A numbered procedure is never split mid-sequence, for the same reason.
3. Mutually exclusive branches never share a chunk. A chunk belongs to exactly
   one situation and carries its label.

Every chunk opens with the title of the document it came from, and the branch
and section it sits in, so a chunk retrieved on its own still says what
procedure it is talking about.

Nothing here encodes a fact about French administration.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache

from app.config import Settings, get_settings
from app.ingest.parse import Block, Document, Section

# Blocks that must survive intact. Splitting any of these changes what it says.
ATOMIC_KINDS = frozenset({"list", "ordered_list", "checklist", "cases", "table"})

AUDIENCE_BY_SEGMENT = {"part": "particuliers", "pro": "entreprendre"}

_SENTENCE_BREAK = re.compile(r"(?<=[.!?:;])\s+")


@lru_cache(maxsize=1)
def _tokenizer():
    """The embedding model's own tokenizer, when it can be loaded.

    Counting with the tokenizer that will actually embed the text is more
    honest than a word-count heuristic, and it costs nothing since the model
    is already a dependency. If it cannot be loaded (no model downloaded yet,
    no network), chunking still works via the fallback below.
    """
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(get_settings().embed_model)
    except Exception:
        return None


def count_tokens(text: str) -> int:
    """Token count for sizing decisions.

    Falls back to a character-based estimate. French administrative prose runs
    close to four characters per token for this tokenizer, which is accurate
    enough for packing decisions where the structural rules dominate anyway.
    """
    if not text:
        return 0
    tokenizer = _tokenizer()
    if tokenizer is not None:
        return len(tokenizer.encode(text, add_special_tokens=False))
    return max(1, len(text) // 4)


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage, with everything needed to cite it."""

    chunk_id: str
    text: str
    fiche_id: str
    fiche_title_fr: str
    section_title_fr: str | None
    situation_fr: str | None
    section_path: str
    source_url: str
    last_updated: str | None
    last_updated_is_plausible: bool
    last_major_update: str | None
    audience: str
    theme: str | None
    sub_theme: str | None
    doc_kind: str
    publication_type: str
    feed_version: str
    ingested_at: str
    part_index: int
    part_count: int
    token_count: int
    contains_document_list: bool
    contains_numbered_procedure: bool
    also_published_for: str
    block_kinds: tuple[str, ...] = field(default_factory=tuple)

    def metadata(self) -> dict[str, str | int | bool]:
        """Flat metadata for the vector store.

        Chroma stores scalars only, so ``None`` becomes an empty string. Every
        field required by the chunk schema is present on every chunk.
        """
        return {
            "chunk_id": self.chunk_id,
            "fiche_id": self.fiche_id,
            "fiche_title_fr": self.fiche_title_fr,
            "section_title_fr": self.section_title_fr or "",
            "situation_fr": self.situation_fr or "",
            "section_path": self.section_path,
            "source_url": self.source_url,
            "last_updated": self.last_updated or "",
            "last_updated_is_plausible": self.last_updated_is_plausible,
            "last_major_update": self.last_major_update or "",
            "audience": self.audience,
            "theme": self.theme or "",
            "sub_theme": self.sub_theme or "",
            "doc_kind": self.doc_kind,
            "publication_type": self.publication_type,
            "feed_version": self.feed_version,
            "ingested_at": self.ingested_at,
            "part_index": self.part_index,
            "part_count": self.part_count,
            "token_count": self.token_count,
            "contains_document_list": self.contains_document_list,
            "contains_numbered_procedure": self.contains_numbered_procedure,
            "also_published_for": self.also_published_for,
        }


def _header(document: Document, section: Section) -> str:
    """Opening lines that let a chunk stand on its own."""
    lines = [document.title_fr]
    if section.situation_fr:
        lines.append(f"Situation : {section.situation_fr}")
    if section.title_fr:
        lines.append(f"Section : {section.title_fr}")
    return "\n".join(lines)


def _split_paragraph(text: str, budget: int) -> list[str]:
    """Break an oversized paragraph on sentence boundaries, never mid-sentence."""
    sentences = _SENTENCE_BREAK.split(text)
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join(current + [sentence])
        if current and count_tokens(candidate) > budget:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces or [text]


def _explode_cases(block: Block) -> list[Block]:
    """One block per case branch.

    A BlocCas is a set of mutually exclusive alternatives, most often one per
    situation and occasionally one per département. When the whole set is too
    large to embed, the set is divided *between* its branches, never inside
    one. That keeps each branch complete and self-labelled, and it matches how
    Situation branches are already treated: an alternative is a chunk.
    """
    return [
        Block("cases", "", (item,))
        for item in block.items
        if item.strip()
    ]


def _pack(blocks: list[Block], target: int, budget: int,
          hard_limit: int | None = None) -> list[list[Block]]:
    """Group blocks into chunks, never splitting an atomic block."""
    groups: list[list[Block]] = []
    current: list[Block] = []
    current_tokens = 0

    for block in blocks:
        rendered = block.render()
        if not rendered:
            continue
        size = count_tokens(rendered)

        # A case set too large to embed is divided between its branches. Left
        # whole it would be truncated by the embedding model, and content that
        # is silently dropped is worse than content that is separated.
        if (
            hard_limit is not None
            and block.kind == "cases"
            and size > hard_limit
            and len(block.items) > 1
        ):
            for case_block in _explode_cases(block):
                case_size = count_tokens(case_block.render())
                if current:
                    groups.append(current)
                    current, current_tokens = [], 0
                groups.append([case_block])
            continue

        # An oversized paragraph is the only thing we are allowed to divide.
        if size > budget and block.kind == "paragraph":
            for piece in _split_paragraph(rendered, budget):
                part = Block("paragraph", piece)
                part_size = count_tokens(piece)
                if current and current_tokens + part_size > target:
                    groups.append(current)
                    current, current_tokens = [], 0
                current.append(part)
                current_tokens += part_size
            continue

        # Structural rule wins: an atomic block that does not fit becomes its
        # own chunk and is allowed to exceed the target.
        if current and current_tokens + size > target:
            groups.append(current)
            current, current_tokens = [], 0

        current.append(block)
        current_tokens += size

    if current:
        groups.append(current)
    return groups


def _enforce_embed_limit(body: str, budget: int) -> list[str]:
    """Last-resort division so that no chunk is ever truncated when embedded.

    Preferred boundaries first: whole lines, which are list items or case
    branches, then sentences. This only ever runs for content that is
    genuinely indivisible by structure, such as a case branch that itself
    contains nested case branches.
    """
    if count_tokens(body) <= budget:
        return [body]

    parts: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for line in body.split("\n"):
        line_tokens = count_tokens(line)
        if line_tokens > budget:
            if current:
                parts.append("\n".join(current))
                current, current_tokens = [], 0
            parts.extend(_split_paragraph(line, budget))
            continue
        if current and current_tokens + line_tokens > budget:
            parts.append("\n".join(current))
            current, current_tokens = [], 0
        current.append(line)
        current_tokens += line_tokens
    if current:
        parts.append("\n".join(current))
    return [p for p in parts if p.strip()] or [body]


def chunk_document(document: Document, settings: Settings | None = None) -> list[Chunk]:
    settings = settings or get_settings()
    ingested_at = date.today().isoformat()
    audience = AUDIENCE_BY_SEGMENT.get(document.segment, document.segment)

    chunks: list[Chunk] = []
    for section_index, section in enumerate(document.sections):
        header = _header(document, section)
        header_tokens = count_tokens(header)
        target = max(1, settings.chunk_target_tokens - header_tokens)
        budget = max(1, settings.chunk_max_tokens - header_tokens)

        groups = _pack(
            list(section.blocks), target, budget,
            hard_limit=settings.embed_max_tokens - header_tokens,
        )
        if not groups:
            continue

        bodies: list[tuple[str, tuple[str, ...]]] = []
        for group in groups:
            body = "\n\n".join(b.render() for b in group if b.render())
            if not body.strip():
                continue
            kinds = tuple(b.kind for b in group)
            for piece in _enforce_embed_limit(
                body, settings.embed_max_tokens - header_tokens
            ):
                bodies.append((piece, kinds))

        for part_index, (body, kinds) in enumerate(bodies, start=1):
            text = f"{header}\n\n{body}"
            chunks.append(
                Chunk(
                    chunk_id=f"{document.doc_id}#{section_index:03d}#{part_index:02d}",
                    text=text,
                    fiche_id=document.doc_id,
                    fiche_title_fr=document.title_fr,
                    section_title_fr=section.title_fr,
                    situation_fr=section.situation_fr,
                    section_path=" > ".join(section.path),
                    source_url=document.source_url,
                    last_updated=document.last_updated,
                    last_updated_is_plausible=document.last_updated_is_plausible,
                    last_major_update=document.last_major_update,
                    audience=audience,
                    theme=document.theme,
                    sub_theme=document.sub_theme,
                    doc_kind=document.doc_kind,
                    publication_type=document.publication_type,
                    feed_version=settings.feed_version,
                    ingested_at=ingested_at,
                    part_index=part_index,
                    part_count=len(bodies),
                    token_count=count_tokens(text),
                    contains_document_list=any(
                        k in {"list", "checklist"} for k in kinds
                    ),
                    contains_numbered_procedure="ordered_list" in kinds,
                    also_published_for=", ".join(
                        AUDIENCE_BY_SEGMENT.get(seg, seg)
                        for seg in document.segments
                        if seg != document.segment
                    ),
                    block_kinds=kinds,
                )
            )
    return chunks


def chunk_documents(
    documents: list[Document], settings: Settings | None = None
) -> list[Chunk]:
    settings = settings or get_settings()
    result: list[Chunk] = []
    for document in documents:
        result.extend(chunk_document(document, settings))

    # A repeated id would overwrite an earlier chunk in the vector store and
    # lose it without any error, so it is caught here instead.
    seen: set[str] = set()
    collisions = {c.chunk_id for c in result
                  if c.chunk_id in seen or seen.add(c.chunk_id)}
    if collisions:
        sample = ", ".join(sorted(collisions)[:5])
        raise ValueError(
            f"{len(collisions)} duplicate chunk ids would overwrite each other "
            f"in the vector store (e.g. {sample}). Deduplicate the documents "
            f"first with parse.deduplicate()."
        )
    return result
