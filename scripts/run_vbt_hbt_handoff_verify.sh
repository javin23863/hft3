#!/usr/bin/env bash
# Bounded VectorBT→HftBacktest handoff verify (VBT adapter + pipeline + HBT0–HBT5).
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
PYTHON="${PYTHON:-python3}"
PYTHON="$PYTHON" bash scripts/install_vbt_hbt_handoff_verify_deps.sh
export PYTHONPATH="${REPO_ROOT}:${REPO_ROOT}/packages${PYTHONPATH:+:$PYTHONPATH}"
exec bash tools/shell/run_with_timeout.sh 180 vbt-hbt-handoff-verify -- \
  "$PYTHON" -B -m pytest -q \
    tests/backtest_pipeline/test_ontology_gate.py \
    tests/backtest_pipeline/test_feature_plane.py \
    tests/test_vectorbt_adapter.py \
    tests/test_research_pipeline.py \
    tests/test_robustness_producers/test_fee_stress.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt0.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt1.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt2.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt3.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt4.py \
    tests/backtest_pipeline/test_hftbacktest_realism_hbt5.py \
    -p no:cacheprovider
