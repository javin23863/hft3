#!/usr/bin/env bash
# CHI404 keepalive: priority macro MBO download shard 1 until stopped.
REPO="${REPO:-/root/hft3/repo}"
WORKERS="${WORKERS:-32}"
SHARD_INDEX="${SHARD_INDEX:-1}"
SHARD_COUNT="${SHARD_COUNT:-2}"
LOG="${REPO}/runtime/data_downloads/macro_releases_chi404.log"

cd "$REPO" || exit 1
set -a
# shellcheck disable=SC1091
source /root/hft3/.env
set +a

while true; do
  echo "[keepalive $(date -Is)] starting shard ${SHARD_INDEX}/${SHARD_COUNT} workers=${WORKERS}" >>"$LOG"
  python3 scripts/download_mbo_release_data.py \
    --download \
    --derive-npz \
    --scope macro_releases \
    --priority-events \
    --workers "$WORKERS" \
    --shard-index "$SHARD_INDEX" \
    --shard-count "$SHARD_COUNT" \
    --output runtime/data_downloads/mbo_download_report_chi404.json \
    >>"$LOG" 2>&1 || true
  echo "[keepalive $(date -Is)] batch finished - sleep 30s" >>"$LOG"
  sleep 30
done
