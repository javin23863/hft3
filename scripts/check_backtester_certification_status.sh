#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
python - <<'PY'
from hft3.validation.certification_registry import load_registry
from hft3.validation.certification_staleness import assess_staleness
from hft3.validation.certification_registry import repo_root

root = repo_root()
reg = load_registry(root)
stale = assess_staleness(root, registry=reg)
print(f"Certification status: {reg.latest_certification_status}")
print(f"Certification commit: {reg.latest_certification_commit}")
print(f"Current commit: {stale.current_commit}")
print(f"Certification current: {stale.certification_is_current}")
if stale.stale_reason:
    print(f"Staleness reason: {stale.stale_reason}")
if stale.changed_core_files:
    print("Changed core files:")
    for f in stale.changed_core_files:
        print(f"  - {f}")
ok = reg.latest_certification_status == "GREEN" and stale.certification_is_current
raise SystemExit(0 if ok else 1)
PY
