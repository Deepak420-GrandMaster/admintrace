"""Parse DILA XML into structured documents.

Two things here matter more than anything else.

First, **applicability is preserved**. Many fiches are "conditioned": their body
is split into mutually exclusive branches (``ListeSituations`` / ``Situation``,
and ``BlocCas`` / ``Cas``) such as "first year" versus "after one year of
residence". Flattening those would let the rules for one situation be read as
the rules for another, which is the single most damaging mistake this system
could make. Every section therefore carries the label of the branch it came
from, and case branches are kept intact as one indivisible block.

Second, **nothing is dropped silently**. Elements this parser does not
understand are counted and reported, and a document that fails outright is
returned as a :class:`ParseFailure` rather than skipped.

This module states no fact about French administration. It only moves text
from the source documents into a structure.
"""

from __future__ import annotations

import collections
import re
from datetime import date
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path

DC = "{http://purl.org/dc/elements/1.1/}"

# Elements that carry meaning inline and should be flattened into their
# parent's text rather than becoming blocks of their own.
INLINE_TAGS = frozenset({
    "MiseEnEvidence", "Expression", "Valeur", "Exposant", "Indice",
    "LienInterne", "LienIntra", "LienExterne", "LienPublication",
    "Citation", "Abreviation", "Reference", "Texte", "TitreRiche",
})

# Headings and machinery that must not become content blocks.
SKIP_TAGS = frozenset({
    "Titre", "TitreAlternatif", "TitreFlottant", "TitreRiche", "Condition",
    "Definition", "FilDClaré", "Theme", "SousThemePere", "DossierPere",
    "SurTitre", "Audience", "Canal", "PivotLocal", "Video", "ContenuIllustre",
})

# Callout boxes: the label is part of the meaning, so it is kept.
NOTE_TAGS = frozenset({"ANoter", "ASavoir", "Attention", "Rappel", "Exemple", "Complement"})

LIST_KIND = {"puce": "list", "numero": "ordered_list", "caseACocher": "checklist"}

# Wrappers that hold content rather than being content. They appear in several
# positions in the schema, so they are expanded wherever they are met.
CONTAINER_TAGS = frozenset({"Introduction", "Texte", "Chapitre", "SousChapitre"})


def _tag(element: ET.Element) -> str:
    return element.tag.split("}")[-1]


def _text(element: ET.Element | None) -> str:
    """Flatten an element to a single clean line of text."""
    if element is None:
        return ""
    raw = "".join(element.itertext())
    return re.sub(r"\s+", " ", raw.replace(" ", " ")).strip()


@dataclass(frozen=True)
class Block:
    """One unit of content. Lists and case branches are never split."""

    kind: str
    text: str
    items: tuple[str, ...] = ()
    label: str | None = None
    url: str | None = None

    def render(self) -> str:
        lines: list[str] = []

        if self.kind == "note" and self.label:
            lines.append(f"{self.label} : {self.text}".strip())
        elif self.text:
            lines.append(self.text)

        if self.kind in {"list", "checklist"}:
            lines.extend(f"- {item}" for item in self.items)
        elif self.kind == "ordered_list":
            lines.extend(f"{i}. {item}" for i, item in enumerate(self.items, 1))
        elif self.kind in {"cases", "table"}:
            lines.extend(self.items)

        if self.url:
            lines.append(self.url)

        return "\n".join(line for line in lines if line).strip()


@dataclass(frozen=True)
class Section:
    """One section of a document, inside one applicability branch."""

    title_fr: str | None
    situation_fr: str | None
    path: tuple[str, ...]
    blocks: tuple[Block, ...]

    def render(self) -> str:
        parts = [b.render() for b in self.blocks]
        return "\n\n".join(p for p in parts if p)


@dataclass(frozen=True)
class Document:
    """A parsed source document with its body structure intact."""

    doc_id: str
    doc_kind: str
    publication_type: str
    title_fr: str
    audience: str
    segment: str
    theme: str | None
    sub_theme: str | None
    last_updated: str | None
    last_major_update: str | None
    last_updated_is_plausible: bool
    source_url: str
    sections: tuple[Section, ...]
    definitions: tuple[tuple[str, str, str], ...] = ()
    source_file: str = ""
    # Feeds this document was published in. A few hundred documents appear in
    # both; recorded here so that is not lost when the duplicate is dropped.
    segments: tuple[str, ...] = ()

    @property
    def body_text(self) -> str:
        return "\n".join(section.render() for section in self.sections)

    @property
    def situations(self) -> tuple[str, ...]:
        seen = {s.situation_fr for s in self.sections if s.situation_fr}
        return tuple(sorted(seen))


@dataclass
class ParseFailure:
    path: str
    reason: str


@dataclass
class ParseReport:
    """What happened across a whole parse run."""

    documents: list[Document] = field(default_factory=list)
    failures: list[ParseFailure] = field(default_factory=list)
    unhandled_tags: collections.Counter = field(default_factory=collections.Counter)
    skipped_index_files: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Block extraction
# --------------------------------------------------------------------------

def _blocks_of(element: ET.Element, report: ParseReport) -> list[Block]:
    """All blocks directly under an element, expanding any wrapper elements."""
    blocks: list[Block] = []
    for child in element:
        if _tag(child) in CONTAINER_TAGS:
            blocks.extend(_blocks_of(child, report))
        else:
            block = _block(child, report)
            if block:
                blocks.append(block)
    return blocks


def _block(element: ET.Element, report: ParseReport) -> Block | None:
    name = _tag(element)

    if name in SKIP_TAGS or name in INLINE_TAGS:
        return None

    if name == "Paragraphe":
        text = _text(element)
        return Block("paragraph", text) if text else None

    if name == "Liste":
        items = tuple(_text(i) for i in element.findall("Item") if _text(i))
        if not items:
            return None
        kind = LIST_KIND.get(element.get("type", "puce"), "list")
        return Block(kind, "", items)

    if name == "BlocCas":
        # Mutually exclusive alternatives. Kept as one block so that no later
        # step can split them and present one branch as if it were general.
        items = []
        for case in element.findall("Cas"):
            title = _text(case.find("Titre"))
            body_parts = [
                b.render()
                for child in case
                if _tag(child) != "Titre"
                for b in (
                    _blocks_of(child, report)
                    if _tag(child) in CONTAINER_TAGS
                    else [_block(child, report)]
                )
                if b
            ]
            body = " ".join(p.replace("\n", " ") for p in body_parts).strip()
            items.append(f"[{title}] {body}".strip() if title else body)
        items = tuple(i for i in items if i)
        return Block("cases", "", items) if items else None

    if name in NOTE_TAGS:
        label = _text(element.find("Titre")) or name
        body = " ".join(
            _text(child) for child in element if _tag(child) != "Titre"
        ).strip()
        return Block("note", body, label=label) if body else None

    if name == "ServiceEnLigne":
        title = _text(element.find("Titre")) or _text(element)
        return Block(
            "service", title, label=element.get("type"), url=element.get("URL")
        ) if title else None

    if name == "OuSAdresser":
        text = _text(element)
        return Block("where", text, label="Où s'adresser") if text else None

    if name == "Tableau":
        rows = []
        for row in element.iter("Rangée"):
            cells = [_text(c) for c in row.findall("Cellule")]
            if any(cells):
                rows.append(" | ".join(cells))
        caption = _text(element.find("Titre"))
        return Block("table", caption, tuple(rows)) if rows else None

    if name in {"PourEnSavoirPlus", "RessourceWeb", "FragmentConditionne"}:
        text = _text(element)
        return Block("paragraph", text) if text else None

    report.unhandled_tags[name] += 1
    text = _text(element)
    return Block("paragraph", text) if text else None


def _walk(container: ET.Element, situation: str | None,
          path: tuple[str, ...], report: ParseReport) -> list[Section]:
    """Turn a Chapitre-bearing container into flat, branch-labelled sections."""
    title = _text(container.find("Titre")) or None
    here = path + ((title,) if title else ())

    blocks: list[Block] = []
    nested: list[Section] = []

    for child in container:
        name = _tag(child)
        if name in {"Chapitre", "SousChapitre", "Introduction"}:
            nested.extend(_walk(child, situation, here, report))
        elif name == "Texte":
            nested.extend(_walk(child, situation, here, report))
        else:
            block = _block(child, report)
            if block:
                blocks.append(block)

    sections: list[Section] = []
    if blocks:
        sections.append(Section(title, situation, here, tuple(blocks)))
    sections.extend(nested)
    return sections


def _body(root: ET.Element, report: ParseReport) -> list[Section]:
    sections: list[Section] = []

    intro = root.find("Introduction")
    if intro is not None:
        sections.extend(_walk(intro, None, ("Introduction",), report))

    branches = root.find("ListeSituations")
    if branches is not None:
        for situation in branches.findall("Situation"):
            label = _text(situation.find("Titre")) or None
            intro_block = situation.find("Introduction")
            if intro_block is not None:
                sections.extend(_walk(intro_block, label, (), report))
            body = situation.find("Texte")
            target = body if body is not None else situation
            sections.extend(_walk(target, label, (), report))

    for container_name in ("Texte", "QuestionReponse"):
        container = root.find(container_name)
        if container is not None:
            sections.extend(_walk(container, None, (), report))

    for chapter in root.findall("Chapitre"):
        sections.extend(_walk(chapter, None, (), report))

    return sections


def _definitions(root: ET.Element) -> tuple[tuple[str, str, str], ...]:
    """Official glossary definitions inlined in this document."""
    found = []
    for element in root.iter("Definition"):
        term = _text(element.find("Titre"))
        body = " ".join(
            _text(child) for child in element if _tag(child) != "Titre"
        ).strip()
        ref = element.get("ID", "")
        if term and body:
            found.append((ref, term, body))
    return tuple(found)


# --------------------------------------------------------------------------
# Document parsing
# --------------------------------------------------------------------------

# Bounds for a believable publication date. This is a sanity check on the
# data, not a statement about French administration: the lower bound predates
# service-public.gouv.fr itself, and the upper bound simply rules out the
# future.
_EARLIEST_PLAUSIBLE_YEAR = 1990


def _date(value: str | None) -> str | None:
    """DILA writes dc:date as 'modified 2026-08-01'."""
    if not value:
        return None
    match = re.search(r"(\d{4}-\d{2}-\d{2})", value)
    return match.group(1) if match else None


def _date_is_plausible(value: str | None) -> bool:
    """False for dates the source clearly mistyped, e.g. the year 0205.

    The value is still reported exactly as published; this flag only lets the
    citation layer avoid presenting an obviously broken date as fact.
    """
    if not value:
        return False
    year = int(value[:4])
    return _EARLIEST_PLAUSIBLE_YEAR <= year <= date.today().year + 1


def parse_file(path: Path, segment: str, report: ParseReport) -> Document | None:
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        report.failures.append(ParseFailure(str(path), f"malformed XML: {exc}"))
        return None

    root_tag = _tag(root)
    if root_tag not in {"Publication", "ServiceComplementaire"}:
        # Index files (menu, arborescence, redirections...) are navigation
        # scaffolding, not content. Recorded, not treated as failures.
        report.skipped_index_files.append(path.name)
        return None

    doc_id = root.get("ID") or _text(root.find(f"{DC}identifier"))
    title = _text(root.find(f"{DC}title"))
    if not doc_id or not title:
        report.failures.append(ParseFailure(str(path), "missing ID or title"))
        return None

    source_url = root.get("spUrl") or ""
    publication_type = root.get("type") or _text(root.find(f"{DC}type")) or ""
    audience = _text(root.find("Audience")) or _text(root.find(f"{DC}coverage"))

    theme = _text(root.find("Theme/Titre")) or _text(root.find(f"{DC}subject")) or None
    sub_theme = _text(root.find("SousThemePere")) or None

    kind = {"F": "fiche", "R": "ressource", "N": "dossier"}.get(doc_id[:1], "autre")

    try:
        sections = tuple(_body(root, report))
    except Exception as exc:  # defensive: one odd document must not stop the run
        report.failures.append(ParseFailure(str(path), f"body walk failed: {exc!r}"))
        return None

    return Document(
        doc_id=doc_id,
        doc_kind=kind,
        publication_type=publication_type,
        title_fr=title,
        audience=audience or ("Particuliers" if segment == "part" else "Entreprendre"),
        segment=segment,
        theme=theme,
        sub_theme=sub_theme,
        last_updated=_date(_text(root.find(f"{DC}date"))),
        last_major_update=_date(root.get("dateDerniereModificationImportante")),
        last_updated_is_plausible=_date_is_plausible(
            _date(_text(root.find(f"{DC}date")))
        ),
        source_url=source_url,
        sections=sections,
        definitions=_definitions(root),
        source_file=path.name,
    )


def parse_directory(directory: Path, segment: str,
                    report: ParseReport | None = None) -> ParseReport:
    report = report or ParseReport()
    for path in sorted(directory.glob("*.xml")):
        document = parse_file(path, segment, report)
        if document is not None:
            report.documents.append(document)
    return report


def deduplicate(documents: list[Document]) -> tuple[list[Document], list[str]]:
    """Drop documents republished verbatim under a second audience.

    A few hundred fiches appear in both feeds with byte-identical bodies,
    because they concern individuals and businesses alike. Keeping both copies
    would mean the same passage competing with itself for a place in the
    retrieved set, and a user reading the same answer twice. The first copy is
    kept and the second audience is recorded on it.

    A document sharing an identifier but *differing* in body is not a
    duplicate and is kept, so that a real divergence is never silently lost.
    """
    kept: dict[str, Document] = {}
    order: list[str] = []
    dropped: list[str] = []

    for document in documents:
        existing = kept.get(document.doc_id)
        if existing is None:
            kept[document.doc_id] = document
            order.append(document.doc_id)
            continue
        if existing.body_text == document.body_text:
            merged = sorted(set(existing.segments or (existing.segment,))
                            | {document.segment})
            kept[document.doc_id] = replace(existing, segments=tuple(merged))
            dropped.append(document.doc_id)
        else:
            # Same identifier, different content: keep both under distinct ids.
            alternate = f"{document.doc_id}@{document.segment}"
            kept[alternate] = replace(document, doc_id=alternate)
            order.append(alternate)

    return [kept[key] for key in order], dropped
