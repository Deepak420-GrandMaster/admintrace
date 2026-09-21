# Test fixtures

Small, versioned stand-ins for data AdminTrace otherwise downloads at runtime, so
the offline suite gives the same result on any machine — a laptop with the
full caches, or a CI runner with none.

## institutions.json

A handful of records from the ONISEP register of higher-education
institutions (public open data, `api.opendata.onisep.fr`), copied verbatim
with their original fields. At runtime the full register (~9,000 records,
~11 MB) is downloaded into the gitignored `data/cache/`.

Four tests once passed only because that download existed on the machine
running them. They now read this file instead.

No fixture here contains user data. Test examples are synthetic or public
source text; anything taken from a real conversation must be anonymised
before it is added.
