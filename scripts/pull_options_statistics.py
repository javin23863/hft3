"""WS-0.4a ES.OPT statistics backfill — sync monthly-chunked pull (2026-06-13).

WHY THIS EXISTS (speed): the 2018->2026 batch job GLBX-20260612-5WLAWUBM3Q
(24.6 GB, $22.93) sat "processing" >12h with no progress field. The WS-1.1
OI-conditioned gate only needs statistics for dates with a matching fixing
window, i.e. 2023-05-01 onward (9.2 GB, $8.59). Probed: metadata.get_cost /
get_billable_size return in seconds; only one *long-range* streaming get_range
504'd. Monthly chunks (~200 MB each) stream clean and deliver in minutes.

Pulls ES.OPT statistics (daily OI/settlement/volume per instrument) one calendar
month at a time into C:\\hft3-lake\\options\\statistics. Each chunk is a separate
budget-gated, manifest-recorded request. Idempotent: existing month files skip,
so the script can be re-run after interruption.

Run with:
  HFT3_MANIFEST_PATH=C:\\hft3-lake\\manifest.parquet
  PYTHONPATH=<shims>;<worktree>;<worktree>\\packages
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date, datetime, timezone

from data_system.src.databento_client import DatabentoResearchClient

OUT_DIR = r"C:\hft3-lake\options\statistics"
LOG = os.path.join(OUT_DIR, "stats_pull_log.txt")

START = date(2023, 5, 1)
# End exclusive at first-of-current-month boundary handling below; statistics
# settle EOD so the freshest day or two may 422 (vendor lag) — retries log and
# move on, the data_doctor grace window keeps trailing gaps WARN not FAIL.
END = date(2026, 6, 13)


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def month_starts(start: date, end: date) -> list[date]:
    out: list[date] = []
    y, m = start.year, start.month
    while date(y, m, 1) < end:
        out.append(date(y, m, 1))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    months = month_starts(START, END)
    c = DatabentoResearchClient()
    log(f"START {len(months)} month-chunks ES.OPT statistics, "
        f"ledger=${c.budget._calculate_total_used():.2f}")

    done = failed = skipped = 0
    for i, ms in enumerate(months):
        tag = ms.strftime("%Y-%m")
        dest = os.path.join(OUT_DIR, f"ES_stats_{tag}.dbn.zst")
        if os.path.exists(dest):
            skipped += 1
            continue
        chunk_end = min(next_month(ms), END)
        start_utc = datetime(ms.year, ms.month, ms.day, tzinfo=timezone.utc)
        end_utc = datetime(chunk_end.year, chunk_end.month, chunk_end.day, tzinfo=timezone.utc)
        for attempt in (1, 2, 3):
            try:
                c.download_event_window(
                    event_id=f"OPT_STATS_ES_{tag}",
                    symbols=["ES.OPT"],
                    start_utc=start_utc,
                    end_utc=end_utc,
                    schema="statistics",
                    stype_in="parent",
                    output_path=dest,
                    override_operating_cap=True,
                )
                done += 1
                log(f"OK {tag} -> {os.path.getsize(dest)/1e6:.1f}MB")
                break
            except Exception as e:  # noqa: BLE001 — log and retry; vendor 5xx/422 happen
                log(f"RETRY {tag} attempt {attempt}: {type(e).__name__}: {e}")
                time.sleep(10 * attempt)
        else:
            failed += 1
            log(f"FAILED {tag} after 3 attempts")

    total = c.budget._calculate_total_used()
    log(f"END done={done} failed={failed} skipped={skipped} ledger=${total:.2f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        sys.exit(2)
