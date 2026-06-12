#!/usr/bin/env python3
"""General resumable downloader for the futures EVENT-WINDOW universe.

Fills the missing-tape gap across ALL event types (minus an exclude set) for the
per-event symbol list in events.csv. Resumable + size-aware (treats sub-4KB
shells as missing so broken/empty NPZ get re-pulled) + cost-capped. Prices each
window with get_cost first, skips $0/holiday windows, aborts cleanly at the cap.

    # Tier C (default excludes the daily markers), capped:
    python scripts/download_event_tape.py --cost-cap 650
    # price only:
    python scripts/download_event_tape.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
for _p in [str(_REPO), str(_REPO / "packages")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from data_system.src.keystore import load_keys  # noqa: E402
from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from data_system.src.npz_resolver import npz_root  # noqa: E402
from backtest_pipeline.src.converter import DatabentoConverter  # noqa: E402
from data_system.src.databento_client import DatabentoResearchClient  # noqa: E402

EMBARGO = "2026-01-01"
EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
NPZ_ROOT = npz_root(_REPO)
RAW_DIR = _REPO / "data" / "raw" / "event_tape"
RECEIPT = _REPO / "runtime" / "databento" / "event_tape_download_receipt.json"
SHELL_BYTES = 4096  # below this an NPZ is an empty/broken shell -> re-download

# Tier C: skip the high-volume daily markers (not econ releases) + the mislabeled one.
DEFAULT_EXCLUDE = "FACTORY_ORDERS,CASH_EQUITY_OPEN,FRIDAY_CLOSE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("event_tape")


def _present(event_id: str, sym: str) -> bool:
    p = NPZ_ROOT / f"{sym}_{event_id}_mbo.npz"
    return p.is_file() and p.stat().st_size >= SHELL_BYTES


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude-types", default=DEFAULT_EXCLUDE)
    ap.add_argument("--cost-cap", type=float, default=650.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-dbn", action="store_true")
    args = ap.parse_args()
    exclude = {t.strip() for t in args.exclude_types.split(",") if t.strip()}

    load_keys()
    NPZ_ROOT.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    client = DatabentoResearchClient()
    converter = DatabentoConverter(output_dir=str(NPZ_ROOT))

    ev = load_and_parse_events(str(EVENTS_CSV))
    ev = ev[ev["release_date"] < EMBARGO].copy().sort_values("release_date").reset_index(drop=True)
    ev = ev[~ev["event_type"].isin(exclude)].reset_index(drop=True)
    log.info("Excluding types: %s", sorted(exclude))
    log.info("Candidate events (pre-embargo, post-exclude): %d", len(ev))

    processed = empty = skipped = written = 0
    running = 0.0
    per_sym: dict[str, int] = {}
    n = len(ev)

    for idx, row in ev.iterrows():
        eid = str(row["event_id"])
        syms = [s.strip() for s in str(row.get("symbols", "")).replace(";", ",").split(",") if s.strip()]
        missing = [s for s in syms if not _present(eid, s)]
        if not missing:
            skipped += 1
            continue
        start = row["start_utc"].to_pydatetime()
        end = row["end_utc"].to_pydatetime()
        try:
            cost = float(client.estimate_cost(symbols=missing, start_utc=start, end_utc=end))
        except Exception as exc:
            log.warning("[%d/%d] %s price failed: %s", idx + 1, n, eid, exc)
            empty += 1
            continue
        if cost == 0.0:
            empty += 1
            continue
        if running + cost > args.cost_cap:
            log.warning("COST CAP $%.2f would be exceeded (running $%.2f + $%.4f). Stopping cleanly at %d/%d (%s). Re-run to resume.",
                        args.cost_cap, running, cost, idx + 1, n, eid)
            break
        if args.dry_run:
            running += cost
            processed += 1
            continue
        dbn = RAW_DIR / f"{eid}_mbo.dbn.zst"
        try:
            dest = client.download_event_window(event_id=eid, symbols=missing, start_utc=start, end_utc=end,
                                                output_path=str(dbn), override_operating_cap=True, override_hard_limit=False)
        except Exception as exc:
            log.error("[%d/%d] %s download failed: %s", idx + 1, n, eid, exc)
            empty += 1
            continue
        dbn = Path(dest) if dest else dbn
        conv = []
        for s in missing:
            try:
                converter.convert_file(str(dbn), s)
                conv.append(s)
                per_sym[s] = per_sym.get(s, 0) + 1
                written += 1
            except Exception as exc:
                log.warning("  convert %s/%s failed (thin/no records): %s", eid, s, exc)
        running += cost
        processed += 1
        log.info("[%d/%d] %s cost=$%.4f conv=%s running=$%.2f", idx + 1, n, eid, cost, conv or "[]", running)
        if not args.keep_dbn and dbn.is_file():
            try:
                dbn.unlink()
            except OSError:
                pass

    summary = {
        "run_utc": datetime.now(timezone.utc).isoformat(), "dry_run": args.dry_run,
        "excluded_types": sorted(exclude), "windows_processed": processed, "windows_empty": empty,
        "windows_skipped_existing": skipped, "symbols_npz_written": written,
        "total_cost_usd": round(running, 6), "cost_cap": args.cost_cap,
        "npz_root": str(NPZ_ROOT), "per_symbol_file_counts": per_sym,
    }
    print("\n" + "=" * 70)
    print(f"EVENT TAPE DOWNLOAD  (dry_run={args.dry_run})")
    print(f"  processed {processed}  empty/no-data {empty}  skipped-existing {skipped}  npz_written {written}")
    print(f"  TOTAL COST: ${running:.4f}  (cap ${args.cost_cap})")
    if not args.dry_run:
        RECEIPT.parent.mkdir(parents=True, exist_ok=True)
        RECEIPT.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"  receipt: {RECEIPT}")
    print("=" * 70)


if __name__ == "__main__":
    main()
