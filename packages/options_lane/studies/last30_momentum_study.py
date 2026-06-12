"""Baltussen et al. last-30-minutes ES momentum revalidation harness.

Study definitions
-----------------
Gate question (WS-1.3): does the first-part-of-day return (08:30–14:30 CT)
predict the last-30-minute return (14:30–15:00 CT) for ES futures, net of
approximately 1.3 ticks round-trip cost, over the period 2021–2026?
Fail → dead permanently.

Signal window : 08:30:00 CT → 14:30:00 CT
    Cumulative log return from the last trade AT OR BEFORE 08:30 CT to the
    last trade AT OR BEFORE 14:30 CT.  Forward-fill semantics: if no trade
    exists at the exact boundary, use the last trade that occurred before it.
    NO lookahead — a trade with local_ts == 14:30:00 CT belongs to the signal
    window, not the target window.

Target window : 14:30:00 CT → 15:00:00 CT
    Log return from the last trade AT OR BEFORE 14:30 CT (== signal end price)
    to the last trade AT OR BEFORE 15:00 CT.
    A trade at exactly 14:30 CT counts as the signal-end price (already used
    above); the target window starts from the NEXT trade after 14:30 CT.
    The last trade in [14:30:01 ns, 15:00:00 CT] is the target-end price.

Strategy : sign(signal_ret) position over the target window.
    PnL in ES ticks (1 tick = 0.25 index points).
    Gross ticks = direction * (target_end_px - signal_end_px) / 0.25
    Net ticks   = gross_ticks - cost_ticks  (default round-trip 1.3 ticks)

Per-file output fields
----------------------
date, symbol, signal_ret, target_ret, gross_ticks, net_ticks,
n_trades_signal_window, n_trades_target_window,
has_signal_coverage, has_target_coverage

Aggregation (measure subcommand)
---------------------------------
hit_rate        = fraction of days where net_ticks > 0
mean_net_ticks  = mean of per-day net_ticks
t_stat          = mean_net_ticks / (std_net_ticks / sqrt(n))  [screening number only;
                  real inference goes through the gauntlet — see AGENTS.md]
Split by year.

CRITICAL DATA CAVEAT
--------------------
The NPZ event lake is windowed around macro releases (08:30 ET CPI etc.).
Both the signal window (08:30–14:30 CT) and the target window (14:30–15:00 CT)
must be present in a single file to compute a valid signal–target pair.
Almost no files will cover both.  The inventory subcommand reports coverage
honestly.  Only files where BOTH windows have at least one trade are processed.

Usage
-----
    python -m options_lane.studies.last30_momentum_study inventory [--root PATH] [--out PATH]
    python -m options_lane.studies.last30_momentum_study measure   [--root PATH] [--out PATH]
                                                                   [--cost-ticks FLOAT]

HFT3_NPZ_ROOT env var overrides the default lake root.
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
# Path bootstrap — mirror the pattern in pipeline.py / fixing_window_study.py
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

_ES_TICK_SIZE = 0.25  # ES tick = 0.25 index points
_DEFAULT_COST_TICKS = 1.3  # round-trip default


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
    """Convert a wall-clock time in CT on the given UTC date to Unix ns.

    date_utc only needs a valid .date() component; its time is ignored.
    DST ambiguity is resolved via zoneinfo fold=0 (first occurrence).
    """
    from datetime import date as _date

    d = date_utc.date()
    naive = datetime(d.year, d.month, d.day, h, m, s)
    aware = naive.replace(tzinfo=_TZ_CT)
    return int(aware.timestamp() * 1_000_000_000)


def _momentum_bounds_ns(
    file_date_utc: datetime,
) -> tuple[int, int, int, int]:
    """Return (signal_start, signal_end, target_end, scan_start) in UTC ns.

    signal_start : 08:30:00 CT  (signal window open)
    signal_end   : 14:30:00 CT  (signal window close = target window open)
    target_end   : 15:00:00 CT  (target window close)
    scan_start   : 08:30:00 CT  (= signal_start; coverage check anchor)

    A trade with local_ts == signal_end belongs to the signal window (forward-
    fill, no lookahead).  The target window is (signal_end, target_end].
    """
    signal_start = _ct_hms_to_utc_ns(file_date_utc, 8, 30, 0)
    signal_end = _ct_hms_to_utc_ns(file_date_utc, 14, 30, 0)
    target_end = _ct_hms_to_utc_ns(file_date_utc, 15, 0, 0)
    return signal_start, signal_end, target_end, signal_start


# ---------------------------------------------------------------------------
# NPZ loading helpers — identical to fixing_window_study.py
# ---------------------------------------------------------------------------

def _load_raw(path: Path) -> np.ndarray | None:
    """Load and validate the 'data' structured array from an NPZ file.

    Returns None if the file is absent, malformed, or empty.
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
# Price helpers — no lookahead; boundary-inclusive for signal window
# ---------------------------------------------------------------------------

def _last_trade_at_or_before(raw: np.ndarray, ts_hi: int, ts_lo: int = 0) -> float:
    """Return price of the last trade with ts_lo <= local_ts <= ts_hi.

    Forward-fill semantics: returns the most recent trade price at or before
    the boundary.  Returns nan when no qualifying trade exists.
    Filtration-safe: never reads a row with local_ts > ts_hi.
    """
    best_ts = -1
    best_px = float("nan")
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if ts > ts_hi:
            continue
        if ts < ts_lo:
            continue
        if _is_trade(ev) and ts >= best_ts:
            best_ts = ts
            best_px = float(row["px"])
    return best_px


def _first_trade_after(raw: np.ndarray, ts_lo_exclusive: int, ts_hi: int) -> float:
    """Return price of the first trade with ts_lo_exclusive < local_ts <= ts_hi.

    Returns nan when no qualifying trade exists.

    TODO: not currently used by measure_file; retained for potential future
    intraday-entry study that needs the first target-window trade.
    """
    best_ts = -1
    best_px = float("nan")
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if ts <= ts_lo_exclusive or ts > ts_hi:
            continue
        if _is_trade(ev):
            if best_ts < 0 or ts < best_ts:
                best_ts = ts
                best_px = float(row["px"])
    return best_px


def _count_trades(raw: np.ndarray, ts_lo: int, ts_hi: int, lo_exclusive: bool = False) -> int:
    """Count trade rows with local_ts in the specified range.

    lo_exclusive=True uses (ts_lo, ts_hi]; lo_exclusive=False uses [ts_lo, ts_hi].
    """
    count = 0
    for row in raw:
        ev = int(row["ev"])
        ts = int(row["local_ts"])
        if lo_exclusive:
            if ts <= ts_lo or ts > ts_hi:
                continue
        else:
            if ts < ts_lo or ts > ts_hi:
                continue
        if _is_trade(ev):
            count += 1
    return count


# ---------------------------------------------------------------------------
# Core measurement
# ---------------------------------------------------------------------------

def measure_file(
    raw: np.ndarray,
    symbol: str,
    event_id: str,
    file_date_utc: datetime,
    cost_ticks: float = _DEFAULT_COST_TICKS,
) -> dict[str, Any]:
    """Compute momentum signal and target return for one NPZ file.

    Returns a flat dict suitable for NDJSON output.

    Boundary semantics (no-lookahead, forward-fill):
    - signal_start_px : last trade with local_ts in [scan_start, signal_start]
                        (i.e. at or before 08:30 CT, scanning from the file start)
    - signal_end_px   : last trade with local_ts <= signal_end (14:30 CT)
    - target_end_px   : last trade with local_ts <= target_end (15:00 CT) and
                        local_ts > signal_end  (strictly after 14:30 CT)

    A trade at exactly 14:30:00 CT is counted in the signal window and used as
    signal_end_px.  The target window requires a trade AFTER 14:30:00 CT.

    Coverage flags:
    - has_signal_coverage : True when both signal_start_px and signal_end_px are
                            non-nan AND at least 1 trade exists in the signal window
    - has_target_coverage : True when target_end_px is non-nan AND at least 1 trade
                            exists strictly after signal_end within the target window
    """
    signal_start, signal_end, target_end, scan_start = _momentum_bounds_ns(file_date_utc)

    # Signal window price anchors
    signal_start_px = _last_trade_at_or_before(raw, signal_start)
    signal_end_px = _last_trade_at_or_before(raw, signal_end)

    # Target window: strictly after signal_end
    target_end_px = _last_trade_at_or_before(raw, target_end, ts_lo=signal_end + 1)

    # Trade counts for reporting
    n_trades_signal = _count_trades(raw, signal_start, signal_end)
    n_trades_target = _count_trades(raw, signal_end, target_end, lo_exclusive=True)

    # Coverage flags
    has_signal = (
        not math.isnan(signal_start_px)
        and not math.isnan(signal_end_px)
        and n_trades_signal >= 1
    )
    has_target = not math.isnan(target_end_px) and n_trades_target >= 1

    # Returns and PnL (only when both windows covered)
    signal_ret: float | None = None
    target_ret: float | None = None
    gross_ticks: float | None = None
    net_ticks: float | None = None

    if has_signal and has_target and signal_start_px > 0 and signal_end_px > 0 and target_end_px > 0:
        signal_ret = math.log(signal_end_px / signal_start_px)
        target_ret = math.log(target_end_px / signal_end_px)
        direction = math.copysign(1.0, signal_ret) if signal_ret != 0.0 else 0.0
        # PnL in ticks: position = direction contracts, P&L = direction * (target move in ticks)
        # target move in ticks = (target_end_px - signal_end_px) / tick_size
        target_move_pts = target_end_px - signal_end_px
        gross_ticks = direction * target_move_pts / _ES_TICK_SIZE
        # No cost when direction==0 (no position taken on zero signal)
        net_ticks = gross_ticks - cost_ticks if direction != 0.0 else 0.0

    date_str = file_date_utc.date().isoformat()

    return {
        "date": date_str,
        "symbol": symbol,
        "event_id": event_id,
        "signal_ret": signal_ret,
        "target_ret": target_ret,
        "gross_ticks": gross_ticks,
        "net_ticks": net_ticks,
        "n_trades_signal_window": n_trades_signal,
        "n_trades_target_window": n_trades_target,
        "has_signal_coverage": has_signal,
        "has_target_coverage": has_target,
    }


# ---------------------------------------------------------------------------
# Inventory subcommand
# ---------------------------------------------------------------------------

def _date_from_ts_min(raw: np.ndarray) -> datetime:
    ts_min, _ = _ts_range(raw)
    return datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc)


def run_inventory(
    repo_root: Path,
    out_path: Path | None = None,
) -> dict[str, Any]:
    """Scan the lake manifest and report coverage of BOTH momentum windows.

    A file is marked as covering only when its ts range overlaps BOTH:
        [08:30:00, 14:30:00] CT  (signal window)
        (14:30:00, 15:00:00] CT  (target window)

    Returns a coverage-report dict with:
        files_total          int
        files_covering_both  int  — overlap BOTH windows
        files_covering_signal_only  int
        files_covering_target_only  int
        files_missing        int  — NPZ absent/unreadable
        dates_covered        dict[symbol, list[str]]
        covering_entries     list[dict]
        non_covering_entries list[dict]
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
                    "covers_signal": False,
                    "covers_target": False,
                    "covers_both": False,
                    "ts_min_utc": None,
                    "ts_max_utc": None,
                }
            )
            continue

        ts_min, ts_max = _ts_range(raw)
        file_date_utc = datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc)

        signal_start, signal_end, target_end, _ = _momentum_bounds_ns(file_date_utc)

        # Signal window [08:30, 14:30] overlaps if ts_min <= signal_end AND ts_max >= signal_start
        covers_signal = ts_min <= signal_end and ts_max >= signal_start
        # Target window (14:30, 15:00] overlaps if ts_min < target_end AND ts_max > signal_end
        covers_target = ts_min < target_end and ts_max > signal_end
        covers_both = covers_signal and covers_target

        ts_min_utc_str = datetime.fromtimestamp(ts_min / 1e9, tz=timezone.utc).isoformat()
        ts_max_utc_str = datetime.fromtimestamp(ts_max / 1e9, tz=timezone.utc).isoformat()
        date_str = file_date_utc.date().isoformat()

        record = {
            "symbol": symbol,
            "event_id": event_id,
            "npz_path": str(path),
            "covers_signal": covers_signal,
            "covers_target": covers_target,
            "covers_both": covers_both,
            "date": date_str,
            "ts_min_utc": ts_min_utc_str,
            "ts_max_utc": ts_max_utc_str,
        }
        if covers_both:
            covering.append(record)
        else:
            reason = (
                "signal_only" if covers_signal and not covers_target
                else "target_only" if covers_target and not covers_signal
                else "outside_both_windows"
            )
            non_covering.append({**record, "reason": reason})

    # Per-symbol date lists for files covering both windows
    dates_covered: dict[str, list[str]] = {}
    for r in covering:
        sym = r["symbol"]
        d = r["date"]
        dates_covered.setdefault(sym, [])
        if d not in dates_covered[sym]:
            dates_covered[sym].append(d)

    signal_only = sum(
        1 for r in non_covering
        if r.get("reason") == "signal_only"
    )
    target_only = sum(
        1 for r in non_covering
        if r.get("reason") == "target_only"
    )

    report: dict[str, Any] = {
        "files_total": total,
        "files_covering_both": len(covering),
        "files_covering_signal_only": signal_only,
        "files_covering_target_only": target_only,
        "files_missing": missing,
        "files_non_covering": total - len(covering),
        "dates_covered": dates_covered,
        "covering_entries": covering,
        "non_covering_entries": non_covering,
    }

    # Human-readable table
    print(f"{'Symbol':<20} {'EventID':<35} {'Both':>5} {'Sig':>4} {'Tgt':>4}  {'Date':<12}  ts_range")
    print("-" * 110)
    for r in covering + non_covering:
        both = "YES" if r["covers_both"] else "no"
        sig = "Y" if r["covers_signal"] else "n"
        tgt = "Y" if r["covers_target"] else "n"
        date_s = r.get("date", "?")
        ts_min_s = r.get("ts_min_utc", "?") or "?"
        ts_max_s = r.get("ts_max_utc", "?") or "?"
        reason = f"  [{r['reason']}]" if "reason" in r else ""
        print(
            f"{r['symbol']:<20} {r['event_id']:<35} {both:>5} {sig:>4} {tgt:>4}"
            f"  {date_s:<12}  {ts_min_s[:19]} .. {ts_max_s[:19]}{reason}"
        )
    print("-" * 110)
    print(
        f"Total: {total}  Covering both: {len(covering)}  "
        f"Signal-only: {signal_only}  Target-only: {target_only}  Missing NPZ: {missing}"
    )

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nInventory written to: {out_path}")

    return report


# ---------------------------------------------------------------------------
# Measure subcommand
# ---------------------------------------------------------------------------

def _aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute aggregate momentum statistics from per-day measurement records.

    Only records with both has_signal_coverage and has_target_coverage True
    contribute to aggregate stats.

    Returns:
        n_days          int   total eligible trading days
        hit_rate        float fraction of days with net_ticks > 0
        mean_net_ticks  float mean of per-day net_ticks
        t_stat          float screening t-statistic (sqrt(n) scaled; not inference-grade)
        by_year         dict[str, dict]  same keys split by calendar year
    """
    eligible = [
        r for r in records
        if r.get("has_signal_coverage") and r.get("has_target_coverage")
        and r.get("net_ticks") is not None
    ]

    def _stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
        n = len(rows)
        if n == 0:
            return {"n_days": 0, "hit_rate": None, "mean_net_ticks": None, "t_stat": None}
        net = [r["net_ticks"] for r in rows]
        mean = sum(net) / n
        hits = sum(1 for v in net if v > 0)
        hit_rate = hits / n
        if n > 1:
            variance = sum((v - mean) ** 2 for v in net) / (n - 1)
            std = math.sqrt(variance) if variance > 0 else 0.0
            t = (mean / (std / math.sqrt(n))) if std > 0 else float("nan")
        else:
            t = float("nan")
        return {
            "n_days": n,
            "hit_rate": hit_rate,
            "mean_net_ticks": mean,
            "t_stat": None if math.isnan(t) else t,
        }

    by_year: dict[str, dict[str, Any]] = {}
    for r in eligible:
        year = r["date"][:4]
        by_year.setdefault(year, []).append(r)

    return {
        **_stats(eligible),
        "by_year": {yr: _stats(rows) for yr, rows in sorted(by_year.items())},
    }


def run_measure(
    repo_root: Path,
    out_path: Path | None = None,
    cost_ticks: float = _DEFAULT_COST_TICKS,
) -> list[dict[str, Any]]:
    """Measure momentum returns for all NPZ files covering both windows.

    Only files confirmed to overlap BOTH [08:30, 14:30] CT and (14:30, 15:00]
    CT are processed.  Results are written as NDJSON; aggregate stats appended
    as a final JSON summary comment line.
    """
    inventory = run_inventory(repo_root, out_path=None)
    covering = inventory["covering_entries"]

    if not covering:
        print("No NPZ files cover both momentum windows — nothing to measure.")
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

        rec = measure_file(
            raw, entry["symbol"], entry["event_id"], file_date_utc, cost_ticks=cost_ticks
        )
        results.append(rec)
        print(json.dumps(rec))

    agg = _aggregate(results)
    print("\n# Aggregate statistics:")
    print(json.dumps(agg, indent=2))

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec) + "\n")
            fh.write("# " + json.dumps(agg) + "\n")
        print(f"\nMeasurements written to: {out_path}")

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _default_out(subcommand: str, repo_root: Path) -> Path:
    """Default output path — always under research_cards/last30_momentum/."""
    stamp = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    filename = f"last30_momentum_{subcommand}_{stamp}.json"
    return repo_root / "research_cards" / "last30_momentum" / filename


def _resolve_root(args: argparse.Namespace) -> Path:
    if args.root:
        return Path(args.root)
    return npz_root(_REPO_ROOT)


def _cmd_inventory(args: argparse.Namespace) -> int:
    out_path = Path(args.out) if args.out else _default_out("inventory", _REPO_ROOT)
    run_inventory(_REPO_ROOT, out_path=out_path)
    return 0


def _cmd_measure(args: argparse.Namespace) -> int:
    cost = getattr(args, "cost_ticks", _DEFAULT_COST_TICKS)
    out_path = Path(args.out) if args.out else _default_out("measure", _REPO_ROOT)
    run_measure(_REPO_ROOT, out_path=out_path, cost_ticks=cost)
    return 0


# ---------------------------------------------------------------------------
# OHLCV path — measure-ohlcv subcommand
# ---------------------------------------------------------------------------

_DEFAULT_OHLCV_PATH = Path(
    r"C:\hft3-lake\options\ohlcv\ES_v0_ohlcv1m_20210101_20260612.dbn.zst"
)

# Bar OPEN timestamp convention (verified from databento_dbn 0.79.0):
# OHLCVMsg.ts_event = bar OPEN time in UTC ns.
# A bar with ts_event=T opens at T and closes at T+60s.
# Therefore:
#   signal boundary 08:30 CT: bar ts_event=08:29 CT (opens 08:29, closes 08:30)
#   signal boundary 14:30 CT: bar ts_event=14:29 CT (opens 14:29, closes 14:30)
#   target boundary 15:00 CT: bar ts_event=14:59 CT (opens 14:59, closes 15:00)
#
# The close of the 14:29 CT bar == last trade at or before 14:30 CT.
# This matches the NPZ forward-fill convention (last trade AT OR BEFORE boundary).
# The close of the 08:29 CT bar == last trade at or before 08:30 CT.
# The close of the 14:59 CT bar == last trade at or before 15:00 CT.
# No off-by-one: selecting bar by OPEN time, reading its close gives the
# correct boundary price consistent with forward-fill semantics.

_SIGNAL_OPEN_BAR_H = 8   # bar ts_event = 08:29 CT
_SIGNAL_OPEN_BAR_M = 29
_SIGNAL_END_BAR_H = 14   # bar ts_event = 14:29 CT
_SIGNAL_END_BAR_M = 29
_TARGET_END_BAR_H = 14   # bar ts_event = 14:59 CT
_TARGET_END_BAR_M = 59


def _split_bars_by_day(
    ts_ns: np.ndarray,
    start_date_str: str | None = None,
    end_date_str: str | None = None,
) -> dict[str, tuple[int, int]]:
    """Split bar array indices by CME trading day (date of bar in CT).

    Returns a dict mapping date string (ISO) -> (lo_idx, hi_idx) where
    ts_ns[lo_idx:hi_idx] contains all bars whose CT date equals that date.
    Bars are assigned to CT date by converting ts_ns to America/Chicago.

    start_date_str / end_date_str are inclusive ISO date filters (CT date).
    None means no bound on that side.
    """
    from datetime import date as _date

    if len(ts_ns) == 0:
        return {}

    # Vectorised conversion: ts_ns (UTC) -> CT wall-clock dates
    # We compute UTC offset (in hours) for each bar using DST rules.
    # Strategy: convert each ts_ns to a Python datetime in CT to get CT date.
    # For 1.9M bars this loops; use numpy for the UTC->seconds part then
    # batch-convert to avoid per-row Python datetime overhead.
    seconds = ts_ns / 1_000_000_000  # float64 UTC seconds
    # For DST determination we need to call into zoneinfo per unique date.
    # The bar array is sorted by ts_event so we can batch by day boundary changes.

    # Map each element to its CT date string using vectorised approach:
    # Convert to pandas for tz-aware grouping (pandas already imported via databento,
    # but we should not assume it's always there). Use a lightweight manual approach.
    #
    # We exploit that for any bar, its UTC hour is between 0 and 23 (minute bar),
    # and CT is UTC-5 (CDT) or UTC-6 (CST).  We compute the floor(day) boundary
    # changes by checking DST offset only when the UTC hour crosses midnight±7h.

    _tz = _TZ_CT
    prev_date_str = None
    day_starts: list[tuple[str, int]] = []  # (date_str, start_idx)

    for i, ts in enumerate(ts_ns):
        # Convert ts_ns to CT wall-clock date
        dt_utc = datetime.fromtimestamp(float(ts) / 1_000_000_000, tz=timezone.utc)
        dt_ct = dt_utc.astimezone(_tz)
        ds = dt_ct.date().isoformat()
        if ds != prev_date_str:
            day_starts.append((ds, i))
            prev_date_str = ds

    # Build ranges
    result: dict[str, tuple[int, int]] = {}
    n = len(ts_ns)
    for k, (ds, lo) in enumerate(day_starts):
        hi = day_starts[k + 1][1] if k + 1 < len(day_starts) else n
        # Apply date filter
        if start_date_str is not None and ds < start_date_str:
            continue
        if end_date_str is not None and ds > end_date_str:
            continue
        result[ds] = (lo, hi)

    return result


def _find_bar_close(
    ts_ns: np.ndarray,
    close: np.ndarray,
    lo_idx: int,
    hi_idx: int,
    bar_h: int,
    bar_m: int,
    date_str: str,
) -> float:
    """Return close price of the bar with ts_event == (bar_h, bar_m) CT on date_str.

    Returns nan when the bar is absent (holiday, half-day, gap).

    The bar is found by exact match of CT hour/minute on ts_ns values in
    ts_ns[lo_idx:hi_idx].  At most one bar should match per minute.
    """
    from datetime import date as _date

    _tz = _TZ_CT
    y, mo, d = map(int, date_str.split("-"))
    naive_target = datetime(y, mo, d, bar_h, bar_m, 0)
    aware_target = naive_target.replace(tzinfo=_tz)
    target_ns = int(aware_target.timestamp() * 1_000_000_000)

    for i in range(lo_idx, hi_idx):
        if ts_ns[i] == target_ns:
            return float(close[i])

    return float("nan")


def measure_day_ohlcv(
    ts_ns: np.ndarray,
    close: np.ndarray,
    date_str: str,
    lo_idx: int,
    hi_idx: int,
    cost_ticks: float = _DEFAULT_COST_TICKS,
) -> dict[str, Any]:
    """Compute momentum signal and target return for one trading day from bar arrays.

    Bar selection (verified ts_event = bar OPEN time convention):
      signal_open_px : close of bar ts_event=08:29 CT (bar closes at 08:30 CT)
      signal_end_px  : close of bar ts_event=14:29 CT (bar closes at 14:30 CT)
      target_end_px  : close of bar ts_event=14:59 CT (bar closes at 15:00 CT)

    signal_ret = log(signal_end_px / signal_open_px)
    direction  = sign(signal_ret); 0 when signal_ret == 0
    gross_ticks = direction * (target_end_px - signal_end_px) / 0.25
    net_ticks   = gross_ticks - cost_ticks  (0 when direction == 0)

    has_signal_coverage: True when both 08:29 and 14:29 bars present
    has_target_coverage: True when 14:59 bar present

    Returns flat dict suitable for NDJSON and _aggregate.
    """
    signal_open_px = _find_bar_close(
        ts_ns, close, lo_idx, hi_idx, _SIGNAL_OPEN_BAR_H, _SIGNAL_OPEN_BAR_M, date_str
    )
    signal_end_px = _find_bar_close(
        ts_ns, close, lo_idx, hi_idx, _SIGNAL_END_BAR_H, _SIGNAL_END_BAR_M, date_str
    )
    target_end_px = _find_bar_close(
        ts_ns, close, lo_idx, hi_idx, _TARGET_END_BAR_H, _TARGET_END_BAR_M, date_str
    )

    has_signal = not (math.isnan(signal_open_px) or math.isnan(signal_end_px))
    has_target = not math.isnan(target_end_px)

    signal_ret: float | None = None
    gross_ticks: float | None = None
    net_ticks: float | None = None

    if has_signal and has_target and signal_open_px > 0 and signal_end_px > 0 and target_end_px > 0:
        signal_ret = math.log(signal_end_px / signal_open_px)
        direction = math.copysign(1.0, signal_ret) if signal_ret != 0.0 else 0.0
        gross_ticks = direction * (target_end_px - signal_end_px) / _ES_TICK_SIZE
        net_ticks = gross_ticks - cost_ticks if direction != 0.0 else 0.0

    return {
        "date": date_str,
        "symbol": "ES.v.0",
        "signal_open_px": None if math.isnan(signal_open_px) else signal_open_px,
        "signal_end_px": None if math.isnan(signal_end_px) else signal_end_px,
        "target_end_px": None if math.isnan(target_end_px) else target_end_px,
        "signal_ret": signal_ret,
        "gross_ticks": gross_ticks,
        "net_ticks": net_ticks,
        "has_signal_coverage": has_signal,
        "has_target_coverage": has_target,
    }


def run_measure_ohlcv(
    dbn_file: Path,
    out_path: Path | None = None,
    cost_ticks: float = _DEFAULT_COST_TICKS,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Measure last-30-min momentum from the continuous ohlcv-1m DBN file.

    Parameters
    ----------
    dbn_file    : path to the ohlcv-1m .dbn.zst file
    out_path    : NDJSON output path (research_cards/last30_momentum/); summary JSON appended
    cost_ticks  : round-trip cost default 1.3
    start_date  : ISO date string inclusive filter on CT date (e.g. '2024-01-01')
    end_date    : ISO date string inclusive filter on CT date (e.g. '2024-03-31')

    Returns summary dict with keys: n_days, skipped_days, records, aggregate.
    """
    from options_lane.studies.dbn_ohlcv import load_ohlcv_from_dbn

    print(f"Loading OHLCV bars from: {dbn_file}")
    arrays = load_ohlcv_from_dbn(dbn_file)
    ts_ns = arrays["ts_ns"]
    close = arrays["close"]
    print(f"  Loaded {len(ts_ns):,} bars")

    day_ranges = _split_bars_by_day(ts_ns, start_date, end_date)
    print(f"  Trading days in range: {len(day_ranges)}")

    results: list[dict[str, Any]] = []
    skipped = 0

    for date_str, (lo, hi) in sorted(day_ranges.items()):
        rec = measure_day_ohlcv(ts_ns, close, date_str, lo, hi, cost_ticks=cost_ticks)
        if not rec["has_signal_coverage"] or not rec["has_target_coverage"]:
            skipped += 1
        results.append(rec)
        print(json.dumps(rec))

    agg = _aggregate(results)
    summary = {
        "source": str(dbn_file),
        "start_date": start_date,
        "end_date": end_date,
        "cost_ticks": cost_ticks,
        "n_days_total": len(results),
        "skipped_days": skipped,
        "aggregate": agg,
    }

    print(f"\n# Skipped days (missing boundary bar): {skipped}")
    print("\n# Aggregate statistics:")
    print(json.dumps(agg, indent=2))

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with out_path.open("w", encoding="utf-8") as fh:
            for rec in results:
                fh.write(json.dumps(rec) + "\n")
            fh.write("# " + json.dumps(summary) + "\n")
        print(f"\nMeasurements written to: {out_path}")

    return summary


def _cmd_measure_ohlcv(args: argparse.Namespace) -> int:
    dbn_file = Path(args.dbn_file)
    cost = getattr(args, "cost_ticks", _DEFAULT_COST_TICKS)
    start = getattr(args, "start", None)
    end = getattr(args, "end", None)
    out_path = Path(args.out) if args.out else _default_out("measure_ohlcv", _REPO_ROOT)
    run_measure_ohlcv(dbn_file, out_path=out_path, cost_ticks=cost, start_date=start, end_date=end)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="options_lane.studies.last30_momentum_study",
        description=(
            "Baltussen et al. last-30-min ES momentum revalidation harness. "
            "Gate: does 08:30-14:30 CT return predict 14:30-15:00 CT return "
            "net of ~1.3 ticks RT cost? Fail -> dead permanently. "
            "NOTE: almost no NPZ files cover both windows -- run 'inventory' first."
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
        help="Output path. Default: research_cards/last30_momentum/<timestamp>.json",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_inv = sub.add_parser(
        "inventory",
        help="Scan manifest; report which files cover BOTH 08:30-14:30 and 14:30-15:00 CT",
    )
    p_inv.set_defaults(func=_cmd_inventory)

    p_meas = sub.add_parser(
        "measure",
        help="Compute momentum signal/target returns for all dual-covering files",
    )
    p_meas.add_argument(
        "--cost-ticks",
        type=float,
        default=_DEFAULT_COST_TICKS,
        help=f"Round-trip cost in ES ticks (default: {_DEFAULT_COST_TICKS})",
    )
    p_meas.set_defaults(func=_cmd_measure)

    p_ohlcv = sub.add_parser(
        "measure-ohlcv",
        help="Compute momentum returns from continuous ohlcv-1m DBN file",
    )
    p_ohlcv.add_argument(
        "--dbn-file",
        default=str(_DEFAULT_OHLCV_PATH),
        help="Path to ohlcv-1m .dbn.zst file",
    )
    p_ohlcv.add_argument(
        "--cost-ticks",
        type=float,
        default=_DEFAULT_COST_TICKS,
        help=f"Round-trip cost in ES ticks (default: {_DEFAULT_COST_TICKS})",
    )
    p_ohlcv.add_argument(
        "--start",
        default=None,
        help="Start date inclusive, ISO format (CT date), e.g. 2024-01-01",
    )
    p_ohlcv.add_argument(
        "--end",
        default=None,
        help="End date inclusive, ISO format (CT date), e.g. 2024-03-31",
    )
    p_ohlcv.set_defaults(func=_cmd_measure_ohlcv)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
