"""The register of French higher-education institutions.

Published by the Ministère de l'Enseignement supérieur as open data under the
Licence Ouverte, the same terms as the rest of the corpus.

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

DATASET = "fr-esr-principaux-etablissements-enseignement-superieur"
BASE = ("https://data.enseignementsup-recherche.gouv.fr/api/explore/v2.1"
        f"/catalog/datasets/{DATASET}/records")
PAGE = 100
CACHE_NAME = "universities.json"

FIELDS = (
    "uai,uo_lib,uo_lib_officiel,uo_lib_en,sigle,type_d_etablissement,"
    "secteur_d_etablissement,url,com_nom,code_postal_uai,dep_id,dep_nom,"
    "aca_id,aca_nom,reg_nom,champ_recherche"
)


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
    aliases: tuple[str, ...] = ()

    @property
    def label(self) -> str:
        return f"{self.name} ({self.acronym})" if self.acronym else self.name

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
    """Page through the register and return every record."""
    settings = settings or get_settings()
    records: list[dict] = []
    offset = 0
    while True:
        url = f"{BASE}?limit={PAGE}&offset={offset}&select={FIELDS}"
        request = urllib.request.Request(
            url, headers={"User-Agent": "reperes/0.1 (local research tool)"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.load(response)
        except (urllib.error.URLError, ValueError) as exc:
            raise DirectoryError(f"Could not read the register: {exc}") from None
        page = payload.get("results", [])
        records.extend(page)
        offset += len(page)
        if len(page) < PAGE or offset >= payload.get("total_count", 0):
            break
    return records


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
        name = row.get("uo_lib") or row.get("uo_lib_officiel") or ""
        if not name:
            continue
        aliases = {a.strip() for a in (row.get("champ_recherche") or "").split(";")
                   if a.strip()}
        for key in ("uo_lib_officiel", "uo_lib_en", "sigle"):
            if row.get(key):
                aliases.add(str(row[key]))
        institutions.append(Institution(
            uai=row.get("uai") or "",
            name=name,
            acronym=row.get("sigle") or "",
            kind=_first(row.get("type_d_etablissement")),
            sector=row.get("secteur_d_etablissement") or "",
            url=row.get("url") or "",
            commune=row.get("com_nom") or "",
            postcode=row.get("code_postal_uai") or "",
            departement=row.get("dep_nom") or "",
            departement_id=(row.get("dep_id") or "").lstrip("D"),
            academie=row.get("aca_nom") or "",
            region=row.get("reg_nom") or "",
            aliases=tuple(sorted(aliases)),
        ))
    return tuple(sorted(institutions, key=lambda i: _fold(i.name)))


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
        haystacks = [institution.name, institution.acronym, *institution.aliases]
        folded = [_fold(h) for h in haystacks if h]
        if any(h.startswith(needle) for h in folded):
            starts.append(institution)
        elif any(needle in h for h in folded):
            contains.append(institution)
    return (starts + contains)[:limit]


def by_uai(uai: str) -> Institution | None:
    return next((i for i in load() if i.uai == uai), None)


def universities() -> tuple[Institution, ...]:
    return tuple(i for i in load() if "universit" in _fold(i.kind))


if __name__ == "__main__":  # pragma: no cover - operational entry point
    count = refresh()
    print(f"cached {count} institutions")
