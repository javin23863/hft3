#!/usr/bin/env bash
# Dev/CI deps for VectorBT→HftBacktest handoff verify (submodules + Python packages).
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
git submodule update --init vendor/openfoundry vendor/alphageometry
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m pip install --upgrade \
  "jsonschema>=4.20.0" \
  "networkx>=3.2" \
  "pyyaml>=6.0" \
  "pandas>=2.0.0" \
  "pyarrow>=14.0.0" \
  "pdfplumber>=0.11.0" \
  "python-docx>=1.1.0" \
  "beautifulsoup4>=4.12.0" \
  "requests>=2.31.0" \
  pydantic scipy pytest
bash scripts/install_hftbacktest_realism_deps.sh
