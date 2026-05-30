#!/usr/bin/env bash
# Phase 7 — L3 cross-asset event replay (MBO tensor + HftBacktest latency bands)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
EVENT_ID="${1:-CPI_2024_09_11_TIGHT}"
SYMBOL="${2:-MES.v.0}"
python -c "
from pathlib import Path
from hfc3.replay.multi_asset_replay import run_l3_cross_asset_event_replay
import json
repo = Path('.')
out = run_l3_cross_asset_event_replay(repo, '${EVENT_ID}', execution_symbol='${SYMBOL}')
print(json.dumps(out, indent=2))
"
