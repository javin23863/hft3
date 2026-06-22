#!/usr/bin/env bash
# Fast Belgium -> Spain NPZ + checkpoint migration (largest practical chunks).
# Run from workstation AFTER both instances are SSH-ready.
# Env: BE_SSH (e.g. root@ssh7.vast.ai), BE_PORT, ES_SSH, ES_PORT
set -euo pipefail

BE_SSH="${BE_SSH:?set BE_SSH e.g. root@ssh7.vast.ai}"
BE_PORT="${BE_PORT:?set BE_PORT e.g. 15808}"
ES_SSH="${ES_SSH:?set ES_SSH}"
ES_PORT="${ES_PORT:?set ES_PORT}"
OLD_RUN_ID="${OLD_RUN_ID:-paid_full_250w_b1_20260621T124024Z}"
NEW_RUN_ID="${NEW_RUN_ID:-paid_full_spain_$(date -u +%Y%m%dT%H%M%SZ)}"
PARALLEL="${PARALLEL:-16}"
REPO="${REMOTE_REPO:-/root/hft3/repo}"

BE=(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -p "$BE_PORT" "$BE_SSH")
ES=(ssh -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new -p "$ES_PORT" "$ES_SSH")

echo "=== Prep Spain dirs ==="
"${ES[@]}" "mkdir -p /data/npz ${REPO}/research_cards/pipeline_runs/${NEW_RUN_ID}/units"

echo "=== Install rsync/parallel on both if missing ==="
for cmd in "${BE[@]}" "${ES[@]}"; do
  "${cmd[@]}" 'command -v rsync >/dev/null || (apt-get update -qq && apt-get install -y -qq rsync); command -v parallel >/dev/null || (apt-get update -qq && apt-get install -y -qq parallel)' 2>/dev/null || true
done

echo "=== Copy manifest.parquet first (gate hash) ==="
"${BE[@]}" "test -f /data/npz/manifest.parquet"
rsync -avh --whole-file --no-compress --partial --inplace \
  -e "ssh -p ${BE_PORT} -o StrictHostKeyChecking=accept-new -o Compression=no" \
  "${BE_SSH}:/data/npz/manifest.parquet" /tmp/manifest.parquet.$$
rsync -avh --whole-file --no-compress --partial --inplace \
  -e "ssh -p ${ES_PORT} -o StrictHostKeyChecking=accept-new -o Compression=no" \
  /tmp/manifest.parquet.$$ "${ES_SSH}:/data/npz/manifest.parquet"
rm -f /tmp/manifest.parquet.$$

echo "=== Parallel NPZ lake (push from Belgium -> Spain, ${PARALLEL} streams) ==="
# Belgium pushes local reads; each stream rsyncs a chunk of files (whole-file, no recompress).
"${BE[@]}" "bash -s" <<EOF
set -euo pipefail
mkdir -p /tmp/npz_chunks
find /data/npz -maxdepth 1 -type f -name '*.npz' | sort > /tmp/npz_all.lst
split -d -n l/${PARALLEL} /tmp/npz_all.lst /tmp/npz_chunks/chunk_
ES_SSH='${ES_SSH}'
ES_PORT='${ES_PORT}'
export ES_SSH ES_PORT
parallel -j ${PARALLEL} --halt soon,fail=1 '
  rsync -avh --whole-file --no-compress --partial --inplace --info=stats1 \
    -e "ssh -p \$ES_PORT -o StrictHostKeyChecking=accept-new -o Compression=no -o TCPKeepAlive=yes" \
    --files-from={} / ${ES_SSH}:/data/npz/
' ::: /tmp/npz_chunks/chunk_*
EOF

echo "=== Valid unit artifacts from old run (resume checkpoint) ==="
OLD_UNITS="${REPO}/research_cards/pipeline_runs/${OLD_RUN_ID}/units"
NEW_UNITS="${REPO}/research_cards/pipeline_runs/${NEW_RUN_ID}/units"
"${BE[@]}" "test -d ${OLD_UNITS}"
# Belgium -> workstation staging -> Spain (single stream; many small JSON dirs).
STAGE="$(mktemp -d)"
trap 'rm -rf "${STAGE}"' EXIT
rsync -avh --whole-file --no-compress --partial --inplace --info=progress2 \
  -e "ssh -p ${BE_PORT} -o StrictHostKeyChecking=accept-new -o Compression=no" \
  "${BE_SSH}:${OLD_UNITS}/" "${STAGE}/"
rsync -avh --whole-file --no-compress --partial --inplace --info=progress2 \
  -e "ssh -p ${ES_PORT} -o StrictHostKeyChecking=accept-new -o Compression=no" \
  "${STAGE}/" "${ES_SSH}:${NEW_UNITS}/"

echo "=== Counts ==="
"${ES[@]}" "echo NPZ=\$(find /data/npz -maxdepth 1 -name '*.npz' | wc -l); echo UNITS=\$(find ${REPO}/research_cards/pipeline_runs/${NEW_RUN_ID}/units -mindepth 1 -maxdepth 1 -type d | wc -l)"
echo "MIGRATION_PASS new_run_id=${NEW_RUN_ID}"
