#!/usr/bin/env bash
# Install pinned official hftbacktest for HBT realism tests and source-lock verification.
# Policy: docs/ai/SHELL_EXECUTION.md
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"
LOCK="$REPO_ROOT/vendor/hftbacktest/VENDOR.lock"
if [[ ! -f "$LOCK" ]]; then
  echo "missing vendor lock: $LOCK" >&2
  exit 1
fi
VERSION="$(grep -E '^python_package_version=' "$LOCK" | head -1 | cut -d= -f2- | tr -d '[:space:]')"
if [[ -z "$VERSION" ]]; then
  echo "python_package_version missing in $LOCK" >&2
  exit 1
fi
PYTHON="${PYTHON:-python3}"
"$PYTHON" -m pip install --upgrade "hftbacktest==${VERSION}"
"$PYTHON" -c "
import importlib.metadata as metadata
import importlib.util
from pathlib import Path

lock_path = Path('${REPO_ROOT}/vendor/hftbacktest/VENDOR.lock')
expected = None
for raw_line in lock_path.read_text(encoding='utf-8').splitlines():
    line = raw_line.strip()
    if line.startswith('python_package_version='):
        expected = line.split('=', 1)[1].strip()
        break
if not expected:
    raise SystemExit('python_package_version missing in vendor lock')
installed = metadata.version('hftbacktest')
spec = importlib.util.find_spec('hftbacktest')
if spec is None or spec.origin is None:
    raise SystemExit('hftbacktest not importable after install')
if installed != expected:
    raise SystemExit(f'version mismatch: installed={installed!r} expected={expected!r}')
upstream_tag = None
for raw_line in lock_path.read_text(encoding='utf-8').splitlines():
    line = raw_line.strip()
    if line.startswith('upstream_commit_sha_or_tag='):
        upstream_tag = line.split('=', 1)[1].strip()
        break
print(f'hftbacktest {installed} installed (upstream {upstream_tag})')
"
