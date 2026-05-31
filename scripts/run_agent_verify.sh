#!/usr/bin/env bash
# Bounded agent verification: T0 + registry + workbench (excludes slow CPI e2e).
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
exec bash tools/shell/run_with_timeout.sh 180 agent-verify -- \
  python -m pytest \
    tests/backtester_validation/fast \
    tests/test_model_registry_slugs.py \
    tests/test_workbench/ \
    --ignore=tests/test_workbench/test_cpi_e2e.py \
    -q --tb=no
