#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
START=$(date +%s)
OUT=$(mktemp)
set +e
python -m pytest tests/backtester_validation/fast -q --tb=short 2>&1 | tee "$OUT"
RC=${PIPESTATUS[0]}
set -e
END=$(date +%s)
DURATION=$((END - START))
python - <<PY
from hft3.validation.fast_gate_report import write_fast_gate_report
import pathlib
text = pathlib.Path("$OUT").read_text(encoding="utf-8", errors="replace")
passed = $RC == 0
count = 0
failed = 0
for line in text.splitlines():
    if " passed" in line or " failed" in line:
        parts = line.split()
        for i, p in enumerate(parts):
            if p == "passed" and i > 0:
                try:
                    count = int(parts[i - 1])
                except ValueError:
                    pass
            if p == "failed" and i > 0:
                try:
                    failed = int(parts[i - 1])
                except ValueError:
                    pass
write_fast_gate_report(
    passed=passed,
    duration_sec=float($DURATION),
    test_count=count,
    failed_count=failed,
    pytest_output_tail=text,
)
PY
rm -f "$OUT"
exit "$RC"
