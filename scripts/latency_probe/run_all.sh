#!/usr/bin/env bash
# CHI404 colo-only orchestrator: CPU + network probes, then summarize.
# Run on bare metal only (never from a dev workstation in the hot path).
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "latency_probe run_all: Linux CHI404 bare metal only" >&2
  exit 1
fi

ENV_FILE="${HFT3_ENV_FILE:-/root/hft3/.env}"
[[ -f "$ENV_FILE" ]] && set -a && source "$ENV_FILE" && set +a

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
PROBE_DIR="$REPO/scripts/latency_probe"
export RUN_ID="${RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export LATENCY_REPORT_ROOT="${LATENCY_REPORT_ROOT:-$REPO/runtime/latency_reports}"
INCLUDE_TRIAL="${LATENCY_PROBE_INCLUDE_TRIAL_APPENDIX:-1}"
TRIAL_CAPTURE="${LATENCY_PROBE_TRIAL_CAPTURE:-0}"

echo "latency_probe run_all RUN_ID=$RUN_ID REPO=$REPO"
echo "  LATENCY_PROBE_INCLUDE_TRIAL_APPENDIX=$INCLUDE_TRIAL"
echo "  LATENCY_PROBE_TRIAL_CAPTURE=$TRIAL_CAPTURE"

if [[ "$TRIAL_CAPTURE" == "1" ]]; then
  echo "=== optional trial capture (paper orders must be sent in R|Trader VM) ==="
  bash "$REPO/scripts/chi404_run_trial_live.sh"
fi

bash "$PROBE_DIR/local_cpu_probe.sh"
bash "$PROBE_DIR/network_probe.sh"

SUMMARIZE_ARGS=(--run-id "$RUN_ID" --repo "$REPO")
if [[ "$INCLUDE_TRIAL" == "1" ]]; then
  SUMMARIZE_ARGS+=(--include-trial-appendix)
fi
python3 "$PROBE_DIR/summarize_latency.py" "${SUMMARIZE_ARGS[@]}"

echo "latency_probe run_all complete RUN_ID=$RUN_ID"
echo "Report: $LATENCY_REPORT_ROOT/latency_report.txt"
