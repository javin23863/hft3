#!/usr/bin/env bash
# Paper round-trip ingest (R|Trader watch) + trial process + replay-sample + hot-memory snapshot.
# CHI404 only. Uses memory-upgrade latency_summary for replay injection.
set -euo pipefail

REPO="${HFT3_REPO_DIR:-/root/hft3/repo}"
cd "$REPO"
DATE=$(date -u +%F)
EVENT_ID="${EVENT_ID:-CPI_2024_09_11_TIGHT}"
WATCH="${RTRADER_WATCH_DIRS:-/root/hft3/rtrader_watch}"
SYMBOL="${SYMBOL:-MES}"
LATENCY_SUMMARY="${CHI404_LATENCY_SUMMARY:-runtime/latency_reports/latency_summary.json}"

source /root/hft3/.env 2>/dev/null || true
export RITHMIC_TRIAL_ENABLED=1
export RITHMIC_TRIAL_CONNECTOR=rtrader
export RTRADER_WATCH_DIRS="$WATCH"

mkdir -p "$WATCH"
EXPORT="$WATCH/paper_roundtrip_${DATE}.ndjson"

echo "=== write paper round-trip NDJSON (hot-memory core symbols) ==="
python3 - <<'PY'
import json
import time
from datetime import datetime, timezone
from pathlib import Path

date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
watch = Path("/root/hft3/rtrader_watch") / f"paper_roundtrip_{date}.ndjson"
# core_protected + one energy leg from hot_memory_universe.yaml
symbols = ["ES", "NQ", "ZT", "ZN", "SR3", "CL"]
base = time.time_ns()
rows = []
for i, sym in enumerate(symbols):
    oid = f"PAPER-{sym}-RT"
    t0 = base + i * 50_000_000
    px = 5000.0 + i
    rows.extend(
        [
            {
                "event_type": "quote",
                "symbol": sym,
                "exchange": "CME",
                "exchange_timestamp_ns": t0,
                "bid_price": px,
                "ask_price": px + 0.25,
            },
            {
                "event_type": "order_submit",
                "symbol": sym,
                "exchange": "CME",
                "order_id": oid,
                "exchange_timestamp_ns": t0 + 1_000_000,
                "local_receive_timestamp_ns": t0 + 1_500_000,
                "price": px,
                "size": 1,
                "side": "B",
            },
            {
                "event_type": "order_ack",
                "symbol": sym,
                "exchange": "CME",
                "order_id": oid,
                "exchange_timestamp_ns": t0 + 2_000_000,
                "local_receive_timestamp_ns": t0 + 3_000_000,
            },
            {
                "event_type": "fill",
                "symbol": sym,
                "exchange": "CME",
                "order_id": oid,
                "exchange_timestamp_ns": t0 + 4_000_000,
                "local_receive_timestamp_ns": t0 + 4_500_000,
                "price": px,
                "size": 1,
            },
        ]
    )
with watch.open("a", encoding="utf-8") as f:
    for ev in rows:
        f.write(json.dumps(ev, sort_keys=True) + "\n")
print(f"wrote {len(rows)} events -> {watch}")
PY

echo "=== capture ==="
python3 -m data_system.rithmic_trial.pipeline capture \
  --config data_system/config/rithmic_trial.yaml \
  --force --duration-sec 10 --date "$DATE" --symbol "$SYMBOL" --event-id "$EVENT_ID"

echo "=== process ==="
python3 -m data_system.rithmic_trial.pipeline process \
  --config data_system/config/rithmic_trial.yaml \
  --date "$DATE" --symbol "$SYMBOL"

NPZ="$REPO/data/replay/hftbacktest/rithmic_trial/${DATE}/${SYMBOL}/${SYMBOL}_${DATE}_trial.npz"
LATENCY_MS=$(python3 - <<PY
import json
from pathlib import Path
p = Path("$REPO") / "$LATENCY_SUMMARY"
if not p.is_file():
    print("1.0")
else:
    d = json.loads(p.read_text())
    ms = d.get("backtest_latency_ms") or d.get("network_worst_p99_us", 1000) / 1000.0
    print(ms)
PY
)

echo "=== replay-sample (latency_ms=${LATENCY_MS} from ${LATENCY_SUMMARY}) ==="
python3 -m data_system.rithmic_trial.pipeline replay-sample \
  --npz "$NPZ" --simple --latency-ms "$LATENCY_MS"

echo "=== hot-memory telemetry ==="
python3 - <<PY
import json
from pathlib import Path
import sys
sys.path.insert(0, "$REPO")
from workbench.src.data.hot_memory_manager import hot_memory_telemetry_snapshot

repo = Path("$REPO")
snap = hot_memory_telemetry_snapshot(repo)
out = repo / "reports/rithmic_trial/${DATE}/hot_memory_telemetry.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(snap, indent=2), encoding="utf-8")
print(json.dumps({"registry_status": snap.get("registry_status", "ok"), "resident_count": len(snap.get("resident", []))}))
print(f"wrote {out}")
PY

if [[ -f "$REPO/data/npz/MES.v.0_${EVENT_ID}_mbo.npz" ]]; then
  echo "=== replay-event ($EVENT_ID) ==="
  python3 -m data_system.rithmic_trial.pipeline replay-event \
    --config data_system/config/rithmic_trial.yaml \
    --event-id "$EVENT_ID" \
    --chi404-summary "$LATENCY_SUMMARY" \
    --skip-hftbacktest
else
  echo "SKIP replay-event: NPZ not on CHI404 (run from workstation with Databento lake)"
fi

echo "=== latency profile tail ==="
python3 - <<PY
import json
from pathlib import Path
p = Path("$REPO/reports/rithmic_trial/${DATE}/latency_profile.json")
if p.is_file():
    d = json.loads(p.read_text())
    print(json.dumps({
        "order_rtt_ms": d.get("order_rtt_ms"),
        "order_submit_to_ack_us": d.get("order_submit_to_ack_us"),
        "feed_latency_us": d.get("feed_latency_us"),
    }, indent=2))
PY

echo "Paper round-trip run complete."
