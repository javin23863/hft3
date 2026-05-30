#!/usr/bin/env bash
# Blocking graph consult gate (CHI404 / Linux). See graphify_gate.ps1.
set -euo pipefail

QUERY="${1:-}"
PURPOSE="${2:-code-edit}"
REPO="${HFT3_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
cd "$REPO"

if [[ -z "$QUERY" ]]; then
  echo "usage: graphify_gate.sh '<graphify query>' [purpose]" >&2
  exit 1
fi

if ! command -v graphify >/dev/null 2>&1; then
  echo "graphify not on PATH; see docs/GRAPHIFY_WORKFLOW.md" >&2
  exit 1
fi

if [[ ! -f graphify-out/graph.json ]]; then
  echo "graphify-out/graph.json missing — running graphify update ."
  graphify update .
fi

STAMP="graphify-out/.last-graph-query.json"
TMP_OUT="$(mktemp)"
trap 'rm -f "$TMP_OUT"' EXIT

echo "GRAPH GATE query: $QUERY"
if ! graphify query "$QUERY" | tee "$TMP_OUT"; then
  echo "graphify query failed" >&2
  exit 1
fi

OUT_LEN=$(wc -c < "$TMP_OUT" | tr -d ' ')
if [[ "$OUT_LEN" -lt 40 ]]; then
  echo "graphify query output too short ($OUT_LEN bytes)" >&2
  exit 1
fi

python3 - "$QUERY" "$PURPOSE" "$STAMP" "$TMP_OUT" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

query, purpose, stamp, out_path = sys.argv[1:5]
out = Path(out_path).read_text(encoding="utf-8", errors="replace")
payload = {
    "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    "purpose": purpose,
    "query": query,
    "output_excerpt": out[:4000],
}
Path(stamp).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
print(f"Wrote {stamp}")
PY

echo "OK: graph consult recorded."
