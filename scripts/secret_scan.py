#!/usr/bin/env python3
"""Look for credentials before they reach GitHub. Never prints one.

    python scripts/secret_scan.py              # working tree: tracked + not ignored
    python scripts/secret_scan.py --staged     # what the next commit would add
    python scripts/secret_scan.py --history    # every commit, every branch

Reports where a finding is and what kind it is — never the value. On a
machine with a local .env, the real values in it are also searched for by
exact match, which catches a key pasted somewhere no pattern would recognise.

Exits 1 on any finding, so it can gate a commit, a push or a CI job.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = {
    "groq_key": r"gsk_[A-Za-z0-9]{40,}",
    "github_token": r"(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}",
    "openai_key": r"sk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{30,}",
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "slack_token": r"xox[abpors]-[A-Za-z0-9-]{10,}",
    "google_api_key": r"AIza[0-9A-Za-z_-]{35}",
    "huggingface_token": r"hf_[A-Za-z0-9]{30,}",
    "private_key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----",
    "password_assignment":
        r"(?i)\b(?:smtp_password|password|passwd|secret|api_key)\s*[=:]\s*['\"]?[^\s'\"#{}$<>,;)]{8,}",
}
COMPILED = {name: re.compile(rx) for name, rx in PATTERNS.items()}

#: Context that shows a "password = ..." match is code reading a value, a
#: template placeholder, or minified markup — not a credential.
BENIGN_CONTEXT = re.compile(
    r"(?i)(_raw\(|getenv|environ|settings\.|repr=False|field\(|\(unset\)|placeholder|"
    r"example|<|\{|!0|!1|:!|smtp_password\s*=\s*\"\"|os\.environ)")

#: Files that are scanned-for rather than scanned: this script's own patterns.
SELF = {"scripts/secret_scan.py"}


def local_secrets() -> dict[str, str]:
    env = ROOT / ".env"
    if not env.exists():
        return {}
    found = {}
    for line in env.read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"^\s*([A-Z0-9_]*(?:KEY|TOKEN|PASSWORD|SECRET|USERNAME)[A-Z0-9_]*)\s*=\s*(.*)$", line)
        if m:
            value = m.group(2).strip().strip("'\"")
            if len(value) >= 8:
                found[m.group(1)] = value
    return found


def scan_text(text: str, where: str, secrets: dict[str, str]) -> list[tuple[str, str]]:
    findings = []
    for name, rx in COMPILED.items():
        for hit in rx.finditer(text):
            if name == "password_assignment":
                window = text[max(0, hit.start() - 40): hit.end() + 40]
                if BENIGN_CONTEXT.search(window):
                    continue
            findings.append((where, name))
    for label, value in secrets.items():
        if value in text:
            findings.append((where, f"value of {label} from the local .env"))
    return findings


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def scan_tree(secrets) -> list:
    files = [f for f in git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
             if f and f not in SELF]
    out = []
    for f in files:
        path = ROOT / f
        if path.is_file():
            out += scan_text(path.read_text(encoding="utf-8", errors="ignore"), f, secrets)
    return out


def scan_patch(patch: str, label, secrets) -> list:
    out, where, buf = [], "", []

    def flush():
        if buf and where.split(":")[-1] not in SELF:
            out.extend(scan_text("\n".join(buf), where, secrets))

    for line in patch.splitlines():
        if line.startswith("@@COMMIT "):
            flush(); buf = []; label = line.split()[1]
        elif line.startswith("+++ b/"):
            flush(); buf = []; where = f"{label}:{line[6:]}" if label else line[6:]
        elif line.startswith("+") and not line.startswith("+++"):
            buf.append(line[1:])
    flush()
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--staged", action="store_true")
    mode.add_argument("--history", action="store_true")
    args = parser.parse_args(argv)

    secrets = local_secrets()
    if args.staged:
        findings = scan_patch(git("diff", "--cached", "--no-color"), "", secrets)
        scope = "staged changes"
    elif args.history:
        log = git("log", "--all", "-p", "--no-color", "--pretty=format:@@COMMIT %h")
        findings = scan_patch(log, "", secrets)
        scope = f"history ({log.count('@@COMMIT ')} commits)"
    else:
        findings = scan_tree(secrets)
        scope = "working tree"

    print(f"secret scan: {scope}; local .env values checked: {len(secrets)}")
    for where, kind in sorted(set(findings)):
        print(f"  FOUND {kind}: {where}")
    print("result: " + ("FAIL" if findings else "PASS"))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
