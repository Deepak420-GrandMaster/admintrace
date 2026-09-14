# Claré

Grounded question answering for people who have recently arrived in France, or
are about to.

Ask in English or French. Answers come only from official French government
sources, keep the French administrative vocabulary you will actually need at a
counter, and cite each source by its French title with the date it was last
updated. When the sources do not answer the question, Claré says so instead
of guessing.

Everything that touches the corpus runs locally.

## Not legal advice

Claré reports what official sources say and cites them. It cannot tell you
what to do about a refusal, an appeal, or your individual case. For that,
contact the administration concerned. The interface says so on every screen.

## Data source and attribution

The corpus is the open-data publication of **service-public.gouv.fr** by the
**Direction de l'information légale et administrative (DILA)**, distributed on
data.gouv.fr under the **Licence Ouverte / Open Licence**.

| | |
|---|---|
| Feed version | **3.5** (verified live; 3.3 is withdrawn, 3.4 is maintained in degraded mode) |
| Particuliers | [dataset](https://www.data.gouv.fr/datasets/fiches-pratiques-et-ressources-de-service-public-gouv-fr-particuliers) · 5,584 files |
| Entreprendre | [dataset](https://www.data.gouv.fr/datasets/fiches-pratiques-et-ressources-entreprendre-service-public-gouv-fr) · 2,646 files |
| Indexed | 7,768 documents → 43,794 passages |

The Licence Ouverte requires stating the source and the date the information
was last updated. Claré does that on every citation. It is also how a reader
decides whether to trust what they just read, so it is never stripped.

Only the French source text is ingested. French is the legal source of truth,
and the machine-translated versions are disclaimed by the government that
publishes them.

DILA also publishes a pre-chunked, pre-vectorised version of the same fiches.
It is deliberately not used here; it is a useful benchmark to compare our own
retrieval against later.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

```bash
uv sync --extra dev
cp .env.example .env       # then set GROQ_API_KEY
uv run python -m app.ingest.pipeline
uv run python -m app.ui.app
```

Then open **http://localhost:7860**.

The first ingestion downloads ~33 MB, parses it, and embeds 43,794 passages
locally. Embedding is the slow part — a few hours on an Apple GPU. It is
resumable: interrupt it and run the same command again.

### Models

| Job | Runs | Why |
|---|---|---|
| Embedding | locally, `BAAI/bge-m3` | Multilingual by requirement, not preference: the corpus is French and half the questions are English. An English-first model fails on exactly the vocabulary that matters. The corpus never leaves the machine. |
| Answering, translation, answerability | `LLM_PROVIDER` — `groq` or `ollama` | Interchangeable. Nothing outside `app/llm/` knows which is in use. |

Set `LLM_PROVIDER=ollama` with `OLLAMA_CHAT_MODEL` to run with no hosted API at
all. With `groq`, questions are sent to Groq; the corpus is not.

## How it works

```
question
  ├─ detect language ─────────────── hand-written EN/FR discrimination
  ├─ if English: translate to a French search query
  ├─ glossary expansion ──────────── adds the French term a document actually uses
  ├─ retrieve ─────────────────────── dense (bge-m3) + BM25, reciprocal rank fusion
  ├─ gate, stage 1 ────────────────── cosine similarity threshold
  ├─ gate, stage 2 ────────────────── do these passages answer the question?
  └─ answer in the asker's language, or refuse
```

### Why the gate has two stages

Similarity alone cannot decide whether to refuse. Measured against this
corpus, the question the sources genuinely do not answer (`gen-010`, "there are
no appointment slots at my préfecture") scores **higher** than the weakest
question they do answer:

```
answerable questions : min 0.542   median 0.660   max 0.772
gen-010, must refuse : 0.561 (en)  0.588 (fr)
```

No threshold separates them, because the corpus really does contain passages
about booking préfecture appointments. They are topically close; they simply do
not answer the question asked. Cosine similarity measures topical closeness,
not answerability, so a second stage asks that question directly. It can only
ever remove an answer, never add one.

### Why applicability is preserved

Many fiches are *conditioned*: their body splits into mutually exclusive
branches — `ListeSituations`/`Situation` and `BlocCas`/`Cas` — such as "first
year" versus "after one year of residence", which carry different document
lists and different fees. 1,085 documents branch this way, across 1,521
distinct branch labels.

Flattening them would let the rules for one situation be read as the rules for
another. Every chunk therefore belongs to exactly one branch and carries its
label, and no chunk mixes two.

## No administrative fact lives in code

Deadlines, fees, hour limits, document lists and eligibility conditions reach a
user only from a retrieved document. `tests/test_no_hardcoded_facts.py`
enforces this mechanically, failing if a monetary amount or a French duration
appears anywhere in `app/` or `eval/`.

## Evaluation

```bash
uv run python eval/run_eval.py
```

The gold set is 51 questions, each phrased in both English and French, across
student, worker, freelance, family and general segments. Results are written
to `eval/results/`.

The headline metric is **cross-lingual agreement**: how often the English and
French phrasings of the same question reach the same sources. It is the measure
of whether the central claim holds.

`expected_fiche_id` is deliberately empty. A guessed gold label is worse than
no gold label, so retrieval hit rate is reported as unmeasurable until those
are filled in by hand.

### Current results

Run on the 51-question set, both languages, against feed 3.5:

| | |
|---|---|
| Cross-lingual agreement (same top-3, any order) | **11.8%** (6/51) |
| Same top-3, same order | 2.0% (1/51) |
| Same top-1 source | 54.9% (28/51) |
| Mean overlap of top-3 | 42.0% |
| Distinct source documents per answer | 4.75 of 6 slots |

**Cross-lingual agreement is the weakest part of the system and is not yet good
enough.** The top source agrees more than half the time, and on average the two
phrasings share about two of three sources — but strict top-3 agreement is low.
The likely causes, in order of suspicion: the English question is translated
before retrieval, so the two paths search with genuinely different wording;
reciprocal rank fusion produces many near-ties among passages of similar
relevance, which reorder easily; and this corpus contains many near-duplicate
passages across fiches, so several defensible orderings exist.

Retrieval hit rate is not measurable until `expected_fiche_id` is filled in.

### Refusal

`gen-010` — "there are no appointment slots at my préfecture" — is the
designed refusal case. The similarity gate cannot catch it, for the reason
above. The answerability stage does catch it, with the right reasoning:

> NO. The passages only state that you must make an appointment and check the
> prefecture website; they do not explain what to do when no slots are
> available.

**But it does not catch it every time.** The check has been observed refusing
the question in one run and answering it in the next, with a fixed seed and
zero temperature. A gate that is right most of the time is not yet a gate, and
this is the most important open issue in the project.

`stu-002` — "what happens if I missed the three-month deadline" — is refused in
both languages. That is plausibly *correct*: the corpus documents the deadline,
not the consequences of missing it. The gold set assumes it is answerable, which
may be the gold set's error rather than the system's.

### Rate limits

One question costs two model calls, because the answerability check sends the
passages a second time. On Groq's free tier (8,000 tokens per minute) that is
enough to hit a 429 on consecutive questions. The provider now waits out the
interval the service reports and retries, twice, before surfacing the error.

## Known gaps

These are properties of the official corpus, not bugs:

- **Département-level practice** — which documents a particular préfecture
  actually asks for. Mostly absent, and it varies enormously. (Partially
  present in places: one fiche carries a branch per département.)
- **Appointment availability** — the single biggest real-world blocker, and
  the thing `gen-010` asks about. No official source answers it.
- **Actual processing times** versus published targets.
- **Which banks accept which documents** for opening an account.
- **Whether a given landlord or agency accepts Visale** in practice.

These need crowdsourced data rather than an official corpus.

Not yet ingested, and recorded as future sources: ANEF, Campus France,
CROUS/messervices.etudiant.gouv.fr, ameli.fr, caf.fr, urssaf.fr,
impots.gouv.fr, visale.fr, ANTS/France Titres.

The public directory of ~75,000 administrations is fetched and cached but
deliberately not wired into answers yet. Until it is, a refusal that names an
administration is drawing on the model's own knowledge rather than on data —
which is why refusals here point only to pages actually retrieved.

## Layout

```
app/
  config.py        every setting, read nowhere else
  ingest/          fetch → parse → chunk → embed
  retrieval/       dense, keyword, fusion, gate
  query/           language detection, translation, glossary
  answer/          prompts, generation, citations
  llm/             chat providers (groq, ollama)
  ui/              Gradio app, stylesheet, HTML rendering
  directory/       administration lookup (cached, not wired in)
eval/              gold set and metrics runner
tools/             glossary builder
```

## Licence and attribution

Source data © DILA, published under the
[Licence Ouverte / Open Licence](https://www.etalab.gouv.fr/licence-ouverte-open-licence).
Claré is not affiliated with, endorsed by, or operated by the French
government.
