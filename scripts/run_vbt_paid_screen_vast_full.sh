#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast (v2 long-lived workers in tmux).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md, PAID_SCREEN_OPS_COMMANDS.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
# Units default to Stage-A survivors; all-active is an explicit override only.
# v2 env knobs: VBT_BATCH_TIMEOUT_SECONDS, VBT_CACHE_MEMORY_LIMIT_MB, VBT_CACHE_MAX_ENTRIES, VBT_MAX_BATCHES_BEFORE_RECYCLE, VBT_RESUME=1
# v2 provenance: passes --events-csv + derived --events-csv-hash; lake hash from HFT3_MANIFEST_PATH
# (sha256 file content) or declaration lake_manifest_hash — fail-closed before v2 launch if unavailable.
# tmux wrapper: survives SSH disconnect — the run keeps going after you log out.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_ROOT"

# Load owner env if present (HFT3_NPZ_ROOT, HFT3_MANIFEST_PATH, …)
if [[ -f "${HFT3_ENV_FILE:-/root/hft3/.env}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${HFT3_ENV_FILE:-/root/hft3/.env}"
  set +a
fi

# Gate-aligned lake manifest (parquet hash); override only when owner sets explicitly.
export HFT3_MANIFEST_PATH="${HFT3_MANIFEST_PATH:-/data/npz/manifest.parquet}"

EVENTS_CSV="${VBT_EVENTS_CSV:-packages/data_system/config/events.csv}"
UNITS_JSONL="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
if [[ -z "${VBT_READY_GATE_FILE:-}" && -f "runtime/reports/paid_screen_ready_gate_after_forensic_probe.json" ]]; then
  GATE_FILE="runtime/reports/paid_screen_ready_gate_after_forensic_probe.json"
elif [[ -n "${VBT_READY_GATE_FILE:-}" ]]; then
  GATE_FILE="$VBT_READY_GATE_FILE"
else
  GATE_FILE="runtime/reports/paid_screen_ready_gate.json"
fi
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"
MODEL_SCOPE="${VBT_MODEL_SCOPE:-active}"
MODEL_IDS="${VBT_MODEL_IDS:-}"
EVENT_TYPES="${VBT_EVENT_TYPES:-}"
RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"
UNIT_SOURCE="${VBT_UNIT_SOURCE:-stage_a_survivors}"
STAGE_A_SURVIVORS="${VBT_STAGE_A_SURVIVORS:-research_cards/stage_a_full/stage_a_survivors.json}"
DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"
TMUX_SESSION="${VBT_TMUX_SESSION:-vbt_full}"
STALL_MINUTES="${VBT_STALL_MINUTES:-30}"
BATCH_TIMEOUT_SECONDS="${VBT_BATCH_TIMEOUT_SECONDS:-1800}"
if [[ ! "$BATCH_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] || (( BATCH_TIMEOUT_SECONDS < 1 )); then
  echo "ERROR: VBT_BATCH_TIMEOUT_SECONDS must be a positive integer (got '$BATCH_TIMEOUT_SECONDS')" >&2
  exit 1
fi

NPROC="$(nproc)"
if [[ -n "${VBT_WORKERS:-}" ]]; then
  WORKERS="$VBT_WORKERS"
elif [[ "$NPROC" -ge 256 ]]; then
  WORKERS=230
else
  WORKERS=$((NPROC - 26))
  if [[ "$WORKERS" -lt 1 ]]; then WORKERS=1; fi
fi

if [[ ! -f "$GATE_FILE" ]]; then
  echo "ERROR: Ready gate missing: $GATE_FILE (run validate_paid_screen_ready_gate.py first)" >&2
  exit 1
fi

if [[ -z "${HFT3_NPZ_ROOT:-}" ]]; then
  echo "ERROR: HFT3_NPZ_ROOT unset — NPZ lake must be on this host (Vast data from prior sync)." >&2
  exit 1
fi

echo "=== Vast VectorBT paid screen ==="
echo "repo=$REPO_ROOT nproc=$NPROC workers=$WORKERS npz_root=$HFT3_NPZ_ROOT"
echo "events_csv=$EVENTS_CSV symbols=$SYMBOLS unit_source=$UNIT_SOURCE model_scope=$MODEL_SCOPE units_out=$UNITS_JSONL"

bash scripts/install_vbt_hbt_handoff_verify_deps.sh
pip3 install 'vectorbt[rust]==1.0.0' -q

# --- Unit generation (Stage-A survivors by default + require-runnable-npz) ---
GEN_ARGS=(
  python3 scripts/generate_vbt_paid_units_jsonl.py
  --events-csv "$EVENTS_CSV"
  --symbols "$SYMBOLS"
  --out "$UNITS_JSONL"
)

case "$UNIT_SOURCE" in
  stage_a_survivors)
    if [[ ! -f "$STAGE_A_SURVIVORS" ]]; then
      echo "ERROR: Stage-A survivors missing: $STAGE_A_SURVIVORS" >&2
      echo "Current Vast full scope is survivor-scoped; set VBT_STAGE_A_SURVIVORS or VBT_UNIT_SOURCE=all_active explicitly." >&2
      exit 1
    fi
    GEN_ARGS+=(--from-stage-a-survivors "$STAGE_A_SURVIVORS")
    UNITS_SOURCE_DESC="stage_a_survivors_cme_m6_runnable_npz"
    ;;
  all_active)
    if [[ -n "$MODEL_IDS" ]]; then
      GEN_ARGS+=(--model-ids "$MODEL_IDS")
    elif [[ "$MODEL_SCOPE" == "active" ]]; then
      GEN_ARGS+=(--all-active-models)
    else
      GEN_ARGS+=(--model-id "${VBT_MODEL_ID:-SPREAD_BLOWOUT_RECOMPRESSION}")
    fi
    UNITS_SOURCE_DESC="all_active_cme_m6_runnable_npz_override"
    ;;
  *)
    echo "ERROR: unsupported VBT_UNIT_SOURCE=$UNIT_SOURCE (expected stage_a_survivors or all_active)" >&2
    exit 1
    ;;
esac

if [[ -n "$EVENT_TYPES" ]]; then
  GEN_ARGS+=(--event-types "$EVENT_TYPES")
fi

if [[ -n "$RESEARCH_SPLIT" ]]; then
  GEN_ARGS+=(--research-split "$RESEARCH_SPLIT")
fi

if [[ "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "1" || "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "true" ]]; then
  GEN_ARGS+=(--require-runnable-npz)
fi

"${GEN_ARGS[@]}"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Full unit count: $UNIT_COUNT (must match declaration expected_work_units)"

echo "Resolving v2 provenance hashes"
echo "Do not substitute manifest.json or units JSONL."
EVENTS_CSV_HASH="$(python3 -c "import hashlib; print(hashlib.sha256(open('$EVENTS_CSV','rb').read()).hexdigest()[:32])")"
LAKE_MANIFEST_HASH="$(python3 -c "
import hashlib, os, sys
p=os.environ.get('HFT3_MANIFEST_PATH','/data/npz/manifest.parquet')
if not os.path.isfile(p):
    sys.exit(1)
print(hashlib.sha256(open(p,'rb').read()).hexdigest()[:32])
" || true)"
if [[ -z "$LAKE_MANIFEST_HASH" ]]; then
  echo "ERROR: HFT3_MANIFEST_PATH missing or unreadable; cannot verify lake_manifest_hash before workers: $HFT3_MANIFEST_PATH" >&2
  exit 1
fi
GIT_HEAD="$(git rev-parse HEAD 2>/dev/null || echo unknown)"

if [[ "${VBT_WRITE_DECLARATION_TEMPLATE:-0}" == "1" || "${VBT_WRITE_DECLARATION_TEMPLATE:-0}" == "true" ]]; then
  DECL_ABORT_ON_FAILED_UNITS="${VBT_DECL_ABORT_ON_FAILED_UNITS:-true}"
  if [[ "$DECL_ABORT_ON_FAILED_UNITS" == "1" || "${DECL_ABORT_ON_FAILED_UNITS,,}" == "true" || "${DECL_ABORT_ON_FAILED_UNITS,,}" == "yes" || "${DECL_ABORT_ON_FAILED_UNITS,,}" == "on" ]]; then
    DECL_ABORT_ON_FAILED_UNITS="true"
  else
    DECL_ABORT_ON_FAILED_UNITS="false"
  fi
  python3 - "$DECL_FILE" "$UNIT_COUNT" "$NPROC" "$WORKERS" "$UNITS_SOURCE_DESC" "$GIT_HEAD" "$EVENTS_CSV_HASH" "$LAKE_MANIFEST_HASH" "$STALL_MINUTES" "$BATCH_TIMEOUT_SECONDS" "$DECL_ABORT_ON_FAILED_UNITS" <<'PY'
import json
import os
import sys
from pathlib import Path

decl_path, unit_count, host_vcpu, workers, units_source, git_head, events_hash, lake_hash, stall_minutes, batch_timeout_seconds, abort_on_failed_units = sys.argv[1:12]
payload = {
    "host_vcpu": int(host_vcpu),
    "reserved_vcpu": 26,
    "workers_requested": int(workers),
    "expected_work_units": int(unit_count),
    "units_source": units_source,
    "stall_minutes": int(stall_minutes),
    "batch_timeout_seconds": int(batch_timeout_seconds),
    "abort_on_failed_units": str(abort_on_failed_units).lower() in {"1", "true", "yes", "on"},
    "git_head": git_head,
    "events_csv_hash": events_hash,
    "lake_manifest_hash": lake_hash,
}
run_id = os.environ.get("VBT_FULL_RUN_ID")
if run_id:
    payload["run_id"] = run_id
path = Path(decl_path)
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Wrote declaration template: {path}")
print(json.dumps(payload, indent=2, sort_keys=True))
PY
  echo "Review $DECL_FILE, then rerun without VBT_WRITE_DECLARATION_TEMPLATE to launch workers."
  exit 0
fi

# --- Declaration check ---
if [[ ! -f "$DECL_FILE" ]]; then
  echo "ERROR: Full-run declaration missing: $DECL_FILE" >&2
  echo "Run with VBT_WRITE_DECLARATION_TEMPLATE=1 to write the current on-host declaration, review it, then rerun." >&2
  exit 1
fi

DECL_EXPECTED="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["expected_work_units"])
PY
)"

if [[ "$DECL_EXPECTED" != "$UNIT_COUNT" ]]; then
  echo "ERROR: Declaration expected_work_units=$DECL_EXPECTED != generated unit count=$UNIT_COUNT" >&2
  echo "Regenerate or approve the declaration before launching workers." >&2
  exit 1
fi

python3 - "$DECL_FILE" "$UNIT_COUNT" "$NPROC" "$WORKERS" "$UNITS_SOURCE_DESC" "$GIT_HEAD" "$EVENTS_CSV_HASH" "$LAKE_MANIFEST_HASH" "$STALL_MINUTES" "$BATCH_TIMEOUT_SECONDS" <<'PY'
import json
import sys

decl_path, unit_count, host_vcpu, workers, units_source, git_head, events_hash, lake_hash, stall_minutes, batch_timeout_seconds = sys.argv[1:11]
payload = json.load(open(decl_path, encoding="utf-8"))
errors = []

def expect_int(name: str, expected: int) -> None:
    try:
        actual = int(payload.get(name))
    except (TypeError, ValueError):
        errors.append(f"{name}=<missing/non-int> expected {expected}")
        return
    if actual != expected:
        errors.append(f"{name}={actual} expected {expected}")

def expect_str(name: str, expected: str) -> None:
    actual = str(payload.get(name) or "").strip()
    if actual != expected:
        errors.append(f"{name}={actual or '<missing>'} expected {expected}")

expect_int("expected_work_units", int(unit_count))
expect_int("host_vcpu", int(host_vcpu))
expect_int("reserved_vcpu", 26)
expect_int("workers_requested", int(workers))
expect_int("stall_minutes", int(stall_minutes))
expect_int("batch_timeout_seconds", int(batch_timeout_seconds))
expect_str("units_source", units_source)
expect_str("git_head", git_head)
expect_str("events_csv_hash", events_hash)
expect_str("lake_manifest_hash", lake_hash)
if payload.get("abort_on_failed_units") is not True:
    errors.append("abort_on_failed_units must be true; run with VBT_WRITE_DECLARATION_TEMPLATE=1 to regenerate the declaration template")
if errors:
    for err in errors:
        print(f"ERROR: Declaration mismatch: {err}", file=sys.stderr)
    sys.exit(1)
PY
echo "Declaration verified: $DECL_FILE (expected_work_units=$UNIT_COUNT)"
echo "Declaration OK: $DECL_FILE"

# --- Launch v2 runner inside tmux (survives SSH disconnect) ---
if [[ -z "${VBT_FULL_RUN_ID:-}" && -f "$DECL_FILE" ]]; then
  DECL_RUN_ID="$(python3 - "$DECL_FILE" <<'PY'
import json
import sys

payload = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("run_id", "vbt_full_run_id"):
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        print(value.strip())
        break
PY
)"
  if [[ -n "$DECL_RUN_ID" ]]; then
    export VBT_FULL_RUN_ID="$DECL_RUN_ID"
  fi
fi
export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-paid_full_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"
LOG_FILE="${OUT_DIR}/orchestrator.log"
mkdir -p "$OUT_DIR"

# Kill any existing tmux session with the same name
tmux kill-session -t "$TMUX_SESSION" 2>/dev/null || true

echo "Starting v2 runner in tmux session=$TMUX_SESSION run_id=$VBT_FULL_RUN_ID workers=$WORKERS"
echo "Run: tmux attach -t $TMUX_SESSION  (Ctrl-B D to detach)"
# provenance contract: --events-csv "$EVENTS_CSV" --events-csv-hash "$EVENTS_CSV_HASH" --lake-manifest-hash "$LAKE_MANIFEST_HASH"

tmux new-session -d -s "$TMUX_SESSION" bash -lc "
set -o pipefail
cd '$REPO_ROOT'
export HFT3_NPZ_ROOT='$HFT3_NPZ_ROOT'
export HFT3_MANIFEST_PATH='$HFT3_MANIFEST_PATH'
export VBT_FULL_RUN_ID='$VBT_FULL_RUN_ID'
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1
  python3 scripts/run_paid_screen.py \\
  --units-jsonl '$UNITS_JSONL' \\
  --out '$OUT_DIR' \\
  --vectorbt-scope paid-compute \\
  --workers $WORKERS \\
  --ready-gate-file '$GATE_FILE' \\
  --max-wall-clock-seconds \${VBT_MAX_WALL_CLOCK_SECONDS:-86400} \\
  --batch-timeout-seconds $BATCH_TIMEOUT_SECONDS \\
  --max-batches-before-recycle \${VBT_MAX_BATCHES_BEFORE_RECYCLE:-0} \\
  --cache-memory-limit-mb \${VBT_CACHE_MEMORY_LIMIT_MB:-65536} \\
  --cache-max-entries \${VBT_CACHE_MAX_ENTRIES:-20000} \\
  --no-llm \\
  --resume \\
  \${VBT_FAIL_FAST:+--fail-fast --abort-on-failed-units} \\
  \${VBT_SKIP_BAD_UNITS_FILE:+--skip-bad-units-file \"\$VBT_SKIP_BAD_UNITS_FILE\"} \\
  --events-csv '$EVENTS_CSV' \\
  --events-csv-hash '$EVENTS_CSV_HASH' \\
  --lake-manifest-hash '$LAKE_MANIFEST_HASH' \\
  2>&1 | tee -a '$LOG_FILE'
status=\$?
echo 'RUN_FINISHED exit='\$status >> '$LOG_FILE'
exit \$status
"

echo "tmux session '$TMUX_SESSION' started. Attach: tmux attach -t $TMUX_SESSION"
echo "Manifest: ${OUT_DIR}/paid_screen_run_manifest.json"
