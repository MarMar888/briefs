#!/usr/bin/env bash
# Install the bridge crontab (UTC) — mirrors the GitHub Actions schedules:
#   daily-pipeline.yml  cron "0 11 * * *"     -> daily import + geo + site
#   backfill.yml        cron "0 */4 * * *"    -> drain geo + site_pending queue
#   enrich.yml          cron "0 12 * * *"     -> Lead Audit on matched leads
# Different verticals run in parallel; same-vertical overlap is prevented by flock
# inside run-pipeline.sh (the scan-<vertical> concurrency group).
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/briefs}"
RP="$REPO_DIR/deploy/oracle/run-pipeline.sh"
chmod +x "$REPO_DIR/deploy/oracle/"*.sh

crontab - <<EOF
# === Briefs bridge (temporary) — UTC. Remove everything with:  crontab -r ===
SHELL=/bin/bash
# Daily ingest (11:00 UTC)
0 11 * * * $RP daily outdoor
0 11 * * * $RP daily construction
0 11 * * * $RP daily minnesota
# Backfill — every 4h
0 */4 * * * $RP backfill outdoor
0 */4 * * * $RP backfill construction
0 */4 * * * $RP backfill minnesota
# Lead Audit (12:00 UTC)
0 12 * * * $RP enrich outdoor
0 12 * * * $RP enrich construction
0 12 * * * $RP enrich minnesota
EOF

echo "[install-cron] installed:"
crontab -l
echo
echo "When GitHub Actions is back, tear the bridge down with:  crontab -r"
