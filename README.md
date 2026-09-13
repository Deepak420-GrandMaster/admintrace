# Repères

Grounded question answering for people who have recently arrived in France, or
are about to. Ask in English or French; answers come only from official French
government sources, cite the source fiche by its French title, and carry the
date that fiche was last updated.

Everything runs locally. No API keys.

> **Status:** Milestone 1, in progress. This README is completed at the end of
> the build (Step 12). What is below is accurate as of the current step.

## Not legal advice

Repères reports what official sources say. It does not tell anyone what to do
about a specific refusal, appeal, or individual case.

## Data source and attribution

The corpus is the open-data publication of **service-public.gouv.fr** by the
**Direction de l'information légale et administrative (DILA)**, distributed on
data.gouv.fr under the **Licence Ouverte / Open Licence**.

| | |
|---|---|
| Feed version in use | **3.5** |
| Particuliers | [Fiches pratiques et ressources de Service-Public.gouv.fr Particuliers](https://www.data.gouv.fr/datasets/fiches-pratiques-et-ressources-de-service-public-gouv-fr-particuliers) |
| Entreprendre | [Fiches pratiques et ressources Entreprendre - Service-Public.gouv.fr](https://www.data.gouv.fr/datasets/fiches-pratiques-et-ressources-entreprendre-service-public-gouv-fr) |

The Licence Ouverte requires stating the source and the date the information
was last updated. Repères does this on every citation. That obligation is also
the trust mechanism, and it is never stripped.

Only the French source text is ingested. French is the legal source of truth;
machine-translated versions are disclaimed by the government that publishes
them.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and [Ollama](https://ollama.com).

```bash
uv sync --extra dev
cp .env.example .env
```

Further steps are documented as the build progresses.
