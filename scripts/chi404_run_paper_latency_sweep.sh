#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
#
# Real Rithmic paper placement-speed baselines must use the native C++
# rithmic_latency_probe target. This script intentionally refuses to start the
# old Python/ctypes paper-latency daemon because that path is not a hot-path
# authority.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
RUN_ID="${RITHMIC_PROBE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"

cat >&2 <<EOF
FAIL: PAPER_LATENCY_SWEEP_REPLACED_BY_NATIVE_CPP_PROBE

Python/ctypes paper latency sweeps are not authoritative hot-path evidence.
Build and run the native C++ probe instead:

  cd "$REPO"
  cmake --build build --target rithmic_latency_probe --config Release
  RITHMIC_PROBE_RUN_ID="$RUN_ID" \\
  RITHMIC_ENDPOINT_PROFILE=paper_chicago \\
  RITHMIC_PROBE_SYMBOL="\${RITHMIC_PROBE_SYMBOL:-ESM6}" \\
  RITHMIC_PROBE_EXCHANGE="\${RITHMIC_PROBE_EXCHANGE:-CME}" \\
  RITHMIC_PROBE_ORDER_COUNT="\${RITHMIC_PROBE_ORDER_COUNT:-30}" \\
  RITHMIC_PROBE_SKIP_MD=0 \\
  RITHMIC_PROBE_CPU="\${RITHMIC_PROBE_CPU:-1}" \\
  RITHMIC_PROBE_RT_PRIORITY="\${RITHMIC_PROBE_RT_PRIORITY:-0}" \\
  RITHMIC_PROBE_MLOCK=1 \\
  RITHMIC_PROBE_PREFAULT_BYTES="\${RITHMIC_PROBE_PREFAULT_BYTES:-16777216}" \\
  ./build/rithmic_gateway/rithmic_latency_probe

Authority: hot_path_language=c++, wrapper=none.
EOF

exit 2
