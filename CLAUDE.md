# AdminTrace — instructions for Claude Code

AdminTrace answers questions about French administration from authoritative
official sources only. Python + Gradio; retrieval over the public-administration
corpus plus a closed registry of official sites read live. Every claim an
answer makes is checked against the evidence it came from.

This file is AdminTrace's own. The `career-ops` directory above it is a separate
repository with separate instructions; never stage, commit or publish
anything outside `reperes/`.

## The GitHub workflow — follow it after every completed task

GitHub is the canonical history. A finished, tested change should reach it
without being asked.

After completing a logical implementation task:

1. **Run the relevant tests.** At minimum the offline suite
   (`uv run pytest -q`). Add network (`ADMINTRACE_NETWORK_TESTS=1`) or browser
   (`uv run python -m app.browser_tests`) runs when the change touches those
   paths.
2. **Inspect the change.** `git status` and `git diff`. Confirm every file
   belongs to this task.
3. **Commit and push with the ship script**, naming only this task's paths:

   ```bash
   scripts/ship.sh --body-file /path/to/body.txt "fix: what changed" path/one path/two
   ```

   It stages only those paths, checks whitespace and conflict markers, scans
   for secrets, re-runs the tests, commits, fetches, refuses on divergence,
   and pushes. Put the explanation and the `Co-Authored-By` trailer in the
   body file.
4. **Report the result** exactly as the script prints it:

   ```
   Commit:  <hash>
   Branch:  <branch>
   Remote:  origin
   Push:    SUCCESS | NOT PUSHED — <reason> | FAILED — <reason>
   GitHub:  <owner/repo>
   ```

   If the push did not succeed, say so. Never describe a local commit as
   synchronized. `uv run python -m app.github_status --fetch` confirms.

One commit per logical task, not per file edit. Conventional messages:
`feat:`, `fix:`, `perf:`, `refactor:`, `test:`, `docs:`, `chore:`, `ci:` —
followed by what actually changed. Never "update", "changes", "final", "misc".

## Never

- push failing tests, or commit work that is unfinished or experimental;
- commit secrets, `.env`, credentials, or anything the secret scan flags —
  if it flags something, stop and report it rather than working around it;
- stage unrelated changes. If the tree holds work that is not this task's,
  leave it untouched and mention it;
- force-push, reset a shared branch, rewrite published history, or delete a
  branch without explicit instruction in this conversation;
- resolve a divergence by overwriting either side. Report
  "Local and remote histories have diverged" and wait;
- change the repository's visibility, or create tags or releases, unless asked.

## Data that must never be published

`data/` is runtime state and is gitignored in full. In particular:

- `data/bugs/`, `data/reports/` hold **real user bug reports** — private;
- `data/sources/`, `data/cache/` hold fetched pages, versions and the audit log;
- `data/chroma/`, `data/raw/`, `data/extracted/` are the built corpus;
- `data/production_status.json` and `data/status_history/` are derived by
  `app.production_report` / `app.verify` and never hand-edited.

Test fixtures are synthetic or public source text. Anything drawn from a real
conversation or bug report is anonymised before it goes near `tests/`.

## Source registry changes

Fetched pages stay in runtime storage; they are never committed. Only durable,
reviewed registry changes are code: a live-verified new source, an approved
canonical-domain change, corrected metadata. For those: update the registry,
run the tests (including `ADMINTRACE_NETWORK_TESTS=1` for the affected source),
then ship. Never promote an unverified source change into the registry.

## Rules the codebase already enforces — do not work around them

- No administrative fact (deadline, fee, duration, document list) in code:
  `tests/test_no_hardcoded_facts.py`. Reword; never weaken the test.
- The production scorecard is derived from evidence, never hand-edited.
- Claim validation (`CLAIM_VALIDATION`) stays on.
- `groq/compound*` models are refused: they browse the web and would bypass
  retrieval and provenance.
