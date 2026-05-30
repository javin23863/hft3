#!/usr/bin/env bash
# Deprecated wrapper: use chi404_run_trial_live.sh (live) or chi404_run_trial_smoke.sh (CI).
set -euo pipefail
REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
echo "Redirecting to chi404_run_trial_live.sh (no synthetic injection)" >&2
exec bash "$REPO/scripts/chi404_run_trial_live.sh"
