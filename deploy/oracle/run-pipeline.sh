#!/usr/bin/env bash
# Run one pipeline step on the bridge VM.
#   Usage: run-pipeline.sh <daily|backfill|enrich> <outdoor|construction|minnesota>
# Mirrors the per-vertical flags AND the concurrency groups of the GitHub workflows:
#   daily-pipeline.yml, backfill.yml, enrich.yml.
set -uo pipefail

MODE="${1:?usage: run-pipeline.sh <daily|backfill|enrich> <vertical>}"
VERT="${2:?usage: run-pipeline.sh <daily|backfill|enrich> <vertical>}"
REPO_DIR="${REPO_DIR:-$HOME/briefs}"
HERE="$REPO_DIR/deploy/oracle"
cd "$REPO_DIR"

# --- secrets ---
[ -f "$HERE/.env" ] || { echo "missing $HERE/.env (copy .env.example and fill it in)"; exit 2; }
set -a; . "$HERE/.env"; set +a
export DOMAIN_DB_PATH="${TURSO_DB_URL:?set TURSO_DB_URL in .env}"   # store reads DOMAIN_DB_PATH or TURSO_DB_URL

PY="$REPO_DIR/.venv/bin/python"
mkdir -p "$REPO_DIR/logs"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$REPO_DIR/logs/${MODE}-${VERT}-${TS}.log"

# Playwright is memory-heavy; keep workers under the VM's RAM.
case "$VERT" in
  minnesota)            export SITE_WORKERS=12 ;;
  outdoor|construction) export SITE_WORKERS=8 ;;
  *) echo "unknown vertical: $VERT"; exit 2 ;;
esac

# Concurrency, mirroring CI: daily+backfill share scan-<vert>; enrich is its own group.
case "$MODE" in
  daily|backfill) LOCK="/tmp/briefs-scan-${VERT}.lock" ;;
  enrich)         LOCK="/tmp/briefs-audit-${VERT}.lock" ;;
  *) echo "unknown mode: $MODE"; exit 2 ;;
esac

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "[$(date -u)] SKIP ${MODE}/${VERT}: an earlier run for this vertical is still going"
  exit 0
fi

echo "[$(date -u)] START ${MODE}/${VERT} workers=$SITE_WORKERS  log=$LOG"
{
  echo "=== ${MODE}/${VERT} @ ${TS}  SITE_WORKERS=${SITE_WORKERS} ==="
  case "${MODE}:${VERT}" in
    daily:minnesota)    "$PY" run.py --domains-only --domain-source domainsmonitor --domain-limit 0 --geo-limit 8000 --site-limit 4000 --vertical minnesota ;;
    daily:*)            "$PY" run.py --domains-only --domain-source domainsmonitor --keywords --domain-limit 3000 --geo-limit 2000 --site-limit 200 --vertical "$VERT" ;;
    backfill:minnesota) "$PY" run.py --domains-only --skip-domain-import --domain-source domainsmonitor-file --geo-limit 6000 --site-limit 5000 --vertical minnesota ;;
    backfill:*)         "$PY" run.py --domains-only --skip-domain-import --domain-source domainsmonitor-file --keywords --geo-limit 1000 --site-limit 1000 --vertical "$VERT" ;;
    enrich:*)           "$PY" enricher.py --limit 0 --reaudit stale --vertical "$VERT" ;;
  esac
} >>"$LOG" 2>&1
code=$?
echo "[$(date -u)] END ${MODE}/${VERT} exit=${code}  (tail -f $LOG)"
exit $code
