#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
set -euo pipefail

cat >&2 <<'EOF'
FAIL: RTRADER_WATCH_DIRS_REMOVED

RTrader watch directories are not part of the CHI404 native C++ hot path.
Use RITHMIC_TRIAL_CONNECTOR=rithmic_api for capture and
rithmic_gateway/tools/rithmic_latency_probe.cpp for placement-speed evidence.
EOF

exit 2
