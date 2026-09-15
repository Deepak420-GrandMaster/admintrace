# Official sources

Claré answers from documents it retrieved and can show you. Two kinds:

* the **corpus** — the DILA publication of service-public.gouv.fr, ingested
  locally, which covers French public administration;
* **registered live sources** — a closed list of official sites read over the
  web, for the questions the corpus structurally cannot answer. An
  institution's own admission rules are not public administration; they are
  that institution's rule, published by that institution.

A domain is evidence because it is written down in
`app/sources/registry.yml`, verified, and dated. Not because it ranked well,
and not because someone pasted it into the chat.

## Checking

```bash
uv run python -m app.live_source_check            # read-only
uv run python -m app.live_source_check --write    # record health
uv run python -m app.live_source_check --source mbs --json
```

Health is a state, not a pass/fail, because the remedies differ:

| State | Meaning |
|---|---|
| `healthy` | Read over plain HTTP. |
| `healthy_rendered` | Only readable once its scripts run. Still a good source. |
| `stale` | Readable; the stored copy is past its freshness window. |
| `changed` | Readable, and what it says has materially changed. |
| `unavailable` | Could not be reached. |
| `parser_failure` | Reached, nothing readable came out — even rendered. |
| `blocked` | Reached and deliberately refused: a bot wall. Not routed around. |
| `unverified` | Registered, never checked. |

## Refreshing

Each source declares how much it matters, and that sets its interval:

| Priority | Re-read after |
|---|---|
| `critical` | 8 hours |
| `high` | 24 hours |
| `medium` | 3 days |
| `low` | 14 days |

```bash
uv run python -m app.sync_sources --dry-run      # fetch, compare, change nothing
uv run python -m app.sync_sources --all --due    # only what is overdue
```

A sync stores a version, activates it **only if it parsed to something
usable**, classifies what changed, invalidates the work that rested on the old
wording, and writes the decision to `data/sources/audit.jsonl`. A page that
comes back as an error, a consent wall or an empty application shell never
replaces a good version: serving an answer a few days old beats serving none.

### On a schedule

`scripts/refresh-sources.sh` is what a scheduler runs. It exits non-zero when
a change needs review, so the scheduler's own failure notification is the
alert and there is no second alerting system to maintain.

```cron
17 */6 * * * /path/to/reperes/scripts/refresh-sources.sh >> /tmp/clare-refresh.log 2>&1
```

On macOS, launchd survives sleep better than cron:

```bash
cp docs/io.clare.refresh.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/io.clare.refresh.plist
```

In CI, `.github/workflows/refresh-sources.yml` does the same every six hours
and keeps the audit log as an artifact.

## Rendered sources

Some official services — ANEF, ANTS (now France Titres), France Travail —
serve an empty shell and build the page in the browser. Reading them needs the
optional extra:

```bash
uv sync --extra render
uv run playwright install chromium
```

Rendering is a **second** path, never the first: static HTML is read first and
a browser is used only when that produced nothing, and only for a domain that
is already registered. The renderer blocks downloads, uploads, forms,
authentication and any top-level navigation off the approved domain. It is a
reader for public pages, not an agent.

## Rebrands

Institutions move. `montpellier-bs.com` now answers as `mbs-education.com`;
`pole-emploi.fr` as `francetravail.fr`. The registry records the canonical
domain and the `legacy_domains` it answers on, so following such a redirect is
recognised as the same body instead of refused as a hijack — while a redirect
to a domain that is *not* recorded is still refused, and logged as a
`source.domain_change` for someone to verify.

That refusal is not theoretical: `caf.fr` redirects to a third-party bot
check, and the fetcher declines to follow it.
