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
  "pandas>=2.0.0,<3.0.0" \
  "pyarrow>=14.0.0" \
  "pdfplumber>=0.11.0" \
  "python-docx>=1.1.0" \
  "beautifulsoup4>=4.12.0" \
  "requests>=2.31.0" \
  pydantic scipy pytest
bash scripts/install_hftbacktest_realism_deps.sh

if "$PYTHON" -m pip install --quiet --upgrade "pybind11>=2.10" 2>/dev/null \
   && PYBIND_DIR="$("$PYTHON" -m pybind11 --cmakedir 2>/dev/null)" && [ -n "$PYBIND_DIR" ]; then
  if cmake -B "$REPO_ROOT/build" -S "$REPO_ROOT" -DCMAKE_BUILD_TYPE=Release \
          -DPython3_EXECUTABLE="$(command -v "$PYTHON")" -Dpybind11_DIR="$PYBIND_DIR" -Wno-dev \
          && cmake --build "$REPO_ROOT/build" --target hft3_features_cpp; then
    echo "hft3_features_cpp built under $REPO_ROOT/build"
  else
    echo "WARNING: hft3_features_cpp build failed; workers will use python extractor fallback" >&2
  fi
else
  echo "WARNING: pybind11 unavailable; hft3_features_cpp not built (python fallback)" >&2
fi
