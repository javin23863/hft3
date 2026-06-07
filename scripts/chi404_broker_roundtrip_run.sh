#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
# Synthetic broker round-trip row generation is not allowed for latency evidence.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
RUN_ID="${RITHMIC_PROBE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

cat >&2 <<EOF
FAIL: SYNTHETIC_BROKER_ROUNDTRIP_REMOVED

This script used to write synthetic order_submit/order_ack rows. That is not
allowed as latency evidence. Use the native C++ Rithmic probe:

  cd "$REPO"
  cmake --build build --target rithmic_latency_probe --config Release
  RITHMIC_PROBE_RUN_ID="$RUN_ID" \\
  RITHMIC_ENDPOINT_PROFILE=external_chicago \\
  RITHMIC_PROBE_SYMBOL="\${RITHMIC_PROBE_SYMBOL:-ESM6}" \\
  RITHMIC_PROBE_EXCHANGE="\${RITHMIC_PROBE_EXCHANGE:-CME}" \\
  ./build/rithmic_gateway/rithmic_latency_probe

Authority: hot_path_language=c++, wrapper=none.
EOF

exit 2
