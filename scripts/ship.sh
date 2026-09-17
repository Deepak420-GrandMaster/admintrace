#!/usr/bin/env bash
# Commit a finished, tested change and push it to GitHub. Safely.
#
#   scripts/ship.sh "fix: preserve the task through clarification" PATH [PATH...]
#   scripts/ship.sh --body-file notes.txt "feat: add claim validation" PATH...
#
# Stages ONLY the paths named — never "git add -A" — so unrelated work in the
# tree is left exactly where it is. Then, in order, stopping at the first
# problem and never committing past one:
#
#   1. whitespace errors and conflict markers   git diff --cached --check
#   2. secrets in what is staged, and the tree  scripts/secret_scan.py
#   3. the commit message                       conventional, specific
#   4. tests                                    offline suite by default
#   5. commit
#   6. fetch, refuse on divergence              never --force, never reset
#   7. push the current branch
#
# Exit: 0 pushed · 1 refused before commit · 2 committed, not pushed (no
# remote, diverged, or push failed — the local commit is kept).
#
# SHIP_TESTS overrides the test command, e.g. SHIP_TESTS="uv run pytest -q tests/test_i18n.py"
# for a change that cannot affect anything else. It cannot be empty.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"

body_file=""
if [[ "${1:-}" == "--body-file" ]]; then body_file="$2"; shift 2; fi
subject="${1:-}"; shift || true
if [[ -z "$subject" || $# -eq 0 ]]; then
  echo "usage: scripts/ship.sh [--body-file FILE] \"type: summary\" PATH [PATH...]" >&2
  exit 1
fi

stop() { echo "REFUSED: $*" >&2; exit 1; }

branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "$branch" == "HEAD" ]] && stop "detached HEAD; check out a branch first"

# 3 first, because it is free: a bad message should not cost a test run.
if ! [[ "$subject" =~ ^(feat|fix|perf|refactor|test|docs|chore|ci|build|style|revert)(\([a-z0-9._-]+\))?:\ .{8,}$ ]]; then
  stop "message must look like 'fix: what changed' (feat|fix|perf|refactor|test|docs|chore|ci|build|style|revert)"
fi
if [[ "$subject" =~ :\ (update|updates|changes|change|final|misc|stuff|wip|fixes)$ ]]; then
  stop "message says nothing about the change: '$subject'"
fi

paths=("$@")
echo "==> staging only: ${paths[*]}"
git add -- "${paths[@]}"
# From here until the commit lands, any failure — including one this script
# did not anticipate — unstages what it staged, so a refused run leaves the
# index exactly as it found it. The paths are captured once: inside a trap
# fired from a function, "$@" is that function's arguments, and an earlier
# version unstaged nothing because of it.
committed=0
unstage_on_failure() { if [[ $committed -eq 0 ]]; then git reset -q -- "${paths[@]}" 2>/dev/null || true; fi; }
trap unstage_on_failure EXIT
if git diff --cached --quiet; then stop "nothing staged from the paths given"; fi
git diff --cached --stat

echo "==> whitespace and conflict markers"
git diff --cached --check || stop "git diff --check found problems"

echo "==> secret scan"
python3 scripts/secret_scan.py --staged || { stop "possible secret in staged changes (unstaged again)"; }
python3 scripts/secret_scan.py || { stop "possible secret in the working tree (unstaged again)"; }

tests="${SHIP_TESTS:-uv run pytest -q}"
echo "==> tests: $tests"
if ! bash -c "$tests"; then
  stop "tests failed; nothing committed (changes unstaged, untouched)"
fi

echo "==> commit"
# One message file: git refuses -m and -F together, which this script once
# tried, and failed on every commit that carried a body.
message="$(mktemp)"
printf '%s\n' "$subject" > "$message"
if [[ -n "$body_file" ]]; then
  [[ -r "$body_file" ]] || stop "body file not readable: $body_file"
  printf '\n' >> "$message"
  cat "$body_file" >> "$message"
fi
git commit -q -F "$message"
rm -f "$message"
committed=1
trap - EXIT
commit="$(git rev-parse --short HEAD)"

report() {
  echo
  echo "Commit:  $commit"
  echo "Branch:  $branch"
  echo "Remote:  ${1}"
  echo "Push:    ${2}"
  [[ -n "${3:-}" ]] && echo "GitHub:  ${3}"
  return 0
}

if ! git remote get-url origin >/dev/null 2>&1; then
  report "(none)" "NOT PUSHED — no origin remote configured; local commit kept"
  exit 2
fi
repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || git remote get-url origin | sed -E 's#.*[:/]([^/]+/[^/.]+)(\.git)?$#\1#')"

echo "==> fetch and divergence check"
if ! git fetch --quiet origin; then
  report origin "NOT PUSHED — fetch failed; local commit kept" "$repo"
  exit 2
fi
if git rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1; then
  read -r ahead behind < <(git rev-list --left-right --count "HEAD...@{u}")
  if (( behind > 0 )); then
    report origin "NOT PUSHED — local and remote histories have diverged (ahead $ahead, behind $behind). Reconcile by hand; nothing was forced." "$repo"
    exit 2
  fi
  push_args=(origin HEAD)
else
  push_args=(-u origin HEAD)
fi

echo "==> push"
if git push "${push_args[@]}"; then
  report origin "SUCCESS" "$repo"
  exit 0
fi
report origin "FAILED — see the git output above; local commit kept, nothing forced" "$repo"
exit 2
