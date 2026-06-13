"""WS-0.4a fixing-window MBO pull — owner-approved 2026-06-13 (~$95, post-May-2023).

Pulls 10-minute ES futures MBO windows (14:55-15:05 CT) for every option-expiry
day from 2023-05-01 to 2026-06-12 into the canonical lake options lane.
Each window is a separate budget-gated request (~$0.12) recorded in the spend
ledger. Idempotent: existing files are skipped, so the script can be re-run
after interruption. Dates already covered by lake NPZ (ES.v.0) are skipped.

Run with:
  HFT3_MANIFEST_PATH=C:\\hft3-lake\\manifest.parquet
  PYTHONPATH=<worktree>;<worktree>\\packages
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date

from data_system.src.databento_client import DatabentoResearchClient
from options_data.src.backfill_planner import plan_fixing_windows

OUT_DIR = r"C:\hft3-lake\options\fixing_mbo"
LOG = os.path.join(OUT_DIR, "pull_log.txt")
# ES.v.0 futures MBO already in lake for these expiry dates (inventory 2026-06-13)
ALREADY_COVERED = {"2024-09-18", "2025-06-20"}


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    # Schema choice (2026-06-13 lesson): "trades" is the default. The original
    # MBO run was killed at 275/782 — every intraday-start streaming MBO request
    # is billed with a synthetic full-book snapshot (~0.45 GB uncompressed) that
    # metadata.get_cost does NOT include (~7x cost blowout). The fixing study
    # consumes trades only; "trades" has no snapshot and bills as estimated
    # (~$0.08/window measured).
    schema = sys.argv[1] if len(sys.argv) > 1 else "trades"
    os.makedirs(OUT_DIR, exist_ok=True)
    windows = plan_fixing_windows(date(2023, 5, 1), date(2026, 6, 12))
    windows = [w for w in windows if w["date"] not in ALREADY_COVERED]
    c = DatabentoResearchClient()
    log(f"START {len(windows)} windows schema={schema}, ledger=${c.budget._calculate_total_used():.2f}")

    from datetime import datetime

    done = failed = skipped = 0
    for i, w in enumerate(windows):
        d = w["date"]
        # An existing MBO window already contains the trades (adapter filters
        # action==T), so either file satisfies the date.
        mbo_dest = os.path.join(OUT_DIR, f"ES_fixing_{d}.dbn.zst")
        dest = mbo_dest if schema == "mbo" else os.path.join(OUT_DIR, f"ES_fixing_trades_{d}.dbn.zst")
        if os.path.exists(dest) or os.path.exists(mbo_dest):
            skipped += 1
            continue
        start = datetime.fromisoformat(w["start_utc"])
        end = datetime.fromisoformat(w["end_utc"])
        for attempt in (1, 2, 3):
            try:
                c.download_event_window(
                    event_id=f"OPT_FIXWIN_ES_{d}",
                    symbols=["ES.v.0"],
                    start_utc=start,
                    end_utc=end,
                    schema=schema,
                    stype_in="continuous",
                    output_path=dest,
                    override_operating_cap=True,
                )
                done += 1
                break
            except Exception as e:  # noqa: BLE001 — log and retry; vendor 5xx happen
                log(f"RETRY {d} attempt {attempt}: {type(e).__name__}: {e}")
                time.sleep(10 * attempt)
        else:
            failed += 1
            log(f"FAILED {d} after 3 attempts")
        if (i + 1) % 25 == 0:
            log(f"progress {i + 1}/{len(windows)} done={done} failed={failed} skipped={skipped}")

    total = c.budget._calculate_total_used()
    log(f"END done={done} failed={failed} skipped={skipped} ledger=${total:.2f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        sys.exit(2)
