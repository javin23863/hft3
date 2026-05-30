#!/usr/bin/env bash
# Phase 8 — cross-asset MBO ablation (does not assume cross-asset helps)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
EVENT_ID="${1:-CPI_2024_09_11_TIGHT}"
python -c "
from pathlib import Path
from hfc3.ablation.run_ablation import write_ablation_report
md, js = write_ablation_report(Path('.'), ['${EVENT_ID}'])
print('Wrote', md)
print('Wrote', js)
"
