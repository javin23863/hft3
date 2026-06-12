"""ES options fixing-window measurement study.

The CME European daily/weekly ES options settle via a 30-second VWAP captured
between 14:59:30 and 15:00:00 CT (20:00/21:00 UTC, DST-aware via
America/Chicago zoneinfo).

CRITICAL DATA CAVEAT
--------------------
The NPZ event lake (data/npz, manifest.json) is event-windowed around *macro
releases* (CPI 8:30 ET, etc.).  Most files will NOT overlap the 15:00 CT
fixing window.  The ``inventory`` subcommand checks coverage explicitly and
reports which files actually contain data in 14:55:00–15:05:00 CT; never
assume coverage.

Usage
-----
    python -m options_lane.studies.fixing_window_study inventory   [--root PATH] [--out PATH]
    python -m options_lane.studies.fixing_window_study measure     [--root PATH] [--out PATH]
    python -m options_lane.studies.fixing_window_study measure-dbn [--dbn-dir PATH]
                                                                    [--out PATH]
                                                                    [--max-files N]

HFT3_NPZ_ROOT env var overrides the default lake root (see npz_resolver.py).
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

# ---------------------------------------------------------------------------
# Path bootstrap — mirror the pattern in pipeline.py
# ---------------------------------------------------------------------------
_PKG_ROOT = Path(__file__).resolve().parents[2]  # packages/
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

_REPO_ROOT = _PKG_ROOT.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data_system.src.lake_manifest import load_manifest, resolve_npz_path
from data_system.src.npz_resolver import npz_root

# ---------------------------------------------------------------------------
# hftbacktest event-flag constants
# Source of truth: scripts/verify_cpp_parity.py comment
# "Values from hftbacktest/types.py — do NOT change without cross-checking"
#
#   TRADE_EVENT = 2           low byte of ev for trade executions
#   FILL_EVENT  = 13          low byte for fill events (also treated as trades)
#   BUY_EVENT   = 1 << 29    high-bit flag: resting side is BID  (→ sell aggressor)
#   SELL_EVENT  = 1 << 28    high-bit flag: resting side is ASK  (→ buy aggressor)
#
# ev layout: low byte encodes event type (compared via ev & 0xFF);
#            high bits carry side flags.
#
# Side → aggressor convention (from npz_feed._event_side + mbo_features):
#   ev & BUY_EVENT  → side "B" (bid resting) → *sell* aggressor (sign = -1)
#   ev & SELL_EVENT → side "A" (ask resting) → *buy*  aggressor (sign = +1)
# ---------------------------------------------------------------------------
_TRADE_EVENT_BASE = 2
_FILL_EVENT_BASE = 13
_BUY_FLAG = 1 << 29   # resting bid — sell aggressor
_SELL_FLAG = 1 << 28  # resting ask — buy aggressor
_LOW_BYTE_MASK = 0xFF


def _is_trade(ev: int) -> bool:
    """Return True when the low byte of ev signals a trade or fill execution."""
    base = int(ev) & _LOW_BYTE_MASK
    return base == _TRADE_EVENT_BASE or base == _FILL_EVENT_BASE


def _is_buy_aggressor(ev: int) -> bool:
    """True when the aggressive side is a buyer (trade lifted the ask; SELL_FLAG set)."""
    return bool(int(ev) & _SELL_FLAG)


def _is_sell_aggressor(ev: int) -> bool:
    """True when the aggressive side is a seller (trade hit the bid; BUY_FLAG set)."""
    return bool(int(ev) & _BUY_FLAG)


# ---------------------------------------------------------------------------
# Time helpers — DST-aware via zoneinfo
# ---------------------------------------------------------------------------
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

_TZ_CT = ZoneInfo("America/Chicago")


def _ct_hms_to_utc_ns(date_utc: datetime, h: int, m: int, s: int) -> int:
    """Convert a wall-clock time in CT on the given UTC date to a Unix timestamp in nanoseconds.

    ``date_utc`` only needs a valid .date() component; its time component is
    ignored.  The resulting datetime is folded correctly for DST ambiguity.
    """
    from datetime import date as _date

    d = date_utc.date()
    naive = datetime(d.year, d.month, d.day, h, m, s)
    aware = naive.replace(tzinfo=_TZ_CT)
    return int(aware.timestamp() * 1_000_000_000)


def _window_bounds_ns(date_utc: datetime) -> tuple[int, int, int, int, int, int]:
    """Return (scan_start, fix_start, fix_end, markout_30s, markout_2m, markout_5m) in UTC ns.

    scan window:   14:55:00–15:05:00 CT
    fix window:    14:59:30–15:00:00 CT
    markout_30s:   fix_end + 30s
    markout_2m:    fix_end + 2min
    markout_5m:    15:05:00 CT (= scan_end)
    """
    scan_start = _ct_hms_to_utc_ns(date_utc, 14, 55, 0)
    fix_start = _ct_hms_to_utc_ns(date_utc, 14, 59, 30)
    fix_end = _ct_hms_to_utc_ns(date_utc, 15, 0, 0)
    scan_end = _ct_hms_to_utc_ns(date_utc, 15, 5, 0)
    markout_5m = _ct_hms_to_utc_ns(date_utc, 15, 5, 0)  # same as scan_end for 5-min markout
    markout_30s = fix_end + 30 * 1_000_000_000
    markout_2m = fix_end + 120 * 1_000_000_000
    return scan_start, fix_start, fix_end, markout_30s, markout_2m, markout_5m


# ---------------------------------------------------------------------------
# OI conditioning stub — WS-0.4a statistics backfill pending
# ---------------------------------------------------------------------------

def load_expiry_oi(date: datetime) -> float | None:  # noqa: D401
    """Return open-interest for the front ES option expiry on *date*, or None.

    TODO: WS-0.4a statistics backfill pending — this function always returns
    None until the OI time-series is available.  Imbalance records carry an
    ``oi`` field set to None for later joining once data is backfilled.
    """
    return None  # pragma: no cover — stub intentionally returns None


# ---------------------------------------------------------------------------
# NPZ loading helpers
# ---------------------------------------------------------------------------

def _load_raw(path: Path) -> np.ndarray | None:
    """Load and validate the 'data' structured array from an NPZ file.

    Returns None if the file is absent, malformed, or empty — callers treat
    None as "skip this file" rather than crashing.
    """
    if not path.is_file():
        return None
    try:
        with np.load(str(path)) as arc:
            if "data" not in arc:
                return None
            raw = arc["data"]
        if raw.dtype.names is None:
            return None
        required = {"ev", "local_ts", "px", "qty"}
        if required - set(raw.dtype.names):
            return None
        if len(raw) == 0:
            return None
        return raw
    except Exception:
        return None


def _ts_range(raw: np.ndarray) -> tuple[int, int]:
    """Return (min_local_ts, max_local_ts) for a raw structured array."""
    ts = raw["local_ts"]
    return int(ts.min()), int(ts.max())


# ---------------------------------------------------------------------------
# Inventory subcommand
# ---------------------------------------------------------------------------

def _date_from_manifest_entry(entry: dict[str, Any]) -> datetime | None:
    """Best-effort extract a UTC date from a manifest record's created_utc field."""
    created = entry.get("created_utc", "")
    if not created:
        return None
    try:
        return datetime.fromisoformat(created.replace("Z", "+00:00"))
    except ValueError:
        return None


def run_inventory(repo_root: Path, out_path: Path | None = None) -> dict[str, Any]:
    """Scan the lake manifest and report fixing-window coverage.

    For each manifest entry the NPZ is opened to read its local_ts range.
    Files whose ts range overlaps [14:55:00, 15:05:00] CT on the inferred
    event date are marked as covering the fixing window.

    NOTE: The manifest's ``created_utc`` is used only to identify the calendar
    date for DST-aware conversion.  If created_utc is absent or unparseable
    the entry is tested against the ts range directly using a heuristic UTC
    offset (both -5h and -6h) to be conservative.

    Returns a coverage-report dict with the following top-level keys:
        files_total          int — manifest entries examined
        files_covering       int — entries whose ts range overlaps the window
        files_missing        int — entries where the NPZ was absent/unreadable
        dates_covered        dict[symbol, list[str]] — ISO dates per symbol
        covering_entries     list[dict] — full detail for covering files
        non_covering_entries list[dict] — full detail for non-covering files
    """
    manifest = load_manifest(repo_root)
    total = len(manifest)
    covering: list[dict[str, Any]] = []
    non_covering: list[dict[str, Any]] = []
    missing = 0

    for entry in manifest:
        symbol = entry.get("symbol", "")
        event_id = entry.get("event_id", "")
        npz_path_str = entry.get("npz_path", "")
        path = resolve_npz_path(repo_root, npz_path_str) if npz_path_str else Path("")

        raw = _load_raw(path)
        if raw is None:
            missing += 1
            non_covering.append(
                {
                    "symbol": symbol,
                    "event_id": event_id,
                    "npz_path": str(path),
                    "reason": "missing_or_unreadable",
                    "covers_window": False,
                    "ts_min_utc": None,
                    "ts_max_utc": None,
                }
            )
            continue

        ts_min, ts_max = _ts_range(raw)

        # Determine the calendar date for DST-aware window bounds.
        # We derive it from the ts_min of the data (UTC epoch ns → date).
        file_date_utc = datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc)

        # Compute scan-window bounds for this date
        # bounds = (scan_start, fix_start, fix_end, markout_30s, markout_2m, markout_5m)
        bounds = _window_bounds_ns(file_date_utc)
        scan_start, scan_end = bounds[0], bounds[5]  # scan_end = markout_5m = 15:05 CT

        overlaps = ts_min <= scan_end and ts_max >= scan_start

        ts_min_utc_str = datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc).isoformat()
        ts_max_utc_str = datetime.fromtimestamp(ts_max / 1e9, tz=timezone.utc).isoformat()
        date_str = file_date_utc.date().isoformat()

        record = {
            "symbol": symbol,
            "event_id": event_id,
            "npz_path": str(path),
            "covers_window": overlaps,
            "date": date_str,
            "ts_min_utc": ts_min_utc_str,
            "ts_max_utc": ts_max_utc_str,
        }
        if overlaps:
            covering.append(record)
        else:
            non_covering.append({**record, "reason": "outside_window"})

    # Build per-symbol date lists
    dates_covered: dict[str, list[str]] = {}
    for r in covering:
        sym = r["symbol"]
        d = r["date"]
        dates_covered.setdefault(sym, [])
        if d not in dates_covered[sym]:
            dates_covered[sym].append(d)

    report: dict[str, Any] = {
        "files_total": total,
        "files_covering": len(covering),
        "files_missing": missing,
        "files_non_covering": total - len(covering),
        "dates_covered": dates_covered,
        "covering_entries": covering,
        "non_covering_entries": non_covering,
    }

    # Print human-readable table
    print(f"{'Symbol':<20} {'EventID':<35} {'Covers':>6}  {'Date':<12}  ts_range")
    print("-" * 100)
    for r in covering + non_covering:
        covers = "YES" if r["covers_window"] else "no"
        date_s = r.get("date", "?")
        ts_min_s = r.get("ts_min_utc", "?") or "?"
        ts_max_s = r.get("ts_max_utc", "?") or "?"
        reason = f"  [{r['reason']}]" if "reason" in r else ""
        print(
            f"{r['symbol']:<20} {r['event_id']:<35} {covers:>6}  {date_s:<12}  "
            f"{ts_min_s[:19]} .. {ts_max_s[:19]}{reason}"
        )
    print("-" * 100)
    print(
        f"Total: {total}  Covering: {len(covering)}  "
        f"Non-covering: {len(non_covering) - missing}  Missing NPZ: {missing}"
    )

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nInventory written to: {out_path}")

    return report


# ---------------------------------------------------------------------------
# Measurement subcommand
# ---------------------------------------------------------------------------

def _compute_vwap(raw: np.ndarray, ts_lo: int, ts_hi: int) -> float:
    """Compute trade VWAP for rows where local_ts in [ts_lo, ts_hi].

    Returns nan when there are no qualifying trades.
    Filtration-safe: only rows with local_ts <= ts_hi and local_ts >= ts_lo.
    """
    mask = np.zeros(len(raw), dtype=bool)
    for i, row in enumerate(raw):
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if _is_trade(ev) and ts_lo <= ts <= ts_hi:
            mask[i] = True

    trades = raw[mask]
    if len(trades) == 0:
        return float("nan")

    px = trades["px"].astype(float)
    qty = trades["qty"].astype(float)
    total_qty = qty.sum()
    if total_qty == 0.0:
        return float("nan")
    return float((px * qty).sum() / total_qty)


def _last_trade_price(raw: np.ndarray, ts_lo: int, ts_hi: int) -> float:
    """Return price of the last trade row with local_ts in [ts_lo, ts_hi].

    Returns nan when no qualifying trade exists.
    """
    best_ts = -1
    best_px = float("nan")
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if _is_trade(ev) and ts_lo <= ts <= ts_hi and ts >= best_ts:
            best_ts = ts
            best_px = float(row["px"])
    return best_px


def _first_trade_price(raw: np.ndarray, ts_lo: int, ts_hi: int) -> float:
    """Return price of the first trade row with local_ts in [ts_lo, ts_hi]."""
    best_ts = -1
    best_px = float("nan")
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if _is_trade(ev) and ts_lo <= ts <= ts_hi:
            if best_ts < 0 or ts < best_ts:
                best_ts = ts
                best_px = float(row["px"])
    return best_px


def _compute_imbalance(raw: np.ndarray, ts_lo: int, ts_hi: int) -> tuple[float, int, float]:
    """Compute signed aggressor imbalance in [ts_lo, ts_hi].

    imbalance = sum(sign * qty) / sum(qty)
    where sign=+1 for BUY_EVENT aggressors, sign=-1 for SELL_EVENT.

    Returns (imbalance, trade_count, total_volume).
    nan imbalance when total_volume == 0.
    """
    signed_qty = 0.0
    total_qty = 0.0
    trade_count = 0
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if not _is_trade(ev):
            continue
        if not (ts_lo <= ts <= ts_hi):
            continue
        qty = float(row["qty"])
        trade_count += 1
        total_qty += qty
        if _is_buy_aggressor(ev):
            signed_qty += qty
        elif _is_sell_aggressor(ev):
            signed_qty -= qty
        # If neither flag set, side unknown — treat as zero contribution

    imbalance = signed_qty / total_qty if total_qty > 0 else float("nan")
    return imbalance, trade_count, total_qty


def measure_file(
    raw: np.ndarray,
    symbol: str,
    event_id: str,
    file_date_utc: datetime,
) -> dict[str, Any]:
    """Compute all fixing-window statistics for one NPZ file.

    Returns a flat dict suitable for NDJSON output.

    OI field is always None — stub interface pending WS-0.4a.
    """
    bounds = _window_bounds_ns(file_date_utc)
    scan_start, fix_start, fix_end, mark_30s, mark_2m, mark_5m = bounds

    # --- pre-window mid drift: 14:55 → 14:59:30 ---
    pre_start_px = _first_trade_price(raw, scan_start, fix_start)
    pre_end_px = _last_trade_price(raw, scan_start, fix_start)
    pre_drift = (
        pre_end_px - pre_start_px
        if not (math.isnan(pre_start_px) or math.isnan(pre_end_px))
        else float("nan")
    )

    # --- 30-second VWAP 14:59:30–15:00:00 ---
    vwap_30s = _compute_vwap(raw, fix_start, fix_end)

    # --- window-wide stats 14:55–15:05 ---
    imbalance, trade_count, total_volume = _compute_imbalance(raw, scan_start, mark_5m)

    # --- markouts: VWAP vs last trade at +30s, +2m, +5m ---
    def _markout(ts_end: int) -> float:
        last = _last_trade_price(raw, fix_end, ts_end)
        if math.isnan(last) or math.isnan(vwap_30s):
            return float("nan")
        return last - vwap_30s

    markout_30s = _markout(mark_30s)
    markout_2m = _markout(mark_2m)
    markout_5m_val = _markout(mark_5m)

    date_str = file_date_utc.date().isoformat()

    return {
        "symbol": symbol,
        "event_id": event_id,
        "date": date_str,
        "trade_count_scan": trade_count,
        "volume_scan": total_volume,
        "imbalance_signed": None if math.isnan(imbalance) else imbalance,
        "vwap_30s": None if math.isnan(vwap_30s) else vwap_30s,
        "pre_window_drift": None if math.isnan(pre_drift) else pre_drift,
        "markout_30s": None if math.isnan(markout_30s) else markout_30s,
        "markout_2m": None if math.isnan(markout_2m) else markout_2m,
        "markout_5m": None if math.isnan(markout_5m_val) else markout_5m_val,
        "oi": load_expiry_oi(file_date_utc),  # None — WS-0.4a pending
    }


def run_measure(
    repo_root: Path,
    out_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Measure fixing-window statistics for all covering NPZ files.

    Only files confirmed to overlap [14:55:00, 15:05:00] CT are processed.
    Results are written to ``out_path`` as NDJSON (one JSON object per line).
    """
    # First run inventory to find covering files
    inventory = run_inventory(repo_root, out_path=None)
    covering = inventory["covering_entries"]

    if not covering:
        print("No NPZ files cover the fixing window — nothing to measure.")
        return []

    results: list[dict[str, Any]] = []
    for entry in covering:
        path = Path(entry["npz_path"])
        raw = _load_raw(path)
        if raw is None:
            continue

        ts_min_s = entry.get("ts_min_utc") or ""
        if ts_min_s:
            file_date_utc = datetime.fromisoformat(ts_min_s)
        else:
            ts_min, _ = _ts_range(raw)
            file_date_utc = datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc)

        rec = measure_file(raw, entry["symbol"], entry["event_id"], file_date_utc)
        results.append(rec)
        print(json.dumps(rec))

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec) + "\n")
        print(f"\nMeasurements written to: {out_path}")

    return results


# ---------------------------------------------------------------------------
# Pure-array helpers — operate on plain numpy arrays (DBN path)
#
# These accept the dict produced by dbn_trades.load_trades_from_dbn:
#   ts_ns (int64), price (float64), size (float64), aggressor_sign (int8)
#
# The computation is identical to the NPZ helpers above; only the input shape
# differs (plain arrays instead of structured array fields).
# ---------------------------------------------------------------------------

def _pa_vwap(arrays: dict, ts_lo: int, ts_hi: int) -> float:
    """Volume-weighted average price over trades with ts_ns in [ts_lo, ts_hi]."""
    ts = arrays["ts_ns"]
    mask = (ts >= ts_lo) & (ts <= ts_hi)
    px = arrays["price"][mask]
    sz = arrays["size"][mask]
    total_sz = sz.sum()
    if total_sz == 0.0 or len(px) == 0:
        return float("nan")
    return float((px * sz).sum() / total_sz)


def _pa_last_price(arrays: dict, ts_lo: int, ts_hi: int) -> float:
    """Price of the last trade with ts_ns in [ts_lo, ts_hi]; nan when none."""
    ts = arrays["ts_ns"]
    mask = (ts >= ts_lo) & (ts <= ts_hi)
    if not mask.any():
        return float("nan")
    # argmax on masked ts returns the position of the maximum ts in the window
    masked_ts = ts[mask]
    return float(arrays["price"][mask][masked_ts.argmax()])


def _pa_first_price(arrays: dict, ts_lo: int, ts_hi: int) -> float:
    """Price of the first trade with ts_ns in [ts_lo, ts_hi]; nan when none."""
    ts = arrays["ts_ns"]
    mask = (ts >= ts_lo) & (ts <= ts_hi)
    if not mask.any():
        return float("nan")
    masked_ts = ts[mask]
    return float(arrays["price"][mask][masked_ts.argmin()])


def _pa_imbalance(arrays: dict, ts_lo: int, ts_hi: int) -> tuple[float, int, float]:
    """Signed aggressor imbalance for trades with ts_ns in [ts_lo, ts_hi].

    Returns (imbalance, trade_count, total_volume).
    imbalance = sum(sign * size) / sum(size); nan when total_volume == 0.
    """
    ts = arrays["ts_ns"]
    mask = (ts >= ts_lo) & (ts <= ts_hi)
    sz = arrays["size"][mask]
    sign = arrays["aggressor_sign"][mask].astype(np.float64)
    total_sz = sz.sum()
    trade_count = int(mask.sum())
    if total_sz == 0.0:
        return float("nan"), trade_count, 0.0
    return float((sign * sz).sum() / total_sz), trade_count, float(total_sz)


def measure_file_from_arrays(
    arrays: dict,
    symbol: str,
    date_str: str,
    file_date_utc: datetime,
) -> dict[str, Any]:
    """Compute all fixing-window statistics from plain numpy arrays.

    Identical statistics to measure_file() but consumes the dict produced by
    dbn_trades.load_trades_from_dbn rather than a structured NPZ array.

    Markout boundary handling:
        markout_5m endpoint is exactly 15:05:00 CT (= scan_end = file end).
        If the last trade's ts_ns < endpoint, the last trade's price is used
        (forward-fill semantics, consistent with the NPZ path which uses
        _last_trade_price with ts_hi = mark_5m).
    """
    bounds = _window_bounds_ns(file_date_utc)
    scan_start, fix_start, fix_end, mark_30s, mark_2m, mark_5m = bounds

    pre_start_px = _pa_first_price(arrays, scan_start, fix_start)
    pre_end_px = _pa_last_price(arrays, scan_start, fix_start)
    pre_drift = (
        pre_end_px - pre_start_px
        if not (math.isnan(pre_start_px) or math.isnan(pre_end_px))
        else float("nan")
    )

    vwap_30s = _pa_vwap(arrays, fix_start, fix_end)

    imbalance, trade_count, total_volume = _pa_imbalance(arrays, scan_start, mark_5m)

    def _markout(ts_end: int) -> float:
        last = _pa_last_price(arrays, fix_end, ts_end)
        if math.isnan(last) or math.isnan(vwap_30s):
            return float("nan")
        return last - vwap_30s

    markout_30s = _markout(mark_30s)
    markout_2m = _markout(mark_2m)
    markout_5m_val = _markout(mark_5m)

    return {
        "symbol": symbol,
        "event_id": date_str,  # date string doubles as event_id for DBN records
        "date": date_str,
        "trade_count_scan": trade_count,
        "volume_scan": total_volume,
        "imbalance_signed": None if math.isnan(imbalance) else imbalance,
        "vwap_30s": None if math.isnan(vwap_30s) else vwap_30s,
        "pre_window_drift": None if math.isnan(pre_drift) else pre_drift,
        "markout_30s": None if math.isnan(markout_30s) else markout_30s,
        "markout_2m": None if math.isnan(markout_2m) else markout_2m,
        "markout_5m": None if math.isnan(markout_5m_val) else markout_5m_val,
        "oi": None,  # WS-0.4a pending
        "source": "dbn",
    }


_DBN_DEFAULT_DIR = Path(r"C:\hft3-lake\options\fixing_mbo")


def run_measure_dbn(
    dbn_dir: Path = _DBN_DEFAULT_DIR,
    out_path: Path | None = None,
    max_files: int | None = None,
) -> list[dict[str, Any]]:
    """Measure fixing-window statistics from ES_fixing_<date>.dbn.zst files.

    Scans dbn_dir for files matching ES_fixing_*.dbn.zst, extracts the date
    from the filename, loads trade arrays via dbn_trades.load_trades_from_dbn,
    and computes the same statistics as the NPZ measure path.

    max_files: when set, process at most this many files (for testing).

    Output: NDJSON + summary written to out_path (same schema as NPZ measure).
    """
    from options_lane.studies.dbn_trades import load_trades_from_dbn

    files = sorted(dbn_dir.glob("ES_fixing_*.dbn.zst"))
    if max_files is not None:
        files = files[:max_files]

    if not files:
        print(f"No ES_fixing_*.dbn.zst files found in {dbn_dir}")
        return []

    results: list[dict[str, Any]] = []
    for fpath in files:
        # Derive date from filename: ES_fixing_YYYY-MM-DD.dbn.zst
        stem = fpath.name  # e.g. ES_fixing_2023-05-01.dbn.zst
        # Extract date portion between "ES_fixing_" and ".dbn.zst"
        date_part = stem.removeprefix("ES_fixing_").removesuffix(".dbn.zst")
        try:
            file_date = datetime.strptime(date_part, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            print(f"Skipping {fpath.name}: cannot parse date from filename")
            continue

        arrays = load_trades_from_dbn(fpath)
        if len(arrays["ts_ns"]) == 0:
            print(f"Skipping {fpath.name}: no trade records")
            continue

        rec = measure_file_from_arrays(arrays, "ES.v.0", date_part, file_date)
        results.append(rec)
        print(json.dumps(rec))

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec) + "\n")
        print(f"\nDBN measurements written to: {out_path}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_out(subcommand: str, repo_root: Path) -> Path:
    """Default output path — always under research_cards/, never under data/npz/."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"fixing_window_{subcommand}_{stamp}.json"
    return repo_root / "research_cards" / "fixing_window" / filename


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="options_lane.studies.fixing_window_study",
        description=(
            "ES options fixing-window (15:00 CT 30s VWAP) measurement harness. "
            "NOTE: most NPZ files in the lake will NOT cover this window — "
            "run 'inventory' first."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="Override lake root (default: HFT3_NPZ_ROOT env or <repo>/data/npz)",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output path (JSON for inventory, NDJSON for measure). "
             "Default: research_cards/fixing_window/<timestamp>.json",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser(
        "inventory",
        help="Scan manifest and report which files cover 14:55-15:05 CT",
    )
    p_inv.set_defaults(func=_cmd_inventory)

    p_meas = sub.add_parser(
        "measure",
        help="Compute fixing-window statistics for all covering files",
    )
    p_meas.set_defaults(func=_cmd_measure)

    p_dbn = sub.add_parser(
        "measure-dbn",
        help="Compute fixing-window statistics from DBN MBO files in --dbn-dir",
    )
    p_dbn.add_argument(
        "--dbn-dir",
        default=str(_DBN_DEFAULT_DIR),
        help=f"Directory containing ES_fixing_*.dbn.zst files (default: {_DBN_DEFAULT_DIR})",
    )
    p_dbn.add_argument(
        "--max-files",
        type=int,
        default=None,
        help="Process at most N files (for testing/preview)",
    )
    p_dbn.set_defaults(func=_cmd_measure_dbn)

    args = parser.parse_args(argv)
    return args.func(args)


def _resolve_root(args: argparse.Namespace) -> Path:
    if args.root:
        return Path(args.root)
    return npz_root(_REPO_ROOT)


def _cmd_inventory(args: argparse.Namespace) -> int:
    lake_root = _resolve_root(args)
    # We need repo_root for resolve_npz_path; pass it through
    out_path = Path(args.out) if args.out else _default_out("inventory", _REPO_ROOT)
    run_inventory(_REPO_ROOT, out_path=out_path)
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    out_path = Path(args.out) if args.out else _default_out("measure", _REPO_ROOT)
    run_measure(_REPO_ROOT, out_path=out_path)
    return 0


def _cmd_measure_dbn(args: argparse.Namespace) -> int:
    dbn_dir = Path(args.dbn_dir)
    out_path = Path(args.out) if args.out else _default_out("measure_dbn", _REPO_ROOT)
    max_files = getattr(args, "max_files", None)
    run_measure_dbn(dbn_dir=dbn_dir, out_path=out_path, max_files=max_files)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
