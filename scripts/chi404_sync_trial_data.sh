#!/usr/bin/env bash
# Sync CHI404 trial captures, reports, and latency summary to local repo (workstation).
set -euo pipefail

REPO="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
CHI404="${CHI404_SSH_ALIAS:-chi404}"
REMOTE_REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"

mkdir -p "$REPO/runtime/latency_reports"
mkdir -p "$REPO/data/raw/rithmic_trial_live_capture"
mkdir -p "$REPO/reports/rithmic_trial"

echo "=== sync latency_summary.json ==="
scp "${CHI404}:${REMOTE_REPO}/runtime/latency_reports/latency_summary.json" \
  "$REPO/runtime/latency_reports/" || true

echo "=== sync trial raw captures (2026-05-* if present) ==="
scp -r "${CHI404}:${REMOTE_REPO}/data/raw/rithmic_trial_live_capture/2026-05-"* \
  "$REPO/data/raw/rithmic_trial_live_capture/" 2>/dev/null || true

echo "=== sync trial reports ==="
scp -r "${CHI404}:${REMOTE_REPO}/reports/rithmic_trial/2026-05-"* \
  "$REPO/reports/rithmic_trial/" 2>/dev/null || true

echo "Done. Local paths under $REPO/runtime/latency_reports and data/raw/rithmic_trial_live_capture"
