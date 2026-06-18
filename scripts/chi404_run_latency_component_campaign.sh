#!/usr/bin/env bash
# CHI404 HftBacktest three-component latency campaigns (CC-2 .. CC-6).
# Requires probe v3 (feed/exchange/ack timestamps + intp samples).
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"

set -a
# shellcheck disable=SC1091
source "${HFT3_ENV_FILE:-/root/hft3/.env}"
set +a

# Match chi404_run_live_latency_sweep.sh: live .env profile must use live yaml, not probe default (test yaml).
if [[ "${RITHMIC_ENDPOINT_PROFILE:-}" == paper* ]]; then
  export RITHMIC_CONFIG_PATH="${RITHMIC_CONFIG_PATH:-$REPO/packages/data_system/config/rithmic_api_paper.yaml}"
  export RITHMIC_API_CONFIG="${RITHMIC_API_CONFIG:-packages/data_system/config/rithmic_api_paper.yaml}"
  export RITHMIC_PROBE_ENV_LABEL="${RITHMIC_PROBE_ENV_LABEL:-paper_chicago}"
else
  export RITHMIC_CONFIG_PATH="${RITHMIC_CONFIG_PATH:-$REPO/packages/data_system/config/rithmic_api_live.yaml}"
  export RITHMIC_API_CONFIG="${RITHMIC_API_CONFIG:-packages/data_system/config/rithmic_api_live.yaml}"
  export RITHMIC_ENDPOINT_PROFILE="${RITHMIC_ENDPOINT_PROFILE:-live_chicago_r01}"
  export RITHMIC_PROBE_ENV_LABEL="${RITHMIC_PROBE_ENV_LABEL:-live_r01_chicago}"
fi
export RITHMIC_PROBE_SYMBOL="${RITHMIC_PROBE_SYMBOL:-MESU6}"
export RITHMIC_PROBE_EXCHANGE="${RITHMIC_PROBE_EXCHANGE:-CME}"
export RITHMIC_PROBE_ORDER_PRICE="${RITHMIC_PROBE_ORDER_PRICE:-7000.0}"

PROBE="${PROBE:-./build/rithmic_gateway/rithmic_latency_probe}"
CAMPAIGN="${1:-all}"
RUN_SUFFIX="$(date -u +%Y%m%dT%H%M%SZ)"

common_env() {
  export RITHMIC_PROBE_MLOCK=1
  export RITHMIC_PROBE_PREFAULT_BYTES=16777216
  export RITHMIC_PROBE_CPU=-1
  export RITHMIC_PROBE_RT_PRIORITY=0
  export RITHMIC_PROBE_REQUIRE_MD=1
  export RITHMIC_PROBE_SKIP_MD=0
  export RITHMIC_PROBE_CANCEL_AFTER_ACK=1
  export RITHMIC_PROBE_CALIB_MD_SAMPLES=200
}

run_cc2_feed() {
  common_env
  export RITHMIC_PROBE_RUN_ID="cc2_feed_${RUN_SUFFIX}"
  export RITHMIC_PROBE_ORDER_COUNT=0
  export RITHMIC_PROBE_CALIB_MD_SAMPLES=1000
  export RITHMIC_PROBE_MD_TIMEOUT_MS=600000
  export RITHMIC_PROBE_MD_SMOKE_TIMEOUT_MS=600000
  echo "=== CC-2 feed latency (MD-only calibration) ==="
  "$PROBE"
}

run_cc3_new_decomp() {
  common_env
  export RITHMIC_PROBE_RUN_ID="cc3_new_decomp_${RUN_SUFFIX}"
  export RITHMIC_PROBE_ORDER_COUNT=200
  export RITHMIC_PROBE_ORDER_INTERVAL_US=2000000
  export RITHMIC_PROBE_CANCEL_AFTER_ACK=0
  echo "=== CC-3 new order entry/response decomposition ==="
  "$PROBE"
}

run_cc4_cancel() {
  common_env
  export RITHMIC_PROBE_RUN_ID="cc4_cancel_${RUN_SUFFIX}"
  export RITHMIC_PROBE_ORDER_COUNT=200
  export RITHMIC_PROBE_ORDER_INTERVAL_US=2000000
  export RITHMIC_PROBE_CANCEL_AFTER_ACK=1
  export RITHMIC_PROBE_CANCEL_ACK_TIMEOUT_MS=30000
  export RITHMIC_PROBE_PASSIVE_PRICE_OFFSET=0.25
  echo "=== CC-4 cancel effectiveness (near-market) ==="
  "$PROBE"
}

run_cc5_fill() {
  echo "=== CC-5 fill path (opportunistic during CC-3/4) ==="
  echo "Fill metrics captured when F events arrive during CC-3/4 runs."
}

run_cc6_reject() {
  common_env
  export RITHMIC_PROBE_RUN_ID="cc6_reject_${RUN_SUFFIX}"
  export RITHMIC_PROBE_ORDER_COUNT=10
  export RITHMIC_PROBE_ORDER_QTY=99999
  export RITHMIC_PROBE_CANCEL_AFTER_ACK=0
  echo "=== CC-6 reject/throttle stress ==="
  "$PROBE" || true
}

case "$CAMPAIGN" in
  cc2|CC-2) run_cc2_feed ;;
  cc3|CC-3) run_cc3_new_decomp ;;
  cc4|CC-4) run_cc4_cancel ;;
  cc5|CC-5) run_cc5_fill ;;
  cc6|CC-6) run_cc6_reject ;;
  all)
    run_cc2_feed
    run_cc3_new_decomp
    run_cc4_cancel
    run_cc5_fill
    run_cc6_reject
    python3 scripts/latency_probe/ingest_latency_truth_components.py --repo "$REPO"
    python3 scripts/latency_probe/generate_latency_regimes.py --repo "$REPO"
    ;;
  *) echo "Usage: $0 [cc2|cc3|cc4|cc5|cc6|all]" >&2; exit 2 ;;
esac

echo "Done. Ingest with: python3 scripts/latency_probe/ingest_latency_truth_components.py --repo $REPO"
