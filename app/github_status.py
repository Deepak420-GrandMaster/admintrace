"""Is this checkout in step with GitHub?

    uv run python -m app.github_status            # uses what git last fetched
    uv run python -m app.github_status --fetch    # asks the remote first

Read-only. It never pulls, pushes, resets or changes a branch. "Synced" means
the local branch and its upstream point at the same commit *and* the working
tree is clean — a clean tree with unpushed commits is not synced, and neither
is an up-to-date branch with uncommitted work.

Exit code: 0 synced; 1 anything that needs a person (dirty, ahead, behind,
diverged, no remote, no upstream).
"""

from __future__ import annotations

import argparse
import subprocess
import sys


def _git(*args: str) -> tuple[int, str]:
    done = subprocess.run(["git", *args], capture_output=True, text=True)
    return done.returncode, done.stdout.strip()


def status(fetch: bool = False) -> dict:
    _, branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    _, local = _git("rev-parse", "--short", "HEAD")
    _, porcelain = _git("status", "--porcelain")
    _, remotes = _git("remote")
    info = {
        "branch": branch, "local": local,
        "working_tree": "dirty" if porcelain else "clean",
        "changed_files": len(porcelain.splitlines()) if porcelain else 0,
        "remote": "origin" if "origin" in remotes.split() else "",
        "remote_url": "", "upstream": "", "remote_commit": "",
        "ahead": 0, "behind": 0, "fetched": False, "status": "",
    }
    if not info["remote"]:
        info["status"] = "NO REMOTE"
        return info
    _, url = _git("remote", "get-url", "origin")
    # Never echo credentials that a URL might carry.
    info["remote_url"] = url.split("@")[-1] if "@" in url and "://" in url else url
    if fetch:
        code, _ = _git("fetch", "--quiet", "origin")
        info["fetched"] = code == 0
    code, upstream = _git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if code != 0:
        info["status"] = "NO UPSTREAM"
        return info
    info["upstream"] = upstream
    _, info["remote_commit"] = _git("rev-parse", "--short", "@{u}")
    _, counts = _git("rev-list", "--left-right", "--count", "HEAD...@{u}")
    ahead, behind = (int(n) for n in counts.split())
    info["ahead"], info["behind"] = ahead, behind
    if ahead and behind:
        info["status"] = "DIVERGED"
    elif ahead:
        info["status"] = "AHEAD"
    elif behind:
        info["status"] = "BEHIND"
    else:
        info["status"] = "SYNCED" if info["working_tree"] == "clean" else "SYNCED, UNCOMMITTED CHANGES"
    return info


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.github_status", description=__doc__.splitlines()[0])
    parser.add_argument("--fetch", action="store_true", help="fetch from origin first")
    args = parser.parse_args(argv)
    info = status(fetch=args.fetch)

    print("CLARÉ ↔ GITHUB\n")
    print(f"  Branch        {info['branch']}")
    print(f"  Working tree  {info['working_tree']}"
          + (f" ({info['changed_files']} file(s))" if info['changed_files'] else ""))
    print(f"  Local         {info['local']}")
    if info["remote"]:
        print(f"  Remote        {info['remote']}  {info['remote_url']}")
    if info["upstream"]:
        print(f"  Upstream      {info['upstream']}  {info['remote_commit']}"
              + ("" if info["fetched"] else "  (as last fetched; --fetch to refresh)"))
        print(f"  Ahead/behind  {info['ahead']} / {info['behind']}")
    print(f"\n  Status        {info['status']}")
    if info["status"] == "DIVERGED":
        print("\n  Local and remote histories have diverged. Reconcile by hand;"
              "\n  nothing here will force-push or reset.")
    return 0 if info["status"] == "SYNCED" else 1


if __name__ == "__main__":
    sys.exit(main())
