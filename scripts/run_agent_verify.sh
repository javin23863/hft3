#!/usr/bin/env bash
# Bounded agent verification: T0 + registry + workbench (excludes slow catalog-event e2e).
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
exec bash tools/shell/run_with_timeout.sh 180 agent-verify -- \
  python -m pytest \
    tests/backtester_validation/fast \
    tests/test_model_registry_slugs.py \
    tests/test_workbench/ \
    --ignore=tests/test_workbench/test_catalog_event_e2e.py \
    -q --tb=no

if [[ -n "${HANDOFF_STATUS_FILE:-}" && -f "${HANDOFF_STATUS_FILE}" ]]; then
  bash tools/shell/run_with_timeout.sh 30 handoff-status -- \
    python scripts/check_handoff_status.py "${HANDOFF_STATUS_FILE}" --require
fi
