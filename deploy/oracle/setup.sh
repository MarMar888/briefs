#!/usr/bin/env bash
# One-time setup for the Briefs pipeline on an Oracle Cloud Always-Free ARM VM.
# Target: Ubuntu 24.04, VM.Standard.A1.Flex (4 OCPU / 24 GB). Safe to re-run.
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/briefs}"
cd "$REPO_DIR"

echo "[setup] apt packages..."
sudo apt-get update -y
sudo apt-get install -y python3-pip build-essential libssl-dev curl git ca-certificates

# Ubuntu 24.04 ships python3.12. On 22.04, pull it from deadsnakes.
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "[setup] installing python3.12 from deadsnakes..."
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -y
fi
sudo apt-get install -y python3.12 python3.12-venv

echo "[setup] timezone -> UTC (so cron lines up with the GitHub workflow crons)"
sudo timedatectl set-timezone UTC || true

echo "[setup] venv + python deps (this pulls crawl4ai; a few minutes on ARM)..."
python3.12 -m venv "$REPO_DIR/.venv"
"$REPO_DIR/.venv/bin/pip" install --upgrade pip
"$REPO_DIR/.venv/bin/pip" install -r "$REPO_DIR/requirements.txt"

echo "[setup] Playwright Chromium + OS libraries (ARM build)..."
"$REPO_DIR/.venv/bin/python" -m playwright install --with-deps chromium

mkdir -p "$REPO_DIR/logs"
chmod +x "$REPO_DIR/deploy/oracle/"*.sh

echo
echo "[setup] DONE."
echo "  next 1) cp deploy/oracle/.env.example deploy/oracle/.env  &&  edit it"
echo "       2) smoke test:  deploy/oracle/run-pipeline.sh daily minnesota"
echo "       3) install cron: deploy/oracle/install-cron.sh"
