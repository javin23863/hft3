#!/usr/bin/env bash
# Remote pre-launch contract for scripts/vast_deploy_and_verify.ps1.
set -euo pipefail

repo="${DEPLOY_REPO:-}"
events="${DEPLOY_EVENTS:-}"
manifest="${DEPLOY_MANIFEST:-}"
expected_head="${DEPLOY_HEAD:-}"
npz_root="${DEPLOY_NPZ_ROOT:-/data/npz}"
probe_n="${DEPLOY_PROBE_N:-20}"

die() {
  echo "REMOTE_VERIFY_FAIL: $*" >&2
  exit 1
}

[[ -n "$repo" ]] || die "DEPLOY_REPO missing"
[[ -d "$repo/.git" ]] || die "repo missing or not a git checkout: $repo"
cd "$repo"

head="$(git rev-parse HEAD)"
if [[ -n "$expected_head" && "$head" != "$expected_head" ]]; then
  die "HEAD $head != expected $expected_head"
fi

[[ -f "$events" ]] || die "events.csv missing: $events"
[[ -f "$manifest" ]] || die "manifest.parquet missing: $manifest"
[[ -d "$npz_root" ]] || die "NPZ root missing: $npz_root"
export HFT3_NPZ_ROOT="$npz_root"
[[ -f runtime/reports/paid_screen_ready_gate.json ]] || die "ready gate missing"

python - "$events" "$manifest" "$npz_root" "$probe_n" <<'PY'
import hashlib
import json
import subprocess
import sys
from pathlib import Path

events = Path(sys.argv[1])
manifest = Path(sys.argv[2])
npz_root = Path(sys.argv[3])
probe_n = int(sys.argv[4])
gate_path = Path("runtime/reports/paid_screen_ready_gate.json")

gate = json.loads(gate_path.read_text(encoding="utf-8"))
if not gate.get("ready_for_full_run"):
    raise SystemExit("REMOTE_VERIFY_FAIL: gate ready_for_full_run is not true")

events_hash = hashlib.sha256(events.read_bytes()).hexdigest()[:32]
manifest_hash = hashlib.sha256(manifest.read_bytes()).hexdigest()[:32]
pilot_hashes = gate.get("pilot_hashes") or {}
if events_hash != str(pilot_hashes.get("events_csv_hash") or ""):
    raise SystemExit(
        f"REMOTE_VERIFY_FAIL: events hash {events_hash} != gate {pilot_hashes.get('events_csv_hash')}"
    )
if manifest_hash != str(pilot_hashes.get("lake_manifest_hash") or ""):
    raise SystemExit(
        f"REMOTE_VERIFY_FAIL: manifest hash {manifest_hash} != gate {pilot_hashes.get('lake_manifest_hash')}"
    )

npz_count = sum(1 for _ in npz_root.glob("*.npz"))
if npz_count <= 0:
    raise SystemExit(f"REMOTE_VERIFY_FAIL: no npz files under {npz_root}")

probe = Path("runtime/reports/vast_npz_probe_units.jsonl")
cmd = [
    sys.executable,
    "scripts/generate_vbt_paid_units_jsonl.py",
    "--out",
    str(probe),
    "--smoke-count",
    str(probe_n),
    "--symbols",
    "MES.v.0,ES.v.0",
    "--event-types",
    "CPI,NFP",
    "--model-id",
    "HYP_5",
    "--events-csv",
    str(events),
    "--require-runnable-npz",
]
proc = subprocess.run(cmd, text=True, capture_output=True, check=False)
if proc.returncode != 0:
    raise SystemExit(
        "REMOTE_VERIFY_FAIL: runnable NPZ probe generation failed\n"
        + (proc.stdout or "")
        + (proc.stderr or "")
    )
lines = [line for line in probe.read_text(encoding="utf-8").splitlines() if line.strip()]
if not lines:
    raise SystemExit("REMOTE_VERIFY_FAIL: runnable NPZ probe produced zero units")

print(f"REMOTE_VERIFY_PASS head={subprocess.check_output(['git','rev-parse','HEAD'], text=True).strip()}")
print(f"events_hash={events_hash} lake_manifest_hash={manifest_hash} npz_count={npz_count} probe_units={len(lines)}")
PY
