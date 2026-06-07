#!/usr/bin/env bash
# CHI404 native C++ broker placement-speed sweep.
set -euo pipefail

HOST_ID="${HFT3_DEPLOY_HOST:-${HOSTNAME:-$(hostname 2>/dev/null || true)}}"
if [[ "${HOST_ID,,}" != "chi404" ]]; then
  echo "FAIL: CHI404_REQUIRED broker latency sweep must run on CHI404 only" >&2
  exit 2
fi

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"

cmake --build build --target rithmic_latency_probe --config Release

export RITHMIC_ENDPOINT_PROFILE="${RITHMIC_ENDPOINT_PROFILE:-external_chicago}"
export RITHMIC_CONFIG_PATH="${RITHMIC_CONFIG_PATH:-$REPO/packages/data_system/config/rithmic_api_external.yaml}"
export RITHMIC_PROBE_ENV_LABEL="${RITHMIC_PROBE_ENV_LABEL:-external}"
export RITHMIC_PROBE_RUN_ID="${RITHMIC_PROBE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
export RITHMIC_PROBE_SYMBOL="${RITHMIC_PROBE_SYMBOL:-ESM6}"
export RITHMIC_PROBE_EXCHANGE="${RITHMIC_PROBE_EXCHANGE:-CME}"
export RITHMIC_PROBE_ORDER_COUNT="${RITHMIC_PROBE_ORDER_COUNT:-30}"
export RITHMIC_PROBE_CANCEL_AFTER_ACK="${RITHMIC_PROBE_CANCEL_AFTER_ACK:-1}"
export RITHMIC_PROBE_SKIP_MD="${RITHMIC_PROBE_SKIP_MD:-0}"
export RITHMIC_PROBE_CPU="${RITHMIC_PROBE_CPU:--1}"
export RITHMIC_PROBE_RT_PRIORITY="${RITHMIC_PROBE_RT_PRIORITY:-0}"
export RITHMIC_PROBE_MLOCK="${RITHMIC_PROBE_MLOCK:-1}"
export RITHMIC_PROBE_PREFAULT_BYTES="${RITHMIC_PROBE_PREFAULT_BYTES:-16777216}"

echo "Authority: hot_path_language=c++, wrapper=none"
./build/rithmic_gateway/rithmic_latency_probe
