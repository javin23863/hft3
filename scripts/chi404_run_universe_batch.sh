#!/usr/bin/env bash
# chi404_run_universe_batch.sh — run a universe shard on CHI404 during idle windows
#
# USAGE
#   ./scripts/chi404_run_universe_batch.sh <SHARD> [EXPECTED_COMMIT]
#
#   SHARD          Required. Shard spec in I/N format (e.g. "0/2" or "1/2").
#   EXPECTED_COMMIT  Optional. If provided the script verifies HEAD matches before
#                    launching (prevents running stale code after a merge).
#
# §5 IDLE-EXCEPTION CONSTRAINTS (specs/HOT_PATH.md §5)
#   This script may ONLY run when CHI404 is fully idle:
#     - NOT while hft3-rithmic-trial.service is active
#     - NOT while any paper-latency / live-session daemon is active
#     - NOT while any rithmic_latency_probe process is running
#   The script enforces these constraints and refuses to start if any is violated.
#   Never invoke this script concurrently with latency probes or live/paper sessions.
#
# NOTE ON PYTHON INVOCATION
#   The "-S" flag (skip site.py) is a Windows-workstation workaround for a
#   pip_system_certs.pth stall (see project memory: reference_sandbox_python_stall.md).
#   On Linux (this box) there is no such stall; plain python3 is used here.

set -euo pipefail

# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

if [[ $# -lt 1 ]]; then
    echo "ERROR: SHARD argument required (e.g. 0/2 or 1/2)" >&2
    echo "Usage: $0 <SHARD> [EXPECTED_COMMIT]" >&2
    exit 1
fi

SHARD="$1"
EXPECTED_COMMIT="${2:-}"

# Validate shard format superficially (deep validation is done by the script)
if ! [[ "$SHARD" =~ ^[0-9]+/[0-9]+$ ]]; then
    echo "ERROR: SHARD must be in I/N format (e.g. 0/2), got: $SHARD" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# §5 SAFETY GUARDS — refuse to run if CHI404 is not idle
# ---------------------------------------------------------------------------

echo "=== CHI404 idle safety checks ==="

# Guard 1: hft3-rithmic-trial.service must not be active
if systemctl is-active --quiet hft3-rithmic-trial.service 2>/dev/null; then
    echo "ABORT: hft3-rithmic-trial.service is active. Batch research must not" >&2
    echo "       run concurrently with a live/paper Rithmic trial session." >&2
    echo "       Stop the service first: sudo systemctl stop hft3-rithmic-trial.service" >&2
    exit 2
fi
echo "  [OK] hft3-rithmic-trial.service is not active"

# Guard 2: paper-latency daemon (hft3-paper-latency.service) must not be active
if systemctl is-active --quiet hft3-paper-latency.service 2>/dev/null; then
    echo "ABORT: hft3-paper-latency.service is active. Batch research must not" >&2
    echo "       run concurrently with the paper-latency session daemon." >&2
    exit 2
fi
echo "  [OK] hft3-paper-latency.service is not active"

# Guard 3: rithmic_latency_probe process must not be running
if pgrep -x rithmic_latency_probe > /dev/null 2>&1; then
    echo "ABORT: rithmic_latency_probe process is running. Batch research must not" >&2
    echo "       run concurrently with active latency probes — it would pollute" >&2
    echo "       the jitter numbers the box exists to produce." >&2
    exit 2
fi
echo "  [OK] rithmic_latency_probe is not running"

echo "=== Safety checks passed ==="

# ---------------------------------------------------------------------------
# Environment checks
# ---------------------------------------------------------------------------

echo "=== Environment checks ==="

# Check HFT3_NPZ_ROOT is set and non-empty
if [[ -z "${HFT3_NPZ_ROOT:-}" ]]; then
    echo "ERROR: HFT3_NPZ_ROOT is not set. Export the path to the NPZ lake directory." >&2
    echo "  e.g.: export HFT3_NPZ_ROOT=/mnt/lake/npz" >&2
    exit 1
fi
echo "  HFT3_NPZ_ROOT=$HFT3_NPZ_ROOT"

# Check lake directory exists and contains at least one NPZ
if [[ ! -d "$HFT3_NPZ_ROOT" ]]; then
    echo "ERROR: HFT3_NPZ_ROOT directory does not exist: $HFT3_NPZ_ROOT" >&2
    exit 1
fi
NPZ_COUNT=$(find "$HFT3_NPZ_ROOT" -maxdepth 1 -name '*.npz' | wc -l)
if [[ "$NPZ_COUNT" -eq 0 ]]; then
    echo "ERROR: HFT3_NPZ_ROOT directory is empty (no *.npz files): $HFT3_NPZ_ROOT" >&2
    exit 1
fi
echo "  Lake dir OK: $NPZ_COUNT NPZ file(s) found"

# Check we are inside the repo (git repo sanity)
REPO_ROOT="$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$REPO_ROOT" ]]; then
    echo "ERROR: Could not determine repo root. Run from inside the hft3 repo." >&2
    exit 1
fi
echo "  Repo root: $REPO_ROOT"

# Optional: verify HEAD matches expected commit
ACTUAL_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
if [[ -n "$EXPECTED_COMMIT" ]]; then
    if [[ "$ACTUAL_COMMIT" != "$EXPECTED_COMMIT" ]]; then
        echo "ERROR: Repo HEAD ($ACTUAL_COMMIT) does not match expected commit ($EXPECTED_COMMIT)." >&2
        echo "       Pull the correct commit before running the batch." >&2
        exit 1
    fi
    echo "  Commit verified: $ACTUAL_COMMIT"
else
    echo "  Commit (unverified): $ACTUAL_COMMIT"
fi

echo "=== Environment checks passed ==="

# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------

# Worker count: all cores minus 2 housekeeping/latency cores
WORKERS=$(( $(nproc) - 2 ))
if [[ "$WORKERS" -lt 1 ]]; then
    WORKERS=1
fi

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
SHARD_LABEL="${SHARD//\//-}"   # e.g. "0/2" → "0-2"
OUT_DIR="$REPO_ROOT/research_cards/universe_full_chi404_shard${SHARD_LABEL}_${TIMESTAMP}"
LOG_FILE="$REPO_ROOT/research_cards/universe_full_chi404_shard${SHARD_LABEL}_${TIMESTAMP}.log"

echo ""
echo "Launching shard $SHARD on $WORKERS workers"
echo "  Output dir : $OUT_DIR"
echo "  Log file   : $LOG_FILE"
echo ""

# nohup + background so the SSH session can disconnect
nohup python3 \
    "$REPO_ROOT/scripts/run_event_universe.py" \
    --rescan \
    --workers "$WORKERS" \
    --shard "$SHARD" \
    --out "$OUT_DIR" \
    > "$LOG_FILE" 2>&1 &

BGPID=$!
echo "Launched PID $BGPID"
echo ""
echo "Monitor progress:"
echo "  tail -f $LOG_FILE"
echo ""
echo "Check when done:"
echo "  wait $BGPID && echo DONE || echo FAILED"
echo "  cat $OUT_DIR/universe_result.json | python3 -c \"import json,sys; d=json.load(sys.stdin); print('units_run:', d.get('units_run')); print('errored:', d.get('units_errored'))\""
