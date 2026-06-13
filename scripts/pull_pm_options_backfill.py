"""WS-0.4a PM-settled ES option backfill — statistics + definitions (2026-06-13).

WHY: the original ES.OPT backfill resolved to QUARTERLY (AM/SOQ-settled) options
only — 1,984 instruments, 3rd-Fri Mar/Jun/Sep/Dec. The WS-1.1 fixing-window gate
needs the PM-settled series that actually expire to the 15:00 CT fixing: EOM (EW),
Friday weeklies (EW1-EW4) and Mon/Tue/Wed/Thu dailies (E{1..5}{A,B,C,D}). Those
are SEPARATE Databento parent symbols (root.OPT) and were never purchased.
Verified (2026-06-13) against real bytes: OI = StatType.OPEN_INTEREST(9), value in
`quantity`, date = ts_ref; instrument_id -> expiry/strike/class via definitions.

Pulls per root, monthly chunks (parent-symbology sync get_range 504s on long
ranges — month chunks avoid it; lesson from ES.OPT). Each chunk is budget-gated,
manifest-recorded, idempotent (existing files skip). Owner-approved full re-scope
(~$100 statistics + definitions) 2026-06-13.

  arg1: schema in {statistics, definition, both}   (default: both)
  arg2 (optional): comma-sep root list             (default: all 25 PM roots)

Run with:
  HFT3_MANIFEST_PATH=C:\\hft3-lake\\manifest.parquet ; HFT3_NPZ_ROOT=C:\\hft3-lake\\npz
  PYTHONPATH=<shims>;<worktree>;<worktree>\\packages
"""
from __future__ import annotations

import os
import sys
import time
import traceback
from datetime import date, datetime, timezone

from data_system.src.databento_client import DatabentoResearchClient

# Fail-fast on manifest-lock contention. A concurrent multi-writer (the 6-shard
# download_event_tape job) holds the shared manifest lock in sustained bursts; the
# default 120s acquire wait makes every contended chunk crawl. 6s lets us skip the
# ledger write and move on -- the chunk's DATA still lands on disk and is recorded
# afterward by scripts/reconcile_pm_backfill_ledger.py (disk -> manifest). No clobber
# risk (the lock is still exclusive); we just defer recording under contention.
import data_system.src.manifest_io as _mio  # noqa: E402

_LOCK_ORIG_INIT = _mio.ManifestFileLock.__init__


def _fastfail_lock_init(self, manifest_path, *, timeout_s=6.0):
    _LOCK_ORIG_INIT(self, manifest_path, timeout_s=timeout_s)


_mio.ManifestFileLock.__init__ = _fastfail_lock_init

LAKE = r"C:\hft3-lake\options"
LOG = os.path.join(LAKE, "pm_backfill_log.txt")
START = date(2023, 5, 1)
END = date(2026, 6, 13)

# CME E-mini S&P PM-settled option roots: EOM, Fri weeklies, Mon-Thu dailies.
# (EW5 absent in this dataset; day letter A=Mon B=Tue C=Wed D=Thu.)
PM_ROOTS = ["EW", "EW1", "EW2", "EW3", "EW4"] + [
    f"E{wk}{d}" for wk in range(1, 6) for d in ("A", "B", "C", "D")
]


def log(msg: str) -> None:
    line = f"{time.strftime('%H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def month_starts(start: date, end: date) -> list[date]:
    out, y, m = [], start.year, start.month
    while date(y, m, 1) < end:
        out.append(date(y, m, 1))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def next_month(d: date) -> date:
    return date(d.year + 1, 1, 1) if d.month == 12 else date(d.year, d.month + 1, 1)


# schema -> (lake subdir, filename infix)
_SCHEMA_DIR = {
    "statistics": ("statistics", "stats"),
    "definition": (os.path.join("definitions", "pm"), "def"),
}


def main() -> int:
    which = sys.argv[1] if len(sys.argv) > 1 else "both"
    schemas = ["statistics", "definition"] if which == "both" else [which]
    roots = sys.argv[2].split(",") if len(sys.argv) > 2 else PM_ROOTS
    months = month_starts(START, END)
    c = DatabentoResearchClient()
    log(f"START schemas={schemas} roots={len(roots)} months={len(months)} "
        f"ledger=${c.budget._calculate_total_used():.2f}")

    done = failed = skipped = unrecorded = 0
    for schema in schemas:
        subdir, infix = _SCHEMA_DIR[schema]
        for root in roots:
            out_dir = os.path.join(LAKE, subdir, root)
            os.makedirs(out_dir, exist_ok=True)
            for ms in months:
                tag = ms.strftime("%Y-%m")
                dest = os.path.join(out_dir, f"{root}_{infix}_{tag}.dbn.zst")
                if os.path.exists(dest):
                    skipped += 1
                    continue
                ce = min(next_month(ms), END)
                s_utc = datetime(ms.year, ms.month, ms.day, tzinfo=timezone.utc)
                e_utc = datetime(ce.year, ce.month, ce.day, tzinfo=timezone.utc)
                for attempt in (1, 2, 3):
                    try:
                        c.download_event_window(
                            event_id=f"OPT_PM_{schema}_{root}_{tag}",
                            symbols=[f"{root}.OPT"],
                            start_utc=s_utc,
                            end_utc=e_utc,
                            schema=schema,
                            stype_in="parent",
                            output_path=dest,
                            override_operating_cap=True,
                        )
                        done += 1
                        break
                    except Exception as e:  # noqa: BLE001 — vendor 5xx/422 happen; log+retry
                        msg = str(e)
                        # empty range for a root in a month with no listings -> not an error
                        if "did not return any data" in msg or "No data" in msg:
                            log(f"EMPTY {schema} {root} {tag} (no listings)")
                            skipped += 1
                            break
                        # ONLY the manifest-lock timeout means the download already
                        # COMPLETED (get_range returns before the ledger append) — accept the
                        # whole file and let reconcile record it from disk at END. Any other
                        # exception (ReadTimeout/ConnectionError mid-download) may have left a
                        # PARTIAL file; never accept it.
                        lock_timeout = isinstance(e, TimeoutError) and "manifest lock" in msg
                        if lock_timeout and os.path.exists(dest) and os.path.getsize(dest) > 0:
                            unrecorded += 1
                            log(f"UNRECORDED {schema} {root} {tag} (file complete; "
                                f"ledger reconcile pending)")
                            break
                        # Network/other error: drop any partial so the retry re-downloads
                        # cleanly (databento refuses to overwrite an existing file).
                        try:
                            if os.path.exists(dest):
                                os.remove(dest)
                        except OSError:
                            pass
                        log(f"RETRY {schema} {root} {tag} a{attempt}: {type(e).__name__}: {msg[:120]}")
                        time.sleep(8 * attempt)
                else:
                    failed += 1
                    log(f"FAILED {schema} {root} {tag} after 3 attempts")
            log(f"root done: {schema} {root} | done={done} fail={failed} skip={skipped} "
                f"unrec={unrecorded} ledger=${c.budget._calculate_total_used():.2f}")

    total = c.budget._calculate_total_used()
    log(f"END done={done} failed={failed} skipped={skipped} unrecorded={unrecorded} "
        f"(run reconcile_pm_backfill_ledger.py to record {unrecorded} on-disk chunks) ledger=${total:.2f}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        log("FATAL:\n" + traceback.format_exc())
        sys.exit(2)
