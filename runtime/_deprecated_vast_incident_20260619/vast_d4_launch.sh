set -euo pipefail
source /root/hft3/.env
cd /root/hft3/repo
export VBT_FULL_RUN_ID=paid_full_$(date -u +%Y%m%dT%H%M%SZ)
echo "Starting D4 run_id=$VBT_FULL_RUN_ID"
bash scripts/run_vbt_paid_screen_vast_full.sh
