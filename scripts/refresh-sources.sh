#!/usr/bin/env bash
# Re-read the official sources that are due, and say if anything needs a look.
#
# This is what a scheduler runs. It is deliberately boring: it syncs only what
# is past its own refresh interval, writes everything to the audit log, and
# exits non-zero when a change needs human review so the scheduler's own
# failure notification does the alerting.
#
#   crontab -e
#   17 */6 * * * /path/to/reperes/scripts/refresh-sources.sh >> /tmp/clare-refresh.log 2>&1
#
# On macOS, launchd is better behaved than cron for this; see docs/SOURCES.md.
set -euo pipefail

cd "$(dirname "$0")/.."

echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) refreshing due sources ==="

# Health first: a source that has moved, gone dark or started refusing bots
# should be known before its content is compared against yesterday's.
uv run python -m app.live_source_check --write || true

uv run python -m app.sync_sources --all --due --json > /tmp/clare-sync.json
uv run python - <<'PY'
import json, sys
rows = json.load(open("/tmp/clare-sync.json"))
attention = [r for r in rows if r.get("severity") in ("critical", "high")
             and r.get("change") in ("substantive", "critical")]
print(f"{len(rows)} page(s) synced, {len(attention)} needing review")
for row in attention:
    print(f"  [{row['severity'].upper()}] {row['source']} {row['url']}")
    print(f"    {row['summary']}")
    if row.get("topics"):
        print(f"    invalidates: {', '.join(row['topics'])}")
sys.exit(1 if attention else 0)
PY
