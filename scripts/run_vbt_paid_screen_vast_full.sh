#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast (v2 long-lived workers).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md, PAID_SCREEN_OPS_COMMANDS.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
# Units are generated on-host from events.csv + active model registry (not local Stage A survivors).
# v2 env knobs: PYTHON, VBT_CACHE_MEMORY_LIMIT_MB, VBT_CACHE_MAX_ENTRIES,
# VBT_MAX_BATCHES_BEFORE_RECYCLE, VBT_MAX_UNITS_PER_BATCH, VBT_BATCH_TIMEOUT_SECONDS,
# VBT_MAX_UNITS, VBT_SAMPLE_MODE=1, VBT_RESUME=1
# v2 provenance: passes --events-csv + derived --events-csv-hash; lake hash from HFT3_MANIFEST_PATH
# (sha256 file content) or declaration lake_manifest_hash — fail-closed before v2 launch if unavailable.
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
GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"
MODEL_SCOPE="${VBT_MODEL_SCOPE:-active}"
MODEL_IDS="${VBT_MODEL_IDS:-}"
EVENT_TYPES="${VBT_EVENT_TYPES:-}"
RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"
MAX_UNITS="${VBT_MAX_UNITS:-}"
START_DATE="${VBT_START_DATE:-}"
END_DATE="${VBT_END_DATE:-}"
PYTHON="${PYTHON:-python3}"
SAMPLE_ENABLED=0
case "${VBT_SAMPLE_MODE:-0}" in
  1|true|TRUE|yes|YES) SAMPLE_ENABLED=1 ;;
esac
if [[ "$SAMPLE_ENABLED" == "1" && -z "${VBT_FULL_RUN_DECLARATION:-}" ]]; then
  DECL_FILE="runtime/reports/vbt_sample_run_declaration.json"
else
  DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"
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
echo "events_csv=$EVENTS_CSV symbols=$SYMBOLS model_scope=$MODEL_SCOPE max_units=${MAX_UNITS:-all} units_out=$UNITS_JSONL"

if [[ "$SAMPLE_ENABLED" == "1" && -z "$MAX_UNITS" ]]; then
  echo "ERROR: VBT_SAMPLE_MODE=1 requires VBT_MAX_UNITS to cap paid compute." >&2
  exit 1
fi

PYTHON="$PYTHON" bash scripts/install_vbt_hbt_handoff_verify_deps.sh

GEN_ARGS=(
  "$PYTHON" scripts/generate_vbt_paid_units_jsonl.py
  --events-csv "$EVENTS_CSV"
  --symbols "$SYMBOLS"
  --out "$UNITS_JSONL"
)

if [[ -n "$MODEL_IDS" ]]; then
  GEN_ARGS+=(--model-ids "$MODEL_IDS")
elif [[ "$MODEL_SCOPE" == "active" ]]; then
  GEN_ARGS+=(--all-active-models)
else
  GEN_ARGS+=(--model-id "${VBT_MODEL_ID:-SPREAD_BLOWOUT_RECOMPRESSION}")
fi

if [[ -n "$EVENT_TYPES" ]]; then
  GEN_ARGS+=(--event-types "$EVENT_TYPES")
fi

GEN_ARGS+=(--research-split "$RESEARCH_SPLIT")
if [[ -n "$MAX_UNITS" ]]; then
  GEN_ARGS+=(--max-units "$MAX_UNITS")
fi
if [[ -n "$START_DATE" ]]; then
  GEN_ARGS+=(--start-date "$START_DATE")
fi
if [[ -n "$END_DATE" ]]; then
  GEN_ARGS+=(--end-date "$END_DATE")
fi

if [[ "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "1" || "${VBT_REQUIRE_RUNNABLE_NPZ:-1}" == "true" ]]; then
  GEN_ARGS+=(--require-runnable-npz)
fi

"${GEN_ARGS[@]}"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Unit count: $UNIT_COUNT (must match declaration expected_work_units)"

if [[ "$SAMPLE_ENABLED" == "1" && -z "${VBT_FULL_RUN_DECLARATION:-}" ]]; then
  mkdir -p "$(dirname "$DECL_FILE")"
  "$PYTHON" - "$DECL_FILE" "$UNIT_COUNT" "$WORKERS" "$UNITS_JSONL" <<'PY'
import json
import os
import subprocess
import sys
from pathlib import Path

decl_file = Path(sys.argv[1])
unit_count = int(sys.argv[2])
workers = int(sys.argv[3])
units_path = sys.argv[4]
decl = {
    "sample_mode": True,
    "workers_requested": workers,
    "expected_work_units": unit_count,
    "units_source": "sample env: events.csv × selected model/symbol/event filters",
    "units_path": units_path,
    "stall_minutes": int(os.environ.get("VBT_STALL_MINUTES", "30")),
    "abort_on_failed_units": True,
    "git_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
}
decl_file.write_text(json.dumps(decl, indent=2) + "\n", encoding="utf-8")
print(f"Sample declaration written: {decl_file}")
PY
fi

if [[ ! -f "$DECL_FILE" ]]; then
  echo "ERROR: Full-run declaration missing: $DECL_FILE" >&2
  echo "Generate units on-host, record expected_work_units, then rerun. See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md (Phase D0)." >&2
  echo "  $PYTHON scripts/generate_vbt_paid_units_jsonl.py ... --out $UNITS_JSONL" >&2
  echo "  wc -l $UNITS_JSONL  # write count to $DECL_FILE as expected_work_units" >&2
  exit 1
fi

DECL_EXPECTED="$("$PYTHON" - "$DECL_FILE" <<'PY'
import json, sys
print(json.load(open(sys.argv[1], encoding="utf-8"))["expected_work_units"])
PY
)"

if [[ "$DECL_EXPECTED" != "$UNIT_COUNT" ]]; then
  echo "ERROR: Declaration expected_work_units=$DECL_EXPECTED != generated unit count=$UNIT_COUNT" >&2
  echo "Regenerate $UNITS_JSONL or update $DECL_FILE before starting workers." >&2
  echo "See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md (Phase D0)." >&2
  exit 1
fi

echo "Declaration OK: expected_work_units=$DECL_EXPECTED"

if [[ -z "${VBT_WORKERS:-}" && -f "$DECL_FILE" ]]; then
  DECL_WORKERS="$("$PYTHON" - "$DECL_FILE" <<'PY'
import json, sys
w = json.load(open(sys.argv[1], encoding="utf-8")).get("workers_requested")
print(w if w is not None else "")
PY
)"
  if [[ -n "$DECL_WORKERS" && "$DECL_WORKERS" -gt 0 ]]; then
    WORKERS="$DECL_WORKERS"
    echo "Workers from declaration: $WORKERS"
  fi
fi

if [[ -z "${VBT_FULL_RUN_ID:-}" && -f "$DECL_FILE" ]]; then
  DECL_RUN_ID="$("$PYTHON" - "$DECL_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("run_id", "vbt_full_run_id"):
    v = d.get(key)
    if isinstance(v, str) and v.strip():
        print(v.strip())
        break
PY
)"
  if [[ -n "$DECL_RUN_ID" ]]; then
    export VBT_FULL_RUN_ID="$DECL_RUN_ID"
    echo "Run id from declaration: $VBT_FULL_RUN_ID"
  fi
fi

RUN_PREFIX="${VBT_RUN_PREFIX:-paid_full}"
if [[ "$SAMPLE_ENABLED" == "1" && -z "${VBT_RUN_PREFIX:-}" ]]; then
  RUN_PREFIX="paid_sample"
fi
export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-${RUN_PREFIX}_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"
LOG_FILE="${OUT_DIR}/orchestrator.log"
mkdir -p "$OUT_DIR"

PAID_SCREEN_SCRIPT="scripts/run_paid_screen.py"

echo "Resolving v2 provenance hashes (events CSV + lake manifest)..."
if ! read -r EVENTS_CSV_HASH LAKE_MANIFEST_HASH < <(
    "$PYTHON" - "$REPO_ROOT" "$EVENTS_CSV" "$DECL_FILE" "$GATE_FILE" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
events_csv = Path(sys.argv[2])
decl_file = Path(sys.argv[3])
gate_file = Path(sys.argv[4])


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:32]


def resolve_events_csv_hash(events_csv: Path, repo_root: Path) -> str:
    path = events_csv if events_csv.is_absolute() else repo_root / events_csv
    if not path.is_file():
        print(
            f"ERROR: events CSV unavailable for hash: {path} "
            "(expected on-host events.csv for --events-csv / --events-csv-hash)",
            file=sys.stderr,
        )
        sys.exit(1)
    return file_sha256(path)


def resolve_lake_manifest_hash(repo_root: Path, decl_file: Path, gate_file: Path) -> str:
    gate_hash = ""
    if gate_file.is_file():
        gate_hash = str(
            (json.loads(gate_file.read_text(encoding="utf-8")).get("pilot_hashes") or {}).get(
                "lake_manifest_hash"
            )
            or ""
        ).strip()
    decl_hash = ""
    if decl_file.is_file():
        decl_hash = str(
            json.loads(decl_file.read_text(encoding="utf-8")).get("lake_manifest_hash") or ""
        ).strip()
    expected = gate_hash or decl_hash
    manifest_env = os.environ.get("HFT3_MANIFEST_PATH", "/data/npz/manifest.parquet").strip()
    manifest_path = Path(manifest_env)
    if not manifest_path.is_absolute():
        manifest_path = repo_root / manifest_path
    if not manifest_path.is_file():
        print(
            f"ERROR: lake manifest file missing: {manifest_path} "
            "(set HFT3_MANIFEST_PATH=/data/npz/manifest.parquet on Vast)",
            file=sys.stderr,
        )
        sys.exit(1)
    actual = file_sha256(manifest_path)
    if expected:
        if actual != expected:
            print(
                f"ERROR: lake manifest hash mismatch: manifest={actual} "
                f"gate/decl={expected} path={manifest_path}",
                file=sys.stderr,
            )
            sys.exit(1)
        return expected
    print(
        "ERROR: lake manifest hash unavailable for v2 launch: "
        "record lake_manifest_hash in gate pilot_hashes or full-run declaration. "
        "Do not substitute units JSONL or manifest.json.",
        file=sys.stderr,
    )
    sys.exit(1)


print(
    resolve_events_csv_hash(events_csv, repo_root),
    resolve_lake_manifest_hash(repo_root, decl_file, gate_file),
)
PY
); then
  exit 1
fi
echo "events_csv_hash=$EVENTS_CSV_HASH lake_manifest_hash=$LAKE_MANIFEST_HASH"

PAID_ARGS=(
  "$PYTHON" "$PAID_SCREEN_SCRIPT"
  --units-jsonl "$UNITS_JSONL"
  --out "$OUT_DIR"
  --vectorbt-scope paid-compute
  --workers "$WORKERS"
  --ready-gate-file "$GATE_FILE"
  --max-wall-clock-seconds "${VBT_MAX_WALL_CLOCK_SECONDS:-86400}"
  --no-llm
  --max-batches-before-recycle "${VBT_MAX_BATCHES_BEFORE_RECYCLE:-100}"
  --max-units-per-batch "${VBT_MAX_UNITS_PER_BATCH:-2}"
  --batch-timeout-seconds "${VBT_BATCH_TIMEOUT_SECONDS:-1800}"
  --cache-memory-limit-mb "${VBT_CACHE_MEMORY_LIMIT_MB:-4096}"
  --cache-max-entries "${VBT_CACHE_MAX_ENTRIES:-1000}"
  --events-csv "$EVENTS_CSV"
  --events-csv-hash "$EVENTS_CSV_HASH"
  --lake-manifest-hash "$LAKE_MANIFEST_HASH"
)
if [[ "${VBT_RESUME:-}" == "1" || "${VBT_RESUME:-}" == "true" ]]; then
  PAID_ARGS+=(--resume)
fi

ABORT_ON_FAIL="${VBT_ABORT_ON_FAILED_UNITS:-}"
if [[ -z "$ABORT_ON_FAIL" && -f "$DECL_FILE" ]]; then
  ABORT_ON_FAIL="$("$PYTHON" - "$DECL_FILE" <<'PY'
import json, sys
print("1" if json.load(open(sys.argv[1], encoding="utf-8")).get("abort_on_failed_units") else "0")
PY
)"
fi
if [[ "$ABORT_ON_FAIL" == "1" || "$ABORT_ON_FAIL" == "true" ]]; then
  PAID_ARGS+=(--abort-on-failed-units)
fi

echo "Starting full run id=$VBT_FULL_RUN_ID workers=$WORKERS out=$OUT_DIR"
"${PAID_ARGS[@]}" 2>&1 | tee "$LOG_FILE"

echo "Manifest: ${OUT_DIR}/paid_screen_run_manifest.json"
"$PYTHON" scripts/aggregate_vbt_promoted_ids.py \
  --manifest "${OUT_DIR}/paid_screen_run_manifest.json" \
  --out runtime/reports/vbt_full_promoted_ids.json
