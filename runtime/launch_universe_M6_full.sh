#!/usr/bin/env bash
set -euo pipefail

# Cap BLAS/OpenMP per process so high worker counts do not exhaust pthread budget.
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export MKL_NUM_THREADS=1
export NUMEXPR_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export BLIS_NUM_THREADS=1
export HFT3_NPZ_ROOT=/data/npz

cd /root/hft3/repo || { echo "FATAL: missing /root/hft3/repo" >&2; exit 1; }

if [[ ! -d "$HFT3_NPZ_ROOT" ]]; then
  echo "FATAL: HFT3_NPZ_ROOT=$HFT3_NPZ_ROOT is not a directory" >&2
  exit 1
fi
NPZ_COUNT="$(find "$HFT3_NPZ_ROOT" -maxdepth 1 -type f -name '*.npz' 2>/dev/null | wc -l | tr -d ' ')"
if [[ "${NPZ_COUNT:-0}" -lt 1000 ]]; then
  echo "FATAL: lake too small at $HFT3_NPZ_ROOT (npz_count=$NPZ_COUNT)" >&2
  exit 1
fi

WORKERS="${WORKERS:-$(($(nproc)-4))}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="/root/hft3/repo/runtime/universe_M6_full_${TS}.log"
echo "$LOG" > /root/hft3/repo/runtime/.universe_M6_full_latest_log
echo "workers=$WORKERS nproc=$(nproc) npz=$HFT3_NPZ_ROOT npz_count=$NPZ_COUNT log=$LOG commit=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" >&2

exec python3 scripts/run_event_universe.py \
  --lane cme \
  --symbols MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0 \
  --events-csv packages/data_system/config/events.csv \
  --out research_cards/universe_M6_full \
  --workers "$WORKERS" \
  --rescan \
  2>&1 | tee -a "$LOG"
