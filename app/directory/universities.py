"""The register of French higher-education institutions.

Idéo-Structures d'enseignement supérieur, published by ONISEP as open data
under the ODbL. Nearly nine thousand institutions: public universities and
schools, and private ones too — sous contrat and hors contrat alike. A
register of only the principal public establishments leaves out most of the
places people actually study, and a private-school student is no less lost in
the paperwork.

Note the licence differs from the rest of the corpus: the ODbL asks for
attribution and share-alike on a derived database, where the fiches are
Licence Ouverte. ONISEP is credited in the interface and the README.

Why this is worth having: a student rarely knows which préfecture handles
their permit or which académie their CROUS belongs to, but they always know
where they study. The institution gives the département and the académie, and
those are the two facts that decide who they are actually dealing with.

What this deliberately does not do is tell anyone which bank to use. No
official register records which bank a university has an arrangement with, it
changes yearly and by campus, and newcomers are the most heavily targeted
group there is for financial offers. A guess here sends someone to a counter
that will turn them away, or worse.
"""

from __future__ import annotations

import json
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import Settings, get_settings

SOURCE_URL = "https://api.opendata.onisep.fr/downloads/5fa586da5c4b6/5fa586da5c4b6.json"
SOURCE_NAME = "ONISEP — Idéo-Structures d'enseignement supérieur"
SOURCE_LICENCE = "ODbL"
CACHE_NAME = "institutions.json"


class DirectoryError(RuntimeError):
    """The register could not be downloaded or read."""


@dataclass(frozen=True)
class Institution:
    uai: str
    name: str
    acronym: str
    kind: str
    sector: str
    url: str
    commune: str
    postcode: str
    departement: str
    departement_id: str
    academie: str
    region: str
    parent: str = ""
    aliases: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.name} ({self.acronym})" if self.acronym else self.name

    @property
    def is_private(self) -> bool:
        return "priv" in self.sector.lower()

    @property
    def sector_label(self) -> str:
        return self.sector or "—"

    @property
    def where(self) -> str:
        bits = [b for b in (self.commune, self.departement) if b]
        return " · ".join(bits)


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", (text or "").lower())
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def _cache_path(settings: Settings) -> Path:
    return settings.cache_dir / CACHE_NAME


def download(settings: Settings | None = None) -> list[dict]:
    """Fetch the whole register in one file."""
    settings = settings or get_settings()
    request = urllib.request.Request(
        SOURCE_URL, headers={"User-Agent": "sesame/0.1 (local research tool)"})
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            payload = json.load(response)
    except (urllib.error.URLError, ValueError) as exc:
        raise DirectoryError(f"Could not read the register: {exc}") from None
    if isinstance(payload, dict):
        payload = payload.get("results") or payload.get("data") or []
    return payload


def refresh(settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    settings.ensure_dirs()
    records = download(settings)
    _cache_path(settings).write_text(
        json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    load.cache_clear()
    return len(records)


def _first(value) -> str:
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


@lru_cache(maxsize=1)
def load() -> tuple[Institution, ...]:
    """Every institution in the cached register, alphabetically."""
    settings = get_settings()
    path = _cache_path(settings)
    if not path.exists():
        return ()
    records = json.loads(path.read_text(encoding="utf-8"))

    institutions = []
    for row in records:
        name = (row.get("nom") or "").strip()
        if not name:
            continue
        # "38 - Isère" carries both the number and the name.
        dept_raw = (row.get("departement") or "").strip()
        dept_id, _, dept_name = dept_raw.partition(" - ")
        parent = (row.get("universite_de_rattachement_libelle_et_uai") or "").strip()
        parent = parent.split(" (")[0] if parent else ""

        aliases = {a for a in (row.get("sigle") or "").split(",") if a.strip()}
        if parent:
            aliases.add(parent)
        institutions.append(Institution(
            uai=row.get("code_uai") or "",
            name=name,
            acronym=(row.get("sigle") or "").strip(),
            kind=(row.get("type_detablissement") or "").strip(),
            sector=(row.get("statut") or "").strip(),
            url=(row.get("url_et_id_onisep") or "").strip(),
            commune=(row.get("commune") or "").strip(),
            postcode=(row.get("cp") or "").strip(),
            departement=dept_name.strip() or dept_raw,
            departement_id=dept_id.strip(),
            academie=(row.get("academie") or "").strip(),
            region=(row.get("region") or "").strip(),
            parent=parent,
            aliases=tuple(sorted(a.strip() for a in aliases if a.strip())),
        ))
    # Institutions share names across towns, so the town disambiguates.
    return tuple(sorted(institutions, key=lambda i: (_fold(i.name), _fold(i.commune))))


def search(query: str, limit: int = 8) -> list[Institution]:
    """Match on the name, the acronym, or any name the register records.

    Institutions merge and rename constantly, and a student writes whatever
    is on their card — so the register's own alternative names are searched
    too, which is the difference between finding Sorbonne and finding nothing.
    """
    needle = _fold(query).strip()
    if len(needle) < 2:
        return []

    starts, contains = [], []
    for institution in load():
        haystacks = [institution.name, institution.acronym,
                     institution.commune, *institution.aliases]
        folded = [_fold(h) for h in haystacks if h]
        if any(h.startswith(needle) for h in folded):
            starts.append(institution)
        elif any(needle in h for h in folded):
            contains.append(institution)
        if len(starts) >= limit:
            break
    return (starts + contains)[:limit]


def by_uai(uai: str) -> Institution | None:
    return next((i for i in load() if i.uai == uai), None)


def universities() -> tuple[Institution, ...]:
    return tuple(i for i in load()
                 if "universit" in _fold(i.kind) or "universit" in _fold(i.name))


if __name__ == "__main__":  # pragma: no cover - operational entry point
    count = refresh()
    print(f"cached {count} institutions")
