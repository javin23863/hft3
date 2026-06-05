#!/usr/bin/env bash
# Phase 8 — cross-asset MBO ablation (does not assume cross-asset helps)
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
if [[ $# -lt 1 ]]; then
  echo "usage: $0 EVENT_ID" >&2
  exit 2
fi
EVENT_ID="$1"
python -c "
from pathlib import Path
from hfc3.ablation.run_ablation import write_ablation_report
md, js = write_ablation_report(Path('.'), ['${EVENT_ID}'])
print('Wrote', md)
print('Wrote', js)
"
