#!/usr/bin/env bash
# Bounded HBT0–HBT5 realism verify (install pinned hftbacktest + full pytest slice).
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
bash scripts/install_hftbacktest_realism_deps.sh
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/packages${PYTHONPATH:+:$PYTHONPATH}"
exec python3 -B -m pytest -q \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt1.py \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt2.py \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt3.py \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt4.py \
  tests/backtest_pipeline/test_hftbacktest_realism_hbt5.py \
  tests/backtest_pipeline/test_hftbacktest_vendor_lock.py \
  -p no:cacheprovider
