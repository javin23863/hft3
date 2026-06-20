#!/usr/bin/env bash
# Fail-closed remote verifier for Vast paid-screen deploys.
set -euo pipefail

fail() {
  echo "REMOTE_VERIFY_FAIL: $*" >&2
  exit 1
}

need_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    fail "$name unset"
  fi
}

need_env DEPLOY_REPO
need_env DEPLOY_EVENTS
need_env DEPLOY_MANIFEST
need_env DEPLOY_HEAD
need_env DEPLOY_NPZ_ROOT

if [[ "${DEPLOY_MANIFEST##*/}" != "manifest.parquet" ]]; then
  fail "DEPLOY_MANIFEST must point to manifest.parquet, got $DEPLOY_MANIFEST"
fi
if [[ ! -d "$DEPLOY_REPO/.git" ]]; then
  fail "DEPLOY_REPO is not a git checkout: $DEPLOY_REPO"
fi

cd "$DEPLOY_REPO"
REMOTE_HEAD="$(git rev-parse HEAD)"
if [[ "$REMOTE_HEAD" != "$DEPLOY_HEAD" ]]; then
  fail "remote HEAD $REMOTE_HEAD != expected $DEPLOY_HEAD"
fi

GATE_FILE="$DEPLOY_REPO/runtime/reports/paid_screen_ready_gate.json"
DECL_FILE="$DEPLOY_REPO/runtime/reports/vbt_full_run_declaration.json"
[[ -f "$GATE_FILE" ]] || fail "ready gate missing: $GATE_FILE"
[[ -f "$DECL_FILE" ]] || fail "full-run declaration missing: $DECL_FILE"
[[ -f "$DEPLOY_EVENTS" ]] || fail "events CSV missing: $DEPLOY_EVENTS"
[[ -f "$DEPLOY_MANIFEST" ]] || fail "manifest.parquet missing: $DEPLOY_MANIFEST"
[[ -d "$DEPLOY_NPZ_ROOT" ]] || fail "NPZ root missing: $DEPLOY_NPZ_ROOT"

python3 - "$GATE_FILE" "$DECL_FILE" "$DEPLOY_EVENTS" "$DEPLOY_MANIFEST" "$DEPLOY_HEAD" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

gate_path, decl_path, events_path, manifest_path = map(Path, sys.argv[1:5])
deploy_head = sys.argv[5]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


gate = json.loads(gate_path.read_text(encoding="utf-8"))
decl = json.loads(decl_path.read_text(encoding="utf-8"))
if not gate.get("ready_for_full_run"):
    raise SystemExit("ready gate is not ready_for_full_run")
if str(decl.get("git_head") or "").strip() != str(deploy_head):
    raise SystemExit("declaration git_head does not match deployed head")

pilot_hashes = gate.get("pilot_hashes") or {}
actual_events = digest(events_path)
actual_lake = digest(manifest_path)
checks = (
    ("events_csv_hash", actual_events),
    ("lake_manifest_hash", actual_lake),
)
for key, actual in checks:
    gate_expected = str(pilot_hashes.get(key) or "").strip()
    decl_expected = str(decl.get(key) or "").strip()
    if not gate_expected:
        raise SystemExit(f"gate {key} missing")
    if not decl_expected:
        raise SystemExit(f"declaration {key} missing")
    if actual != gate_expected:
        raise SystemExit(f"{key} actual={actual} != gate={gate_expected}")
    if actual != decl_expected:
        raise SystemExit(f"{key} actual={actual} != declaration={decl_expected}")

print(f"remote_events_hash={actual_events}")
print(f"remote_lake_manifest_hash={actual_lake}")
PY

PROBE_N="${DEPLOY_PROBE_N:-20}"
if ! [[ "$PROBE_N" =~ ^[0-9]+$ ]]; then
  fail "DEPLOY_PROBE_N must be an integer, got $PROBE_N"
fi
if [[ "$PROBE_N" -lt 1 ]]; then
  PROBE_N=1
fi

mapfile -t PROBE_FILES < <(find "$DEPLOY_NPZ_ROOT" -maxdepth 1 -type f -name '*.npz' -print | head -n "$PROBE_N")
if [[ "${#PROBE_FILES[@]}" -lt 1 ]]; then
  fail "no .npz files found in $DEPLOY_NPZ_ROOT"
fi
for npz in "${PROBE_FILES[@]}"; do
  [[ -s "$npz" ]] || fail "empty NPZ probe file: $npz"
done

echo "remote_head=$REMOTE_HEAD"
echo "remote_npz_probe_count=${#PROBE_FILES[@]}"
echo "REMOTE_VERIFY_PASS"
