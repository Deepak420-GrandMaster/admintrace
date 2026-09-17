#!/usr/bin/env bash
# Install Claré's local Git hooks. Hooks live in .git/hooks, which Git never
# versions, so every clone runs this once.
#
#   scripts/install_git_hooks.sh               # pre-push guard only (recommended)
#   scripts/install_git_hooks.sh --auto-push   # also push after every commit
#
# pre-push   refuses a push that would rewrite remote history (a force push,
#            whatever flag was typed) and one containing a detected secret.
# post-commit (opt-in) pushes the current branch after a commit — only when
#            it is a plain fast-forward. Divergence is reported, never forced.
#
# The day-to-day path is scripts/ship.sh, which tests before committing. The
# post-commit hook cannot test anything, which is why it is off by default.
set -euo pipefail
root="$(git rev-parse --show-toplevel)"
hooks="$(git rev-parse --git-path hooks)"
mkdir -p "$hooks"

cat > "$hooks/pre-push" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install_git_hooks.sh
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
zero="0000000000000000000000000000000000000000"
while read -r local_ref local_sha remote_ref remote_sha; do
  [[ "$local_sha" == "$zero" ]] && { echo "pre-push: refusing to delete $remote_ref" >&2; exit 1; }
  if [[ "$remote_sha" != "$zero" ]] && ! git merge-base --is-ancestor "$remote_sha" "$local_sha" 2>/dev/null; then
    echo "pre-push: refusing a push that rewrites $remote_ref (not a fast-forward)." >&2
    echo "pre-push: local and remote histories have diverged; reconcile by hand." >&2
    exit 1
  fi
done
python3 scripts/secret_scan.py >/dev/null || { echo "pre-push: possible secret in the tree; run scripts/secret_scan.py" >&2; exit 1; }
HOOK
chmod +x "$hooks/pre-push"
echo "installed: pre-push guard"

if [[ "${1:-}" == "--auto-push" ]]; then
  cat > "$hooks/post-commit" <<'HOOK'
#!/usr/bin/env bash
# Installed by scripts/install_git_hooks.sh --auto-push
cd "$(git rev-parse --show-toplevel)" || exit 0
git remote get-url origin >/dev/null 2>&1 || { echo "post-commit: no origin; not pushed"; exit 0; }
branch="$(git rev-parse --abbrev-ref HEAD)"; [[ "$branch" == "HEAD" ]] && exit 0
git fetch --quiet origin || { echo "post-commit: fetch failed; not pushed"; exit 0; }
if git rev-parse "@{u}" >/dev/null 2>&1; then
  behind="$(git rev-list --count "HEAD..@{u}")"
  [[ "$behind" -gt 0 ]] && { echo "post-commit: histories have diverged; not pushed"; exit 0; }
  git push --quiet origin HEAD && echo "post-commit: pushed $branch" || echo "post-commit: push FAILED; commit kept locally"
else
  git push --quiet -u origin HEAD && echo "post-commit: pushed $branch" || echo "post-commit: push FAILED; commit kept locally"
fi
HOOK
  chmod +x "$hooks/post-commit"
  echo "installed: post-commit auto-push"
fi
