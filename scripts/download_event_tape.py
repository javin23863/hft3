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
import random
import socket
import sys
import threading
import time
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
PRICE_TIMEOUT_S = 60      # watchdog on metadata.get_cost
DOWNLOAD_TIMEOUT_S = 120  # watchdog on timeseries.get_range (big windows take a while)

# Tier C: skip the high-volume daily markers (not econ releases) + the mislabeled one.
DEFAULT_EXCLUDE = "FACTORY_ORDERS,CASH_EQUITY_OPEN,FRIDAY_CLOSE"

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("event_tape")

# Bound any blocking socket op so a hung Databento connection raises
# socket.timeout (caught by _with_retry as transient -> bounded retry, then skip)
# instead of blocking a shard forever. Generous enough for large MBO windows.
socket.setdefaulttimeout(180)


def _present(event_id: str, sym: str) -> bool:
    p = NPZ_ROOT / f"{sym}_{event_id}_mbo.npz"
    return p.is_file() and p.stat().st_size >= SHELL_BYTES


# Transient = worth retrying (concurrent-load / network blips). Everything else
# (422 symbology_invalid, "already exists", 404 no-data) is permanent for this
# window — retrying just burns wall-clock, so we re-raise on the first hit.
_TRANSIENT_MARKERS = (
    "429", "rate limit", "too many", "timeout", "timed out", "connection",
    "reset", "temporarily", "503", "502", "500", "504", "read timed",
)


def _with_retry(fn, *, tries: int = 4, base: float = 3.0):
    """Call fn(); retry ONLY transient errors with exponential backoff + jitter.

    Several shard processes hit the historical API concurrently, so 429 / 5xx /
    connection blips are expected and worth a retry. Permanent errors for a given
    window (422 symbology_invalid, file-already-exists, no-data 404) are re-raised
    immediately so the caller logs+skips fast instead of grinding 4 backoffs.
    """
    last: Exception | None = None
    for attempt in range(tries):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            last = exc
            msg = str(exc).lower()
            transient = any(m in msg for m in _TRANSIENT_MARKERS)
            if not transient or attempt == tries - 1:
                break
            rate_limited = "429" in msg or "rate" in msg or "too many" in msg
            delay = base * (2 ** attempt) * (3.0 if rate_limited else 1.0)
            delay += random.uniform(0, base)
            time.sleep(delay)
    assert last is not None
    raise last


def _call_with_timeout(fn, seconds):
    """Run fn() in a daemon thread; raise TimeoutError if it doesn't return in
    `seconds`. The hung thread is abandoned (daemon -> dies with the process).

    socket.setdefaulttimeout does NOT bound databento's HTTP client (observed:
    calls froze far past the default with no socket.timeout), so this hard
    wall-clock watchdog is the only reliable way to unstick a stalled request.
    The "timed out" message is a transient marker, so _with_retry retries it.
    """
    box: dict = {}

    def _run():
        try:
            box["r"] = fn()
        except BaseException as e:  # noqa: BLE001
            box["e"] = e

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise TimeoutError(f"call timed out after {seconds}s (abandoned)")
    if "e" in box:
        raise box["e"]
    return box.get("r")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exclude-types", default=DEFAULT_EXCLUDE)
    ap.add_argument("--cost-cap", type=float, default=650.0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keep-dbn", action="store_true")
    ap.add_argument("--shard", default=None, metavar="I/N",
                    help="Run only shard I of N (rows where row_index %% N == I). "
                         "Disjoint event sets across shards -> safe to run N copies in parallel.")
    ap.add_argument("--start-idx", type=int, default=0, dest="start_idx",
                    help="Skip all rows with idx < START_IDX. Jumps straight past the "
                         "already-filled/unfillable head to a tail region without re-running its gap probes.")
    args = ap.parse_args()
    exclude = {t.strip() for t in args.exclude_types.split(",") if t.strip()}

    shard_i = shard_n = None
    if args.shard:
        _parts = args.shard.split("/")
        if len(_parts) != 2:
            ap.error(f"--shard must be 'I/N' (e.g. 0/6), got {args.shard!r}")
        shard_i, shard_n = int(_parts[0]), int(_parts[1])
        if not (shard_n >= 1 and 0 <= shard_i < shard_n):
            ap.error(f"--shard needs N >= 1 and 0 <= I < N, got {args.shard!r}")

    receipt = RECEIPT if shard_n is None else RECEIPT.with_name(
        f"event_tape_download_receipt_shard{shard_i}of{shard_n}.json")

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
    if shard_n is not None:
        log.info("Shard %d/%d active (handling row indices where idx %% %d == %d)", shard_i, shard_n, shard_n, shard_i)

    processed = empty = skipped = written = 0
    running = 0.0
    per_sym: dict[str, int] = {}
    n = len(ev)

    for idx, row in ev.iterrows():
        if idx < args.start_idx:
            continue
        if shard_n is not None and (idx % shard_n) != shard_i:
            continue
        eid = str(row["event_id"])
        syms = [s.strip() for s in str(row.get("symbols", "")).replace(";", ",").split(",") if s.strip()]
        missing = [s for s in syms if not _present(eid, s)]
        if not missing:
            skipped += 1
            continue
        start = row["start_utc"].to_pydatetime()
        end = row["end_utc"].to_pydatetime()
        dbn = RAW_DIR / f"{eid}_mbo.dbn.zst"
        # Raw tape already on disk from a prior --keep-dbn run: convert straight
        # from it — no re-pricing, no re-payment, and no "file already exists"
        # download error. Recovers gap events whose NPZ is missing but whose
        # .dbn was kept.
        reused = dbn.is_file() and dbn.stat().st_size > SHELL_BYTES
        cost = 0.0
        if reused:
            if args.dry_run:
                continue
            dest = str(dbn)
        else:
            try:
                cost = float(_with_retry(lambda: _call_with_timeout(
                    lambda: client.estimate_cost(symbols=missing, start_utc=start, end_utc=end),
                    PRICE_TIMEOUT_S)))
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
            try:
                dest = _with_retry(lambda: _call_with_timeout(
                    lambda: client.download_event_window(
                        event_id=eid, symbols=missing, start_utc=start, end_utc=end,
                        output_path=str(dbn), override_operating_cap=True, override_hard_limit=False),
                    DOWNLOAD_TIMEOUT_S))
            except Exception as exc:
                # Raw .dbn already on disk (prior run / concurrent shard write):
                # convert it instead of failing — the data is already paid for.
                if dbn.is_file() and dbn.stat().st_size > SHELL_BYTES:
                    dest = str(dbn)
                    reused = True
                    cost = 0.0
                    log.info("[%d/%d] %s download blocked (%s) -> converting kept .dbn",
                             idx + 1, n, eid, str(exc).splitlines()[0][:60])
                else:
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
        log.info("[%d/%d] %s %scost=$%.4f conv=%s running=$%.2f", idx + 1, n, eid,
                 "REUSED-dbn " if reused else "", cost, conv or "[]", running)
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
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
        print(f"  receipt: {receipt}")
    print("=" * 70)


if __name__ == "__main__":
    main()
