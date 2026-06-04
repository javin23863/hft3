#!/usr/bin/env bash
# Deprecated compatibility entrypoint.
# CHI404 no longer uses R|Trader/Wine as a trade or latency path.
set -euo pipefail

cat >&2 <<'EOF'
FAIL: RTRADER_SETUP_REMOVED_FOR_CHI404

Use the R|API+ native C++ path:

  cmake --build build --target rithmic_latency_probe --config Release
  ./build/rithmic_gateway/rithmic_latency_probe

Set runtime secrets in /root/hft3/.env only. Do not use R|Trader/Wine for
hot-path placement-speed evidence.
EOF

exit 2
