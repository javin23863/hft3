"""Synthetic OPRA fixture: 30 strikes × 1 front-month × 2 sides, 1-min bars."""
import json
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OUT = REPO / "packages" / "equities_lane" / "fixtures" / "opra_chain_v1.ndjson"


def main() -> None:
    expiry = "2026-07-17"
    underlying = "RUNNER"
    spot = 10.0
    base_ts = int(datetime(2026, 3, 15, 14, 30, 0, tzinfo=timezone.utc).timestamp() * 1e9)
    iv = 0.6
    lines: list[str] = []
    strikes = [round(spot * (0.7 + 0.02 * i), 2) for i in range(31)]
    for bar_idx in range(3):
        ts_ns = base_ts + bar_idx * 60 * 1_000_000_000
        for strike in strikes:
            for right in ("C", "P"):
                intrinsic = max(0.0, spot - strike) if right == "C" else max(0.0, strike - spot)
                time_value = max(0.05, abs(strike - spot) * 0.05) * (1.0 + 0.01 * bar_idx)
                bid = round(intrinsic + time_value * 0.95, 4)
                ask = round(intrinsic + time_value * 1.05, 4)
                lines.append(json.dumps({
                    "session_id": "fixture_low_float_v1",
                    "underlying": underlying,
                    "quote_ts_ns": ts_ns,
                    "symbol": f"{underlying}   260717{right}{int(strike*1000):08d}",
                    "strike": strike,
                    "right": right,
                    "expiry": expiry,
                    "bid": bid,
                    "ask": ask,
                }))
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {len(lines)} rows to {OUT}")


if __name__ == "__main__":
    main()
