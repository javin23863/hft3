#!/usr/bin/env bash
# Phase D full VectorBT paid screen on Vast (default v2 long-lived workers).
# Authority: docs/project/VBT_PAID_SCREEN_UNIT_SCOPE.md, PAID_SCREEN_OPS_COMMANDS.md
# Run ON the Vast instance (NPZ lake already present). Do not use 4-worker smoke topology.
# Units are generated on-host from events.csv + active model registry (not local Stage A survivors).
# Rollback: export VBT_EXECUTION_MODE=v1 before launch (legacy subprocess-per-unit).
# v2 env knobs: VBT_CACHE_MEMORY_LIMIT_MB, VBT_CACHE_MAX_ENTRIES, VBT_MAX_BATCHES_BEFORE_RECYCLE, VBT_RESUME=1
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

EVENTS_CSV="${VBT_EVENTS_CSV:-packages/data_system/config/events.csv}"
UNITS_JSONL="${VBT_FULL_UNITS_JSONL:-runtime/reports/vbt_full_units.jsonl}"
GATE_FILE="${VBT_READY_GATE_FILE:-runtime/reports/paid_screen_ready_gate.json}"
SYMBOLS="${VBT_SYMBOLS:-MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0}"
MODEL_SCOPE="${VBT_MODEL_SCOPE:-active}"
MODEL_IDS="${VBT_MODEL_IDS:-}"
EVENT_TYPES="${VBT_EVENT_TYPES:-}"
RESEARCH_SPLIT="${VBT_RESEARCH_SPLIT:-discovery_confirmation}"
DECL_FILE="${VBT_FULL_RUN_DECLARATION:-runtime/reports/vbt_full_run_declaration.json}"

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

READY_FOR_FULL="$(python3 - "$GATE_FILE" <<'PY'
import json, sys
print("true" if json.load(open(sys.argv[1], encoding="utf-8")).get("ready_for_full_run") else "false")
PY
)"
if [[ "$READY_FOR_FULL" != "true" && "${VBT_IGNORE_LAUNCH_BLOCKED:-}" != "1" ]]; then
  echo "ERROR: ready_for_full_run is not true in $GATE_FILE" >&2
  exit 1
fi

if [[ -f "$DECL_FILE" && "${VBT_IGNORE_LAUNCH_BLOCKED:-}" != "1" ]]; then
  LAUNCH_BLOCKED="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
reason = json.load(open(sys.argv[1], encoding="utf-8")).get("launch_blocked_reason")
print(reason or "")
PY
)"
  if [[ -n "$LAUNCH_BLOCKED" ]]; then
    echo "ERROR: Full run blocked by declaration: $LAUNCH_BLOCKED" >&2
    echo "Clear launch_blocked_reason in $DECL_FILE or set VBT_IGNORE_LAUNCH_BLOCKED=1 after owner approval." >&2
    exit 1
  fi
fi

if [[ -z "${HFT3_NPZ_ROOT:-}" ]]; then
  echo "ERROR: HFT3_NPZ_ROOT unset — NPZ lake must be on this host (Vast data from prior sync)." >&2
  exit 1
fi

echo "=== Vast VectorBT paid screen ==="
echo "repo=$REPO_ROOT nproc=$NPROC workers=$WORKERS npz_root=$HFT3_NPZ_ROOT"
echo "events_csv=$EVENTS_CSV symbols=$SYMBOLS model_scope=$MODEL_SCOPE units_out=$UNITS_JSONL"

bash scripts/install_vbt_hbt_handoff_verify_deps.sh
pip3 install 'vectorbt[rust]==1.0.0' -q

GEN_ARGS=(
  python3 scripts/generate_vbt_paid_units_jsonl.py
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

"${GEN_ARGS[@]}"

UNIT_COUNT="$(grep -c . "$UNITS_JSONL" || true)"
echo "Full unit count: $UNIT_COUNT (must match declaration expected_work_units)"

if [[ ! -f "$DECL_FILE" ]]; then
  echo "ERROR: Full-run declaration missing: $DECL_FILE" >&2
  echo "Generate units on-host, record expected_work_units, then rerun. See docs/project/VBT_PAID_SCREEN_POST_GATE_PLAYBOOK.md (Phase D0)." >&2
  echo "  python3 scripts/generate_vbt_paid_units_jsonl.py ... --out $UNITS_JSONL" >&2
  echo "  wc -l $UNITS_JSONL  # write count to $DECL_FILE as expected_work_units" >&2
  exit 1
fi

DECL_EXPECTED="$(python3 - "$DECL_FILE" <<'PY'
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
  DECL_WORKERS="$(python3 - "$DECL_FILE" <<'PY'
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

BATCH_TIMEOUT="${VBT_BATCH_TIMEOUT_SECONDS:-}"
if [[ -z "$BATCH_TIMEOUT" && -f "$DECL_FILE" ]]; then
  BATCH_TIMEOUT="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
v = json.load(open(sys.argv[1], encoding="utf-8")).get("batch_timeout_seconds")
print(v if v is not None else "")
PY
)"
fi
BATCH_TIMEOUT="${BATCH_TIMEOUT:-1800}"

if [[ -z "${VBT_FULL_RUN_ID:-}" && -f "$DECL_FILE" ]]; then
  DECL_RUN_ID="$(python3 - "$DECL_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for key in ("run_id", "vbt_full_run_id"):
    v = d.get(key)
    if isinstance(v, str) and v.strip() and "PENDING" not in v:
        print(v.strip())
        break
PY
)"
  if [[ -n "$DECL_RUN_ID" ]]; then
    export VBT_FULL_RUN_ID="$DECL_RUN_ID"
    echo "Run id from declaration: $VBT_FULL_RUN_ID"
  fi
fi

export VBT_FULL_RUN_ID="${VBT_FULL_RUN_ID:-paid_full_$(date -u +%Y%m%dT%H%M%SZ)}"
OUT_DIR="${REPO_ROOT}/research_cards/pipeline_runs/${VBT_FULL_RUN_ID}"
LOG_FILE="${OUT_DIR}/orchestrator.log"
mkdir -p "$OUT_DIR"

# Execution mode: v2 (default) long-lived workers; v1 subprocess-per-unit rollback.
EXECUTION_MODE="${VBT_EXECUTION_MODE:-v2}"
PAID_SCREEN_SCRIPT="scripts/run_paid_screen.py"
if [[ "$EXECUTION_MODE" == "v1" ]]; then
  PAID_SCREEN_SCRIPT="scripts/run_vectorbt_paid_screen.py"
  echo "WARN: VBT_EXECUTION_MODE=v1 — legacy subprocess-per-unit path (rollback only)" >&2
fi

# v2 fail-closes without real events/lake provenance hashes. v1 rollback skips this block.
EVENTS_CSV_HASH=""
LAKE_MANIFEST_HASH=""
if [[ "$EXECUTION_MODE" != "v1" ]]; then
  echo "Resolving v2 provenance hashes (events CSV + lake manifest)..."
  if ! read -r EVENTS_CSV_HASH LAKE_MANIFEST_HASH < <(
    python3 - "$REPO_ROOT" "$EVENTS_CSV" "$DECL_FILE" <<'PY'
import hashlib
import json
import os
import sys
from pathlib import Path

repo_root = Path(sys.argv[1])
events_csv = Path(sys.argv[2])
decl_file = Path(sys.argv[3])


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


def resolve_lake_manifest_hash(repo_root: Path, decl_file: Path) -> str:
    manifest_env = os.environ.get("HFT3_MANIFEST_PATH", "").strip()
    if manifest_env:
        manifest_path = Path(manifest_env)
        if not manifest_path.is_absolute():
            manifest_path = repo_root / manifest_path
        if manifest_path.is_file():
            return file_sha256(manifest_path)
    if decl_file.is_file():
        decl_hash = str(
            json.loads(decl_file.read_text(encoding="utf-8")).get("lake_manifest_hash") or ""
        ).strip()
        if decl_hash:
            return decl_hash
    print(
        "ERROR: lake manifest hash unavailable for v2 launch: "
        "set HFT3_MANIFEST_PATH to an on-host manifest file, or record "
        "lake_manifest_hash in the full-run declaration. "
        "Do not substitute units JSONL or other artifacts.",
        file=sys.stderr,
    )
    sys.exit(1)


print(resolve_events_csv_hash(events_csv, repo_root), resolve_lake_manifest_hash(repo_root, decl_file))
PY
  ); then
    exit 1
  fi
  echo "events_csv_hash=$EVENTS_CSV_HASH lake_manifest_hash=$LAKE_MANIFEST_HASH"
fi

PAID_ARGS=(
  python3 "$PAID_SCREEN_SCRIPT"
)
if [[ "$EXECUTION_MODE" != "v1" ]]; then
  PAID_ARGS+=(--execution-mode "$EXECUTION_MODE")
fi
PAID_ARGS+=(
  --units-jsonl "$UNITS_JSONL"
  --out "$OUT_DIR"
  --vectorbt-scope paid-compute
  --workers "$WORKERS"
  --ready-gate-file "$GATE_FILE"
  --max-wall-clock-seconds "${VBT_MAX_WALL_CLOCK_SECONDS:-86400}"
  --no-llm
)
if [[ "$EXECUTION_MODE" != "v1" ]]; then
  PAID_ARGS+=(
    --max-batches-before-recycle "${VBT_MAX_BATCHES_BEFORE_RECYCLE:-100}"
    --cache-memory-limit-mb "${VBT_CACHE_MEMORY_LIMIT_MB:-4096}"
    --cache-max-entries "${VBT_CACHE_MAX_ENTRIES:-1000}"
    --batch-timeout-seconds "$BATCH_TIMEOUT"
    --events-csv "$EVENTS_CSV"
    --events-csv-hash "$EVENTS_CSV_HASH"
    --lake-manifest-hash "$LAKE_MANIFEST_HASH"
  )
  if [[ "${VBT_RESUME:-}" == "1" || "${VBT_RESUME:-}" == "true" ]]; then
    PAID_ARGS+=(--resume)
  fi
fi

echo "Starting full run id=$VBT_FULL_RUN_ID workers=$WORKERS batch_timeout_s=$BATCH_TIMEOUT execution_mode=$EXECUTION_MODE out=$OUT_DIR"
"${PAID_ARGS[@]}" 2>&1 | tee "$LOG_FILE"

echo "Manifest: ${OUT_DIR}/paid_screen_run_manifest.json"
python3 scripts/aggregate_vbt_promoted_ids.py \
  --manifest "${OUT_DIR}/paid_screen_run_manifest.json" \
  --out runtime/reports/vbt_full_promoted_ids.json
