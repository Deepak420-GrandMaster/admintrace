# Bug reports

A reader presses **Report a problem**, writes in any language, and sends it.
The report is written to `data/bugs/open/<id>/` — `report.json` for tools and
`report.md` for people — before any delivery is attempted, and optionally
e-mailed if SMTP is configured.

**Real reports are private.** `data/` is gitignored in full; no real report is
ever committed, pushed, used as a test fixture, or pasted into an issue. What
is versioned is the shape, below, and synthetic examples.

## `report.json`

| Field | Meaning |
|---|---|
| `id`, `created_at`, `status` | identity and lifecycle (`open`, …) |
| `severity`, `category` | from triage, or `untriaged` |
| `language` | the language the reader wrote in |
| `user_report` | the reader's own words, exactly as written |
| `what_doing` | optional: what they were trying to do |
| `user_question`, `answer_language`, `refused` | the question at the time |
| `institution`, `sources` | what the answer was about and cited |
| `title`, `translation`, `expected`, `actual` | triage, in English |
| `likely_cause`, `suggested_fix`, `reproduction_steps`, `affected_feature` | triage |
| `confidence`, `needs_more_info`, `triage_error` | how far triage can be trusted |
| `browser`, `viewport`, `app_version`, `environment` | where it happened |
| `conversation_context`, `conversation_state` | pending task and clarification state |
| `source_selection` | candidates, what was selected, and why the rest were rejected |
| `claim_validation` | every claim the answer made, its verdict, reason, sources and versions |
| `emailed`, `email_error` | whether delivery actually happened |
| `resolution` | filled in when the report is closed |

`emailed` is only ever true after a real send succeeded; a stored report never
claims more than happened.

## A synthetic example

```json
{
  "id": "BUG-0000",
  "created_at": "2026-01-01T09:00:00+00:00",
  "status": "open",
  "severity": "high",
  "category": "retrieval",
  "language": "en",
  "user_report": "Example: the answer gave a renewal deadline for a validation question.",
  "user_question": "how to validate the visa",
  "refused": false,
  "sources": ["administration-etrangers-en-france.interieur.gouv.fr"],
  "claim_validation": {
    "claims_generated": 2,
    "claims_removed": 1,
    "claims": [
      {"claim_type": "deadline", "support": "contradicted",
       "reason": "procedure_mix: claim is about residence permit renewal, question is about VLS-TS validation",
       "action": "removed"}
    ]
  },
  "emailed": false,
  "email_error": ""
}
```
