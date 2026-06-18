#!/usr/bin/env python3
"""Full-universe screening runner.

Runs the active hypothesis matrix against every (event_id, symbol, latency_band)
work unit found in the event lake, aggregates results by hypothesis × event_type ×
band, applies Holm and Benjamini-Hochberg multiple-testing correction, and writes
a universe_result.json + universe_report.md research card.

Aggregation method
------------------
BacktestResult exposes per-event aggregates (expectancy, win_rate, etc.) not
per-trade arrays. Per-event expectancy values are pooled across all events in a
(hypothesis, event_type, band) cell. The cell-level mean/win-rate/adverse-selection
are simple arithmetic means of the per-event values; P5 tail is the 5th-percentile
of per-event expectancies (i.e. worst-event tail, not worst-trade tail — this is
documented in both the JSON and the Markdown card).

p-value derivation
------------------
For each (hypothesis, event_type, band) cell with n_events >= 3, a one-sample
two-sided t-test is run via scipy.stats.ttest_1samp on the vector of per-event
expectancies against null_mean=0.  For n_events < 3 the p-value is set to 1.0
(not enough data to test).  The p-values feed MultipleTestingGate.apply_correction
with method="holm" and method="benjamini_hochberg" separately.

Pool / spawn safety
-------------------
Worker function (_worker) is defined at module top level (picklable).  Only
plain Python scalars, strings, and floats are passed as arguments.  numpy arrays
from the NPZ are not transmitted across the pool boundary; the worker loads them
itself from the path string.  The Pool uses spawn context on all platforms
(explicit via mp.get_context("spawn")) so Windows fork-emulation edge cases are
avoided.  workers=1 forces sequential execution in tests without spawning child
processes.
"""
from __future__ import annotations

import os

# Spawn workers re-import this module before Pool initializer runs; cap BLAS/OpenMP
# threads here so numpy/scipy never see default multi-threaded backends.
for _blas_thread_var in (
    "OPENBLAS_NUM_THREADS",
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "NUMEXPR_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "BLIS_NUM_THREADS",
):
    os.environ.setdefault(_blas_thread_var, "1")

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.hft_backtest_builder import LATENCY_BANDS_MS
from backtest_pipeline.src.chi404_latency import DEFAULT_CHI404_SUMMARY, resolve_order_ack_ms
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from replay.replay_clock import deterministic_run_id

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
DEFAULT_NPZ_DIR = _REPO / "data" / "npz"
Q001_MBO_PILOT_MANIFEST = (
    _REPO / "packages" / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"
)
DEFAULT_SYMBOL = "MES.v.0"
NPZ_PATTERN = re.compile(r"^(?P<symbol>.+?)_(?P<event_id>.+)_mbo\.npz$")
RESEARCH_EMBARGO_START = "2026-01-01"  # ALPHA_CME.md §4 / DEPLOYMENT.md §4.2: research sweeps must never read data >= this date; first 2026 touch is the M9 paper-shadow bundle.
CHECKPOINT_SOURCE_GLOBS = (
    "scripts/run_event_universe.py",
    "packages/backtest_pipeline/src/**/*.py",
    "packages/execution/**/*.py",
    "packages/features_engine/src/**/*.py",
    "packages/replay/**/*.py",
    "packages/trade_manager/**/*.py",
)
CHECKPOINT_SKIP_REASONS = frozenset({"empty_npz"})
CHECKPOINT_ENV_KEYS = (
    "HFT3_CROSS_ASSET",
    "HFT3_FEATURE_BACKEND",
    "HFT3_FEATURE_ROOT",
    "HFT3_QUOTE_STEPPING",
    "HFT3_SCRATCH_HYP_REGISTRY",
)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Lake manifest / NPZ discovery
# ---------------------------------------------------------------------------

def _load_manifest(repo_root: Path) -> dict[tuple[str, str], str] | None:
    """Return {(symbol, event_id): npz_path_str} from lake_manifest if available."""
    try:
        from data_system.src.lake_manifest import load_manifest  # type: ignore[import]

        from data_system.src.lake_manifest import resolve_npz_path

        entries = load_manifest(repo_root)
        if entries is None:
            return None
        result: dict[tuple[str, str], str] = {}
        for e in entries:
            result[(e["symbol"], e["event_id"])] = str(
                resolve_npz_path(repo_root, e["npz_path"])
            )
        return result
    except Exception:  # module not yet shipped — fall through to scan
        return None


def _scan_npz_dir(npz_dir: Path) -> dict[tuple[str, str], str]:
    """Fallback: scan data/npz/*.npz and parse {symbol}_{event_id}_mbo.npz names."""
    result: dict[tuple[str, str], str] = {}
    if not npz_dir.is_dir():
        return result
    for f in sorted(npz_dir.glob("*.npz")):
        m = NPZ_PATTERN.match(f.name)
        if m:
            result[(m.group("symbol"), m.group("event_id"))] = str(f)
    return result


def load_lake_index(
    repo_root: Path, *, rescan: bool = False
) -> dict[tuple[str, str], str]:
    """Return {(symbol, event_id): npz_path_str}.

    Tries lake_manifest first; falls back to scanning data/npz/*.npz.
    Pass rescan=True to skip the manifest and force a directory scan.
    """
    from data_system.src.npz_resolver import npz_root

    if not rescan:
        manifest = _load_manifest(repo_root)
        if manifest:
            return manifest
    return _scan_npz_dir(npz_root(repo_root))


# ---------------------------------------------------------------------------
# Work-unit enumeration
# ---------------------------------------------------------------------------

def _read_events_csv(events_csv: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(events_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(dict(row))
    return rows


def _parse_symbols(symbols_str: str) -> list[str]:
    return [s.strip() for s in symbols_str.split(",") if s.strip()]


# ---------------------------------------------------------------------------
# Shard helpers
# ---------------------------------------------------------------------------

def parse_shard(shard_str: str) -> tuple[int, int]:
    """Parse 'I/N' shard spec; return (shard_index, total_shards).

    Raises ValueError on malformed input or out-of-range index.
    """
    parts = shard_str.strip().split("/")
    if len(parts) != 2:
        raise ValueError(
            f"--shard must be 'I/N' (e.g. '0/2'), got: {shard_str!r}"
        )
    try:
        shard_i = int(parts[0])
        shard_n = int(parts[1])
    except ValueError:
        raise ValueError(
            f"--shard I/N requires integer parts, got: {shard_str!r}"
        )
    if shard_n < 1:
        raise ValueError(
            f"--shard N must be >= 1, got N={shard_n}"
        )
    if shard_i < 0 or shard_i >= shard_n:
        raise ValueError(
            f"--shard I must satisfy 0 <= I < N; got I={shard_i}, N={shard_n}"
        )
    return shard_i, shard_n


def _unit_shard_key(unit: dict) -> str:
    """Stable string key for a work unit: 'event_id|symbol|band'."""
    return f"{unit['event_id']}|{unit['symbol']}|{unit['latency_ms']}"


def _stable_hash(s: str) -> int:
    """SHA-256 of the key, truncated to an unsigned 64-bit int.

    Deterministic across Python versions, platforms, and PYTHONHASHSEED
    (unlike the built-in hash()).  The same key always maps to the same shard
    on any machine, so the laptop and CHI404 agree on the partition even when
    they scan the lake independently.
    """
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    # Take the first 8 bytes as a big-endian unsigned int
    return int.from_bytes(digest[:8], "big")


def apply_shard(
    work_units: list[dict],
    shard_i: int,
    shard_n: int,
) -> list[dict]:
    """Return the subset of work_units assigned to shard I of N.

    Assignment: stable_hash(event_id|symbol|band) % N == I.
    The input list is first sorted by key for reproducibility (order must not
    affect which shard a unit lands in — hashing ensures it does not — but a
    stable sort keeps the within-shard order deterministic too).
    """
    sorted_units = sorted(work_units, key=_unit_shard_key)
    return [
        u for u in sorted_units
        if _stable_hash(_unit_shard_key(u)) % shard_n == shard_i
    ]


def _q001_gap_reason_map(
    manifest_path: Path | None = None,
) -> dict[tuple[str, str | None], str]:
    """Return known Q001 unavailable MBO slots from the accepted gap ledger."""
    using_default_manifest = manifest_path is None
    manifest_path = Q001_MBO_PILOT_MANIFEST if manifest_path is None else manifest_path
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if using_default_manifest:
            raise RuntimeError(f"Q001 MBO pilot manifest unavailable or malformed: {manifest_path}") from exc
        return {}
    if not isinstance(data, dict):
        if using_default_manifest:
            raise RuntimeError(f"Q001 MBO pilot manifest root must be a JSON object: {manifest_path}")
        return {}

    gap_reasons: dict[tuple[str, str | None], str] = {}
    for event_id in data.get("no_market_windows") or []:
        gap_reasons[(str(event_id), None)] = "no_market_data"

    for window in data.get("partial_windows") or []:
        if not isinstance(window, dict):
            continue
        event_id = str(window.get("event_id") or "")
        reason = str(window.get("reason") or "symbol_absent_in_raw_after_redownload")
        if not event_id:
            continue
        missing_symbols = window.get("missing_symbols") or []
        if not isinstance(missing_symbols, list):
            continue
        for symbol in missing_symbols:
            gap_reasons[(event_id, str(symbol))] = reason
    return gap_reasons


EVENT_FINGERPRINT_FIELDS = (
    "event_id",
    "event_type",
    "release_date",
    "release_time",
    "timezone",
    "window_name",
    "start_offset_seconds",
    "end_offset_seconds",
    "symbols",
    "priority",
    "source",
    "source_url",
    "effective_date",
    "notes",
    "row_status",
)


def _event_row_fingerprint(row: dict[str, str]) -> str:
    payload = {field: str(row.get(field, "")) for field in EVENT_FINGERPRINT_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _npz_fingerprint(path: str, cache: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    if cache is not None and path in cache:
        return cache[path]
    p = Path(path)
    st = p.stat()
    fingerprint = {
        "path": str(p),
        "size": int(st.st_size),
        "mtime_ns": int(st.st_mtime_ns),
        "sha256": _file_sha256(p),
    }
    if cache is not None:
        cache[path] = fingerprint
    return fingerprint


def _sensor_feature_fingerprints(event_id: str) -> dict[str, dict[str, Any]]:
    vix_path = _vix_feature_path(event_id)
    if vix_path is None:
        return {}
    return {"VIX": _npz_fingerprint(str(vix_path))}


def _stamp_expected_hypothesis_ids(work_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    active_ids = sorted(int(h.hyp_id) for h in get_active_hypotheses())
    stamped: list[dict[str, Any]] = []
    for unit in work_units:
        hyp_ids_filter = unit.get("hyp_ids")
        if hyp_ids_filter is None:
            expected_ids = active_ids
        else:
            allowed = {int(h) for h in hyp_ids_filter}
            expected_ids = [h for h in active_ids if h in allowed]
        stamped.append({**unit, "expected_hypothesis_ids": expected_ids})
    return stamped


def _validate_requested_hypothesis_ids(
    per_etype_hyp_ids: dict[str, set[int]] | None,
    *,
    p: argparse.ArgumentParser,
) -> None:
    if per_etype_hyp_ids is None:
        return
    from features_engine.src.hypotheses.registry import get_active_hypotheses

    active_ids = {int(h.hyp_id) for h in get_active_hypotheses()}
    for etype, hyp_ids in sorted(per_etype_hyp_ids.items()):
        requested = [int(h) for h in hyp_ids]
        missing = sorted(set(requested) - active_ids)
        if missing:
            p.error(f"Stage-A filter references inactive/missing hyp_ids for event_type={etype}: {missing}")


def build_work_units(
    events_csv: Path,
    lake_index: dict[tuple[str, str], str],
    *,
    latency_bands: list[float],
    event_type_filter: str | None,
    symbol_filter: list[str] | None,
    max_events: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (work_units, skipped_units) sorted deterministically.

    Each work unit: {event_id, symbol, npz_path, latency_ms, event_type, release_date}
    Each skipped unit: {event_id, event_type, release_date, symbol, latency_ms, reason}
    """
    rows = _read_events_csv(events_csv)
    # stable sort by event_id for determinism
    rows.sort(key=lambda r: r["event_id"])

    work: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    _empty_cache: dict[str, bool] = {}  # npz_path -> is-empty (built-time skip)
    _npz_fingerprint_cache: dict[str, dict[str, Any]] = {}
    _sensor_fingerprint_cache: dict[str, dict[str, dict[str, Any]]] = {}
    # max_events caps events that actually produce work units — truncating
    # the raw rows first would just select the alphabetically-first events
    # regardless of whether their NPZ exists.
    events_with_work: set[str] = set()
    q001_gap_reasons = _q001_gap_reason_map()

    for row in rows:
        etype = row.get("event_type", "")
        if event_type_filter and etype != event_type_filter:
            continue

        event_id = row["event_id"]
        event_fingerprint = _event_row_fingerprint(row)
        candidate_symbols = _parse_symbols(row.get("symbols", DEFAULT_SYMBOL))
        if symbol_filter:
            candidate_symbols = [s for s in candidate_symbols if s in symbol_filter]
        if not candidate_symbols:
            candidate_symbols = [DEFAULT_SYMBOL]

        release_date = row.get("release_date", "")
        if release_date >= RESEARCH_EMBARGO_START:
            for symbol in sorted(candidate_symbols):
                for band in sorted(latency_bands):
                    skipped.append({
                        "event_id": event_id,
                        "event_type": etype,
                        "release_date": release_date,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": "embargo_2026",
                    })
            continue

        if event_id not in _sensor_fingerprint_cache:
            _sensor_fingerprint_cache[event_id] = _sensor_feature_fingerprints(event_id)
        sensor_feature_fingerprints = _sensor_fingerprint_cache[event_id]

        has_any_npz = any(
            lake_index.get((symbol, event_id)) is not None
            for symbol in candidate_symbols
        )
        if (
            max_events is not None
            and has_any_npz
            and event_id not in events_with_work
            and len(events_with_work) >= max_events
        ):
            continue

        for symbol in sorted(candidate_symbols):
            npz_path = lake_index.get((symbol, event_id))
            for band in sorted(latency_bands):
                q001_gap_reason = q001_gap_reasons.get((event_id, symbol)) or q001_gap_reasons.get((event_id, None))
                if q001_gap_reason:
                    skipped.append({
                        "event_id": event_id,
                        "event_type": etype,
                        "release_date": release_date,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": q001_gap_reason,
                    })
                elif npz_path is None:
                    skipped.append({
                        "event_id": event_id,
                        "event_type": etype,
                        "release_date": release_date,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": "npz_missing",
                    })
                elif _is_empty_npz(npz_path, _empty_cache):
                    # Present-but-empty NPZ: skip at BUILD time so it never spawns
                    # a worker (which would pay ~6s of replay setup just to discover
                    # zero events). This is what keeps the run from grinding.
                    skipped.append({
                        "event_id": event_id,
                        "event_type": etype,
                        "release_date": release_date,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": "empty_npz",
                    })
                else:
                    work.append({
                        "event_id": event_id,
                        "symbol": symbol,
                        "npz_path": npz_path,
                        "npz_fingerprint": _npz_fingerprint(npz_path, _npz_fingerprint_cache),
                        "sensor_feature_fingerprints": sensor_feature_fingerprints,
                        "latency_ms": band,
                        "event_type": etype,
                        "release_date": release_date,
                        "event_fingerprint": event_fingerprint,
                    })
                    events_with_work.add(event_id)

    return work, skipped


# Real-data NPZ are large (hundreds of KB+); a zero-event shell is ~263 bytes.
# Only sub-threshold files are actually opened to confirm; big files are never
# loaded here (assumed non-empty — the worker handles them).
_EMPTY_NPZ_MAX_BYTES = 4096


def _is_empty_npz(path: str, cache: dict[str, bool]) -> bool:
    if path in cache:
        return cache[path]
    empty = False
    try:
        if os.path.getsize(path) < _EMPTY_NPZ_MAX_BYTES:
            import numpy as np

            with np.load(path, allow_pickle=True) as d:
                arr = d["data"] if "data" in getattr(d, "files", []) else None
                empty = arr is not None and arr.shape[0] == 0
    except Exception:
        empty = False  # uncertain -> don't skip; let the worker decide
    cache[path] = empty
    return empty


# ---------------------------------------------------------------------------
# VIX feature helpers
# ---------------------------------------------------------------------------

def _vix_feature_path(event_id: str) -> Path | None:
    """Return path to VIX precomputed feature npz if it exists, else None.

    Checks <feature_store_root>/VIX.OPT/VIX.OPT_<event_id>_features_v1.npz.
    Honours HFT3_FEATURE_ROOT env var via feature_store_root().
    Returns None if the feature_store module is absent or the file does not exist.
    """
    try:
        from data_system.src.feature_store import feature_store_root  # type: ignore[import]
        froot = feature_store_root(_REPO)
        candidate = froot / "VIX.OPT" / f"VIX.OPT_{event_id}_features_v1.npz"
        return candidate if candidate.is_file() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Worker (top-level — picklable for spawn pool)
# ---------------------------------------------------------------------------

def _init_worker() -> None:
    """Re-assert single-thread BLAS in spawn children (belt-and-suspenders)."""
    for _var in (
        "OPENBLAS_NUM_THREADS",
        "OMP_NUM_THREADS",
        "MKL_NUM_THREADS",
        "NUMEXPR_NUM_THREADS",
        "VECLIB_MAXIMUM_THREADS",
        "BLIS_NUM_THREADS",
    ):
        os.environ[_var] = "1"


def _worker(unit: dict[str, Any]) -> dict[str, Any]:
    """Run full hypothesis matrix for one (event_id, symbol, npz_path, latency_ms).

    Returns a plain dict with all results; no numpy arrays cross the process
    boundary.
    """
    import sys
    from pathlib import Path

    _REPO_W = Path(__file__).resolve().parents[1]
    if str(_REPO_W) not in sys.path:
        sys.path.insert(0, str(_REPO_W))
    from hft3_bootstrap import setup_repo_paths as _setup
    _setup()

    from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay
    from features_engine.src.hypotheses.registry import get_active_hypotheses
    from replay.replay_clock import deterministic_run_id as _det_id

    event_id: str = unit["event_id"]
    symbol: str = unit["symbol"]
    npz_path: str = unit["npz_path"]
    latency_ms: float = float(unit["latency_ms"])
    hyp_ids_filter: list[int] | None = unit.get("hyp_ids")
    hyp_ids_checkpoint = sorted(int(h) for h in hyp_ids_filter) if hyp_ids_filter is not None else None
    expected_hypothesis_ids = [int(h) for h in unit.get("expected_hypothesis_ids", [])]

    run_id = _det_id(npz_path, latency_ms, "LogProbQueueModel2")

    sensor_fingerprints = unit.get("sensor_feature_fingerprints", {})
    sensor_feature_npz: dict[str, str] | None = (
        {name: str(fp["path"]) for name, fp in sensor_fingerprints.items()}
        if sensor_fingerprints else None
    )

    t0 = time.monotonic()
    try:
        hyps = get_active_hypotheses()
        if hyp_ids_filter is not None:
            hyps = [h for h in hyps if h.hyp_id in hyp_ids_filter]
        results = run_all_hypotheses_replay(
            hyps, npz_path, latency_ms=latency_ms, sensor_feature_npz=sensor_feature_npz
        )
        result_ids = sorted(int(hyp_id) for hyp_id in results)
        if result_ids != sorted(expected_hypothesis_ids):
            raise RuntimeError(
                f"replay returned hypothesis ids {result_ids}, expected {sorted(expected_hypothesis_ids)}"
            )
        hyp_name_map = {h.hyp_id: h.name for h in hyps}
        # Derive fee_per_round_trip_usd from FeeModel for R6 fee-stress (post-hoc).
        # Uses the same product resolution as replay_matrix / stage_a_screen.
        # tick_value_usd is needed for per-trade slippage adder arithmetic.
        # Both fields are additive (never present in old records) — stress only
        # applies to runs produced after this commit (rp_v1 → rp_v2 upgrade path).
        from backtest_pipeline.src.fee_model import FeeModel as _FeeModel

        _tick_val_map = {"ES": 12.50, "NQ": 5.00, "MES": 1.25, "MNQ": 0.50}
        _sym_base = symbol.split(".")[0]
        _tick_val_usd = _tick_val_map.get(_sym_base, 1.25)
        _fm = _FeeModel(product=_sym_base if _sym_base in _FeeModel.TICK_VALUES else "MES")
        _fee_per_rt = _fm.get_fee_per_contract() * 2.0  # both legs, 1 contract

        serialized: list[dict[str, Any]] = []
        for hyp_id in sorted(results):
            res = results[hyp_id]
            serialized.append({
                "hypothesis_id": hyp_id,
                "hypothesis_name": hyp_name_map.get(hyp_id, ""),
                "net_pnl_usd": round(float(res.net_pnl), 6),
                "hftbacktest_cash_balance_usd": round(float(res.hftbacktest_cash_balance), 6),
                "ending_position_qty": round(float(res.ending_position_qty), 6),
                "num_trades": int(res.num_trades),
                "win_rate": round(float(res.win_rate), 6),
                "expectancy_usd": round(float(res.expectancy), 6),
                "adverse_selection_ticks": round(float(res.adverse_selection_ticks), 6),
                "tail_loss_usd": round(float(res.tail_loss), 6),
                # R6 fee-stress decomposition fields (additive, rp_v2+):
                # fee_per_round_trip_usd = FeeModel(product, non_member) * 2 legs
                # tick_value_usd = 1 tick move in USD for this product (1 contract)
                # gross_expectancy = expectancy_usd + fee_per_round_trip_usd
                "fee_per_round_trip_usd": round(_fee_per_rt, 6),
                "tick_value_usd": round(_tick_val_usd, 6),
            })
        elapsed = time.monotonic() - t0
        return {
            "run_id": run_id,
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": npz_path,
            "npz_fingerprint": unit.get("npz_fingerprint"),
            "sensor_feature_fingerprints": sensor_fingerprints,
            "latency_ms": latency_ms,
            "event_type": unit.get("event_type", ""),
            "release_date": unit.get("release_date", ""),
            "event_fingerprint": unit.get("event_fingerprint", ""),
            "hyp_ids": hyp_ids_checkpoint,
            "expected_hypothesis_ids": expected_hypothesis_ids,
            "elapsed_s": round(elapsed, 3),
            "error": None,
            "skip_reason": None,
            "hypotheses": serialized,
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        msg = str(exc)
        # An empty NPZ (present file, zero events) is NOT a code error — it is
        # missing data. Mark it a SKIP so it never counts as an ERROR (which is
        # what made a whole run look broken / grind on bad data).
        is_empty = "zero events" in msg
        return {
            "run_id": run_id,
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": npz_path,
            "npz_fingerprint": unit.get("npz_fingerprint"),
            "sensor_feature_fingerprints": sensor_fingerprints,
            "latency_ms": latency_ms,
            "event_type": unit.get("event_type", ""),
            "release_date": unit.get("release_date", ""),
            "event_fingerprint": unit.get("event_fingerprint", ""),
            "hyp_ids": hyp_ids_checkpoint,
            "expected_hypothesis_ids": expected_hypothesis_ids,
            "elapsed_s": round(elapsed, 3),
            "error": None if is_empty else msg,
            "skip_reason": "empty_npz" if is_empty else None,
            "hypotheses": [],
        }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate_results(
    unit_results: list[dict[str, Any]],
) -> dict[str, dict[str, dict[float, dict[str, Any]]]]:
    """Aggregate per-event results into hypothesis × event_type × band cells.

    Returns nested dict:
      {hypothesis_id_str: {event_type: {latency_ms: cell_dict}}}

    Cell dict keys:
      n_events, total_trades, mean_expectancy, mean_win_rate,
      mean_adverse_selection_ticks, p5_expectancy_tail,
      per_event_expectancies (list[float]) — used for t-test
    """
    # Accumulate per cell
    # Key: (hyp_id, event_type, latency_ms)
    accum: dict[tuple[int, str, float], dict[str, Any]] = {}

    for ur in unit_results:
        if ur.get("error") or ur.get("skip_reason"):
            continue
        etype = ur["event_type"]
        band = float(ur["latency_ms"])
        for hrow in ur.get("hypotheses", []):
            hyp_id = int(hrow["hypothesis_id"])
            key = (hyp_id, etype, band)
            if key not in accum:
                accum[key] = {
                    "hypothesis_id": hyp_id,
                    "hypothesis_name": hrow["hypothesis_name"],
                    "event_type": etype,
                    "latency_ms": band,
                    "n_events": 0,
                    "total_trades": 0,
                    "sum_expectancy": 0.0,
                    "sum_win_rate": 0.0,
                    "sum_adverse_selection": 0.0,
                    "per_event_expectancies": [],
                    "per_event_win_rates": [],
                }
            cell = accum[key]
            cell["n_events"] += 1
            cell["total_trades"] += int(hrow["num_trades"])
            cell["sum_expectancy"] += float(hrow["expectancy_usd"])
            cell["sum_win_rate"] += float(hrow["win_rate"])
            cell["sum_adverse_selection"] += float(hrow["adverse_selection_ticks"])
            cell["per_event_expectancies"].append(float(hrow["expectancy_usd"]))
            cell["per_event_win_rates"].append(float(hrow["win_rate"]))

    # Finalise cells
    finalised: dict[str, dict[str, dict[float, dict[str, Any]]]] = {}
    for (hyp_id, etype, band), cell in sorted(accum.items()):
        n = cell["n_events"]
        expecs = cell["per_event_expectancies"]
        hyp_key = str(hyp_id)
        if hyp_key not in finalised:
            finalised[hyp_key] = {}
        if etype not in finalised[hyp_key]:
            finalised[hyp_key][etype] = {}
        finalised[hyp_key][etype][band] = {
            "hypothesis_id": hyp_id,
            "hypothesis_name": cell["hypothesis_name"],
            "n_events": n,
            "total_trades": cell["total_trades"],
            "mean_expectancy_usd": round(cell["sum_expectancy"] / n, 6) if n else 0.0,
            "mean_win_rate": round(cell["sum_win_rate"] / n, 6) if n else 0.0,
            "mean_adverse_selection_ticks": round(cell["sum_adverse_selection"] / n, 6) if n else 0.0,
            "p5_expectancy_tail_usd": round(float(np.percentile(expecs, 5)), 6) if expecs else 0.0,
            "per_event_expectancies": [round(v, 6) for v in expecs],
            "aggregation_note": (
                "mean/win_rate/adverse_selection are arithmetic means of per-event BacktestResult values; "
                "p5_tail is 5th-percentile of per-event expectancies (worst-event, not worst-trade)"
            ),
        }
    return finalised


# ---------------------------------------------------------------------------
# Multiple-testing correction
# ---------------------------------------------------------------------------

def _derive_p_value(per_event_expectancies: list[float]) -> float:
    """One-sample two-sided t-test on per-event expectancies vs null=0.

    Returns p=1.0 when n < 3 (insufficient data; documented).
    Uses scipy.stats.ttest_1samp identical to MultipleTestingGate.compute_p_value.
    """
    from scipy import stats  # type: ignore[import]

    if len(per_event_expectancies) < 3:
        return 1.0
    arr = np.array(per_event_expectancies, dtype=float)
    _, p_val = stats.ttest_1samp(arr, 0.0)
    return float(np.clip(p_val, 1e-15, 1.0))


def _apply_corrections(
    aggregated: dict[str, dict[str, dict[float, dict[str, Any]]]],
    *,
    stage_a_tested_cells: list[dict[str, Any]] | None = None,
    stage_a_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply Holm and BH corrections per event_type across all (hypothesis, band) cells.

    Returns {event_type: {method: ChampionReport_dict}}

    When stage_a_tested_cells is provided (stage-B selective run), every cell in the
    original stage-A family that was NOT re-run in this sweep receives a placeholder
    HypothesisTestResult(p_value=1.0, metric_value=0.0, num_trades=0) before
    apply_correction.  This keeps Holm n = full original family so adjusted alphas are
    identical to a full run where screened-out cells failed; conservative FWER,
    no multiplicity laundering.
    """
    from decision_engine.python.src.multiple_testing_correction import (  # type: ignore[import]
        HypothesisTestResult,
        MultipleTestingGate,
    )

    # Collect cells per event_type
    by_etype: dict[str, list[dict[str, Any]]] = {}
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                entry = {**cell, "band": band}
                by_etype.setdefault(etype, []).append(entry)

    # Build set of (hyp_id, event_type, band) actually run in this sweep
    rerun_keys: set[tuple[int, str, float]] = set()
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band in band_map:
                rerun_keys.add((int(hyp_key), etype, float(band)))

    # Derive placeholder slugs from stage_a_tested_cells not in rerun_keys
    # tested_cells entries expected: {hyp_id, event_type, band, slug (optional)}
    placeholder_slugs: list[str] = []
    placeholder_results_by_etype: dict[str, list[HypothesisTestResult]] = {}
    # index of (hyp_id, etype) pairs present in rerun_keys (band-agnostic)
    rerun_keys_no_band: set[tuple[int, str]] = {(h, e) for h, e, _ in rerun_keys}
    placeholders_matched = 0
    if stage_a_tested_cells:
        for tc in stage_a_tested_cells:
            tc_hyp = int(tc.get("hyp_id", tc.get("hypothesis_id", 0)))
            tc_etype = str(tc.get("event_type", ""))
            raw_band = tc.get("band_ms", tc.get("band", tc.get("latency_ms")))
            if raw_band is not None:
                tc_band: float | None = float(raw_band)
            else:
                tc_band = None

            if tc_band is not None and (tc_hyp, tc_etype, tc_band) in rerun_keys:
                placeholders_matched += 1
                continue
            if tc_band is None and (tc_hyp, tc_etype) in rerun_keys_no_band:
                placeholders_matched += 1
                continue

            slug = tc.get("slug") or f"hyp_{tc_hyp}_band_{tc_band}"
            placeholder_slugs.append(slug)
            placeholder_results_by_etype.setdefault(tc_etype, []).append(
                # selective-inference guard — adjusted alphas identical to a full
                # run where screened-out cells failed; conservative FWER,
                # no multiplicity laundering.
                HypothesisTestResult(
                    slug=slug,
                    legacy_id=f"HYP_{tc_hyp}",
                    metric_name="mean_expectancy_usd",
                    metric_value=0.0,
                    p_value=1.0,
                    t_statistic=0.0,
                    num_trades=0,
                )
            )

        family_accounted = len(placeholder_slugs) + placeholders_matched
        if family_accounted != len(stage_a_tested_cells):
            print(
                f"WARNING _apply_corrections: family size mismatch — "
                f"tested_cells={len(stage_a_tested_cells)}, "
                f"placeholders={len(placeholder_slugs)}, "
                f"rerun_matched={placeholders_matched}, "
                f"accounted={family_accounted}",
                flush=True,
            )

    if stage_a_filter is not None:
        stage_a_filter["placeholders_added"] = len(placeholder_slugs)

    corrections: dict[str, Any] = {}
    gate = MultipleTestingGate(alpha=0.05)

    # Union of etypes: those we ran + those that only appear in placeholders
    all_etypes = sorted(set(by_etype.keys()) | set(placeholder_results_by_etype.keys()))

    for etype in all_etypes:
        cells = sorted(by_etype.get(etype, []), key=lambda c: (c["hypothesis_id"], c["band"]))
        test_results: list[HypothesisTestResult] = []
        for cell in cells:
            p_val = _derive_p_value(cell["per_event_expectancies"])
            n = cell["n_events"]
            expecs = cell["per_event_expectancies"]
            arr = np.array(expecs, dtype=float)
            t_stat = 0.0
            if len(expecs) >= 2:
                se = float(np.std(arr, ddof=1) / np.sqrt(len(arr)))
                if se >= 1e-15:
                    t_stat = float(np.mean(arr) / se)
            slug = f"hyp_{cell['hypothesis_id']}_band_{cell['band']}"
            test_results.append(HypothesisTestResult(
                slug=slug,
                legacy_id=f"HYP_{cell['hypothesis_id']}",
                metric_name="expectancy",
                metric_value=cell["mean_expectancy_usd"],
                p_value=p_val,
                t_statistic=t_stat,
                num_trades=cell["total_trades"],
            ))
        # Append placeholders for cells not re-run (Holm family honesty)
        test_results.extend(placeholder_results_by_etype.get(etype, []))

        holm_report = gate.apply_correction(test_results, method="holm")
        # BH requires fresh HypothesisTestResult objects (is_significant written in-place)
        test_results_bh: list[HypothesisTestResult] = []
        for cell in cells:
            p_val = _derive_p_value(cell["per_event_expectancies"])
            arr2 = np.array(cell["per_event_expectancies"], dtype=float)
            t2 = 0.0
            if len(cell["per_event_expectancies"]) >= 2:
                se2 = float(np.std(arr2, ddof=1) / np.sqrt(len(arr2)))
                if se2 >= 1e-15:
                    t2 = float(np.mean(arr2) / se2)
            slug2 = f"hyp_{cell['hypothesis_id']}_band_{cell['band']}"
            test_results_bh.append(HypothesisTestResult(
                slug=slug2,
                legacy_id=f"HYP_{cell['hypothesis_id']}",
                metric_name="expectancy",
                metric_value=cell["mean_expectancy_usd"],
                p_value=p_val,
                t_statistic=t2,
                num_trades=cell["total_trades"],
            ))
        test_results_bh.extend(placeholder_results_by_etype.get(etype, []))
        bh_report = gate.apply_correction(test_results_bh, method="benjamini_hochberg")

        def _report_to_dict(rpt: Any) -> dict[str, Any]:
            return {
                "method": rpt.method,
                "original_alpha": rpt.original_alpha,
                "total_tested": rpt.total_tested,
                "passed_slugs": rpt.passed_slugs,
                "failed_slugs": rpt.failed_slugs,
                "sorted_results": [
                    {
                        "slug": r.slug,
                        "legacy_id": r.legacy_id,
                        "p_value": round(r.p_value, 8),
                        "t_statistic": round(r.t_statistic, 6),
                        "adjusted_alpha": round(r.adjusted_alpha, 8),
                        "is_significant": r.is_significant,
                        "num_trades": r.num_trades,
                        "metric_value": round(r.metric_value, 6),
                    }
                    for r in rpt.sorted_results
                ],
            }

        etype_placeholders = [r.slug for r in placeholder_results_by_etype.get(etype, [])]
        corrections[etype] = {
            "holm": _report_to_dict(holm_report),
            "benjamini_hochberg": _report_to_dict(bh_report),
            "p_value_method": (
                "scipy.stats.ttest_1samp(per_event_expectancies, popmean=0.0), "
                "two-sided; p=1.0 when n_events < 3"
            ),
            **({"not_rerun_stage_b": etype_placeholders} if etype_placeholders else {}),
        }
    return corrections


# ---------------------------------------------------------------------------
# Robustness producers (additive block — does NOT alter Holm/BH logic above)
# ---------------------------------------------------------------------------

def _compute_robustness(
    aggregated: dict[str, dict[str, dict[float, dict[str, Any]]]],
    unit_results: list[dict[str, Any]],
    stage_a_tested_cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute DSR, CSCV-PBO, and bootstrap CI for all cells in the aggregated pool.

    Called AFTER _apply_corrections, BEFORE write_universe_result.
    Does NOT touch Holm/BH logic, placeholders, or walk-forward sections.

    n_trials convention
    -------------------
    n_trials for DSR = len(stage_a_tested_cells), the FULL family including
    Stage-B placeholders (cells that were in Stage A but not re-run).
    When stage_a_tested_cells is empty (standalone Stage-B run without --from-stage-a),
    n_trials defaults to the count of cells actually aggregated in this run.
    This is conservative: omitting the stage-A family understates the DSR penalty.
    The n_trials value is echoed in every dsr_by_cell entry for audit.

    CSCV matrix
    -----------
    Rows = date blocks formed by splitting unit_results events (sorted by
    release_date, then event_id for ties) into n_splits=8 equal-ish blocks.
    Cols = non-placeholder tested cells (hyp_id × event_type × band triples
    that have per_event_expectancies in the aggregated pool).
    Value = per-block mean expectancy of that cell.
    Cells with no events in a block get NaN; cells with any NaN block are
    excluded from the CSCV matrix (n_excluded is recorded).

    Returns
    -------
    Dict matching the JSON shape:
        {
          "dsr_by_cell":      {slug: {...}},
          "bootstrap_by_cell": {slug: {...}},
          "pbo":              {...},
          "producer_version": "rp_v1",
        }
    """
    try:
        from research_pipeline.src.robustness_producers import (  # type: ignore[import]
            bootstrap_ci,
            cscv_pbo,
            deflated_sharpe_for_cell,
            fee_stress_for_cell,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"robustness_producers import failed: {exc}", "producer_version": "rp_v1"}

    # n_trials = full family including placeholders; fall back to live cell count
    n_trials_total = len(stage_a_tested_cells) if stage_a_tested_cells else None

    # Flatten aggregated cells to a list of (slug, per_event_expectancies)
    # collecting only cells that actually have data (not placeholders)
    cell_data: list[tuple[str, int, str, float, list[float]]] = []
    # cell_data: [(slug, hyp_id, event_type, band, per_event_expectancies)]
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                slug = f"hyp_{cell['hypothesis_id']}_band_{band}"
                expecs = cell.get("per_event_expectancies", [])
                cell_data.append((slug, int(cell["hypothesis_id"]), etype, float(band), expecs))

    # If no stage_a_tested_cells supplied, use count of live cells as n_trials
    n_trials_effective = n_trials_total if n_trials_total is not None else len(cell_data)

    # --- Per-cell DSR, bootstrap, and R6 fee stress ---
    dsr_by_cell: dict[str, Any] = {}
    bootstrap_by_cell: dict[str, Any] = {}
    fee_stress_by_cell: dict[str, Any] = {}

    # Build a lookup: (hyp_id, etype, band) → per-event decomposition lists
    # for R6 fee stress.  Keys added in rp_v2; old records return 0.0 →
    # fee_stress_for_cell detects this and sets stress_data_available=False.
    _fee_decomp: dict[tuple[int, str, float], dict[str, list]] = {}
    for ur in unit_results:
        if ur.get("error") or ur.get("skip_reason"):
            continue
        ur_etype = ur.get("event_type", "")
        ur_band  = float(ur.get("latency_ms", 0.0))
        for hrow in ur.get("hypotheses", []):
            _key = (int(hrow["hypothesis_id"]), ur_etype, ur_band)
            if _key not in _fee_decomp:
                _fee_decomp[_key] = {
                    "n_trades":       [],
                    "fee_per_rt":     [],
                    "tick_value":     [],
                }
            _d = _fee_decomp[_key]
            _d["n_trades"].append(int(hrow.get("num_trades", 0)))
            _d["fee_per_rt"].append(float(hrow.get("fee_per_round_trip_usd", 0.0)))
            _d["tick_value"].append(float(hrow.get("tick_value_usd", 0.0)))

    for slug, hyp_id, etype, band, expecs in cell_data:
        cell_slug = f"{slug}_{etype}"
        dsr_by_cell[cell_slug]       = deflated_sharpe_for_cell(expecs, n_trials=n_trials_effective)
        bootstrap_by_cell[cell_slug] = bootstrap_ci(expecs)

        # R6: collect decomposition arrays for this cell
        _dk = (hyp_id, etype, band)
        _decomp = _fee_decomp.get(_dk, {})
        fee_stress_by_cell[cell_slug] = fee_stress_for_cell(
            per_event_expectancies=expecs,
            per_event_n_trades=_decomp.get("n_trades", []),
            per_event_fee_per_rt=_decomp.get("fee_per_rt", []),
            per_event_tick_value=_decomp.get("tick_value", []),
        )

    # --- CSCV PBO matrix ---
    # Build date-ordered event list from non-errored unit_results
    events_ordered: list[str] = []
    seen_events: set[str] = set()
    for ur in sorted(unit_results, key=lambda u: (u.get("release_date", ""), u["event_id"])):
        if not ur.get("error") and not ur.get("skip_reason") and ur["event_id"] not in seen_events:
            events_ordered.append(ur["event_id"])
            seen_events.add(ur["event_id"])

    pbo_result: dict[str, Any]
    N_SPLITS = 8

    if len(events_ordered) < N_SPLITS:
        pbo_result = {
            "pbo": None,
            "n_splits": N_SPLITS,
            "n_configs": len(cell_data),
            "n_partitions": 0,
            "n_excluded": 0,
            "reason": f"insufficient_events_for_cscv: {len(events_ordered)} < {N_SPLITS}",
        }
    else:
        # Assign each event to one of N_SPLITS blocks (as equal as possible)
        block_size = len(events_ordered) / N_SPLITS
        event_to_block: dict[str, int] = {}
        for i, eid in enumerate(events_ordered):
            event_to_block[eid] = min(int(i / block_size), N_SPLITS - 1)

        # Build matrix: rows=blocks, cols=cells
        # cell_data already excludes placeholders (they have no per_event_expectancies)
        n_cells = len(cell_data)
        mat = np.full((N_SPLITS, n_cells), np.nan)

        for col_idx, (slug, hyp_id, etype, band, _) in enumerate(cell_data):
            # Collect per-event expectancies keyed by event_id for this cell
            # We need to look up per-event values from unit_results
            event_expec: dict[str, float] = {}
            for ur in unit_results:
                if ur.get("error") or ur.get("skip_reason") or ur.get("event_type", "") != etype:
                    continue
                for hrow in ur.get("hypotheses", []):
                    if int(hrow["hypothesis_id"]) == hyp_id and float(ur["latency_ms"]) == band:
                        event_expec[ur["event_id"]] = float(hrow["expectancy_usd"])
                        break

            # Accumulate block-level means
            block_sums: dict[int, list[float]] = {b: [] for b in range(N_SPLITS)}
            for eid, val in event_expec.items():
                blk = event_to_block.get(eid)
                if blk is not None:
                    block_sums[blk].append(val)

            for blk in range(N_SPLITS):
                vals = block_sums[blk]
                if vals:
                    mat[blk, col_idx] = float(np.mean(vals))
                # else stays NaN → cell excluded from CSCV

        pbo_result = cscv_pbo(mat, n_splits=N_SPLITS)

    return {
        "dsr_by_cell": dsr_by_cell,
        "bootstrap_by_cell": bootstrap_by_cell,
        "pbo": pbo_result,
        "fee_stress_by_cell": fee_stress_by_cell,
        "producer_version": "rp_v2",
        "producer_version_note": (
            "rp_v2 adds R6 fee/slippage stress (fee_stress_by_cell). "
            "stress_pass = fee_x2_pass is a REPORTED gate consumed at CC5/CC7 "
            "promotion time — does NOT mutate Holm/BH survivor logic. "
            "Records from pre-rp_v2 runs will show stress_data_available=False."
        ),
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _git_commit(repo_root: Path) -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def _skip_reason_counts(skipped: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in skipped:
        reason = str(row.get("reason") or "unspecified")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _runtime_skip_rows(unit_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in unit_results:
        reason = row.get("skip_reason")
        if not reason:
            continue
        rows.append({
            "event_id": row.get("event_id", ""),
            "event_type": row.get("event_type", ""),
            "release_date": row.get("release_date", ""),
            "symbol": row.get("symbol", ""),
            "latency_ms": row.get("latency_ms"),
            "reason": str(reason),
        })
    return rows


def _combined_skip_rows(
    skipped: list[dict[str, Any]],
    unit_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [*skipped, *_runtime_skip_rows(unit_results)]


CHECKPOINT_SCHEMA = "universe_unit_results_checkpoint_v1"


def _checkpoint_context_path(checkpoint_path: Path) -> Path:
    return checkpoint_path.with_suffix(".context.json")


def _checkpoint_source_hashes() -> dict[str, str]:
    paths: set[Path] = set()
    for pattern in CHECKPOINT_SOURCE_GLOBS:
        paths.update(p for p in _REPO.glob(pattern) if p.is_file())
    return {
        str(path.relative_to(_REPO)).replace("\\", "/"): _file_sha256(path)
        for path in sorted(paths)
    }


def _checkpoint_env_identity() -> dict[str, Any]:
    env = {key: os.environ.get(key, "") for key in CHECKPOINT_ENV_KEYS}
    scratch_path = env.get("HFT3_SCRATCH_HYP_REGISTRY", "").strip()
    scratch_hash = None
    if scratch_path:
        try:
            scratch_hash = _file_sha256(Path(scratch_path))
        except OSError:
            scratch_hash = "missing"
    return {
        "env": env,
        "scratch_registry_sha256": scratch_hash,
    }


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _reset_checkpoint_context(checkpoint_path: Path, context: dict[str, Any]) -> None:
    _atomic_write_text(checkpoint_path, "")
    _atomic_write_text(
        _checkpoint_context_path(checkpoint_path),
        json.dumps(context, indent=2, sort_keys=True) + "\n",
    )


def _checkpoint_context(cli_args: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": CHECKPOINT_SCHEMA,
        "git_commit": _git_commit(_REPO),
        "source_hashes": _checkpoint_source_hashes(),
        "env_identity": _checkpoint_env_identity(),
        "cli_args": cli_args,
    }


def _prepare_checkpoint_results(
    checkpoint_path: Path,
    work_units: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    context_path = _checkpoint_context_path(checkpoint_path)
    try:
        stored_context = json.loads(context_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored_context = None
    if stored_context != context:
        _reset_checkpoint_context(checkpoint_path, context)
        return {}
    return _load_checkpoint_results(checkpoint_path, work_units)


def _load_checkpoint_results(
    checkpoint_path: Path,
    work_units: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    current_units = {_unit_shard_key(unit): unit for unit in work_units}
    current_keys = set(current_units)
    if not checkpoint_path.is_file() or not current_keys:
        return {}

    results: dict[str, dict[str, Any]] = {}
    with checkpoint_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            try:
                key = _unit_shard_key(row)
            except (KeyError, TypeError):
                continue
            if key in current_keys and _checkpoint_result_reusable(row, current_units[key]):
                results[key] = row
    return results


def _checkpoint_result_reusable(row: dict[str, Any], current_unit: dict[str, Any]) -> bool:
    if row.get("error"):
        return False
    required = (
        "event_id",
        "symbol",
        "npz_path",
        "npz_fingerprint",
        "sensor_feature_fingerprints",
        "latency_ms",
        "event_type",
        "release_date",
        "event_fingerprint",
        "hyp_ids",
        "expected_hypothesis_ids",
        "elapsed_s",
        "error",
        "skip_reason",
        "hypotheses",
    )
    if any(k not in row for k in required):
        return False
    for key in ("event_id", "symbol", "npz_path", "event_type", "release_date"):
        if str(row.get(key)) != str(current_unit.get(key)):
            return False
    try:
        if float(row.get("latency_ms")) != float(current_unit.get("latency_ms")):
            return False
    except (TypeError, ValueError):
        return False
    if row.get("npz_fingerprint") != current_unit.get("npz_fingerprint"):
        return False
    if row.get("sensor_feature_fingerprints") != current_unit.get("sensor_feature_fingerprints"):
        return False
    if str(row.get("event_fingerprint")) != str(current_unit.get("event_fingerprint")):
        return False
    if row.get("hyp_ids") != current_unit.get("hyp_ids"):
        return False
    if row.get("expected_hypothesis_ids") != current_unit.get("expected_hypothesis_ids"):
        return False
    hypotheses = row.get("hypotheses")
    if not isinstance(hypotheses, list):
        return False
    if row.get("skip_reason"):
        if row.get("skip_reason") not in CHECKPOINT_SKIP_REASONS:
            return False
        if row.get("error") is not None:
            return False
        if hypotheses:
            return False
        return True
    required_hyp_keys = (
        "hypothesis_id",
        "hypothesis_name",
        "num_trades",
        "expectancy_usd",
        "win_rate",
        "adverse_selection_ticks",
    )
    actual_hyp_ids: list[int] = []
    for hyp in hypotheses:
        if not isinstance(hyp, dict) or any(k not in hyp for k in required_hyp_keys):
            return False
        try:
            actual_hyp_ids.append(int(hyp["hypothesis_id"]))
            int(hyp["num_trades"])
            float(hyp["expectancy_usd"])
            float(hyp["win_rate"])
            float(hyp["adverse_selection_ticks"])
        except (TypeError, ValueError):
            return False
    expected_hyp_ids = [int(h) for h in row.get("expected_hypothesis_ids", [])]
    if not expected_hyp_ids:
        return False
    if len(expected_hyp_ids) != len(set(expected_hyp_ids)):
        return False
    if len(actual_hyp_ids) != len(set(actual_hyp_ids)):
        return False
    if sorted(actual_hyp_ids) != sorted(expected_hyp_ids):
        return False
    return True


def _append_checkpoint_result(checkpoint_path: Path, result: dict[str, Any]) -> None:
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    with checkpoint_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def write_universe_result(
    out_dir: Path,
    *,
    unit_results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    aggregated: dict[str, Any],
    corrections: dict[str, Any],
    robustness: dict[str, Any] | None = None,
    latency_bands: list[float],
    cli_args: dict[str, Any],
    stamp: dict[str, Any],
    run_start_utc: str,
    run_end_utc: str,
    total_elapsed_s: float,
    stage_a_filter: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    all_skipped = _combined_skip_rows(skipped, unit_results)
    units_skipped_embargo = sum(1 for s in all_skipped if s.get("reason") == "embargo_2026")
    skip_reason_counts = _skip_reason_counts(all_skipped)
    payload = {
        "schema": "universe_result_v1",
        "run_start_utc": run_start_utc,
        "run_end_utc": run_end_utc,
        "total_elapsed_s": round(total_elapsed_s, 2),
        "git_commit": _git_commit(_REPO),
        "cli_args": cli_args,
        "latency_bands_ms": sorted(latency_bands),
        "units_run": len(unit_results),
        "units_skipped": len(all_skipped),
        "units_errored": sum(1 for u in unit_results if u.get("error")),
        "skip_reason_counts": skip_reason_counts,
        "embargo": {
            "start": RESEARCH_EMBARGO_START,
            "units_skipped_embargo": units_skipped_embargo,
        },
        **({"stage_a_filter": stage_a_filter} if stage_a_filter is not None else {}),
        **({"checkpoint": checkpoint} if checkpoint is not None else {}),
        "skipped": sorted(all_skipped, key=lambda s: (s["event_id"], s["symbol"], s["latency_ms"])),
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
        "aggregated": aggregated,
        "corrections": corrections,
        **({"robustness": robustness} if robustness is not None else {}),
        "unit_results": unit_results,
    }
    path = out_dir / "universe_result.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_universe_report(
    out_dir: Path,
    *,
    unit_results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    aggregated: dict[str, Any],
    corrections: dict[str, Any],
    latency_bands: list[float],
    stamp: dict[str, Any],
    run_start_utc: str,
    total_elapsed_s: float,
) -> Path:
    lines: list[str] = []
    all_skipped = _combined_skip_rows(skipped, unit_results)

    lines += [
        "# Universe screening report",
        "",
        f"- **run_start_utc:** {run_start_utc}",
        f"- **total_elapsed_s:** {total_elapsed_s:.1f}",
        f"- **latency_bands_ms:** {sorted(latency_bands)}",
        f"- **units_run:** {len(unit_results)}",
        f"- **units_skipped:** {len(all_skipped)}",
        f"- **units_errored:** {sum(1 for u in unit_results if u.get('error'))}",
        f"- **cert_status:** {stamp.get('status', 'MISSING')}",
        "",
    ]

    # --- Coverage stats ---
    event_types = sorted({
        u["event_type"] for u in unit_results
        if not u.get("error") and not u.get("skip_reason")
    })
    lines += ["## Coverage", "", "| event_type | events_run | events_skipped |", "|---|---|---|"]
    skip_by_etype: dict[str, set[str]] = {}
    for s in all_skipped:
        etype = str(s.get("event_type") or "unknown")
        skip_by_etype.setdefault(etype, set()).add(str(s.get("event_id") or "unknown"))

    run_by_etype: dict[str, set[str]] = {}
    for u in unit_results:
        if not u.get("error") and not u.get("skip_reason"):
            etype = u["event_type"]
            run_by_etype.setdefault(etype, set()).add(u["event_id"])
    for etype in sorted(set(event_types) | set(skip_by_etype)):
        n_events = len(run_by_etype.get(etype, set()))
        n_skipped_events = len(skip_by_etype.get(etype, set()))
        lines.append(f"| {etype} | {n_events} | {n_skipped_events} |")
    units_skipped_embargo = sum(1 for s in all_skipped if s.get("reason") == "embargo_2026")
    lines.append(f"\nEmbargoed (>= 2026-01-01): {units_skipped_embargo} units skipped")
    lines.append("")

    skip_reason_counts = _skip_reason_counts(all_skipped)
    if skip_reason_counts:
        lines += ["## Skip reason counts", "", "| reason | units_skipped |", "|---|---:|"]
        for reason, count in skip_reason_counts.items():
            lines.append(f"| {reason} | {count} |")
        lines.append("")

    # --- Survivors per event_type × band (Holm) ---
    lines += [
        "## Survivors after Holm correction (alpha=0.05)",
        "",
        "_p-value = ttest_1samp(per-event expectancies, 0); n_events >= 3 required_",
        "",
    ]
    for etype in sorted(corrections):
        holm = corrections[etype]["holm"]
        passed = holm["passed_slugs"]
        total = holm["total_tested"]
        lines.append(f"### {etype}")
        lines.append(f"Tested: {total}  |  Survivors: {len(passed)}")
        lines.append("")
        if passed:
            lines += ["| slug | p_value | adj_alpha | t_stat | n_trades | mean_expectancy_usd |",
                      "|---|---|---|---|---|---|"]
            slug_set = set(passed)
            for row in holm["sorted_results"]:
                if row["slug"] in slug_set:
                    lines.append(
                        f"| {row['slug']} | {row['p_value']:.4e} | {row['adjusted_alpha']:.4e} "
                        f"| {row['t_statistic']:.3f} | {row['num_trades']} "
                        f"| {row['metric_value']:.4f} |"
                    )
            lines.append("")
        else:
            lines += ["_No survivors._", ""]

    # --- BH survivors ---
    lines += [
        "## Survivors after Benjamini-Hochberg correction (alpha=0.05)",
        "",
    ]
    for etype in sorted(corrections):
        bh = corrections[etype]["benjamini_hochberg"]
        passed_bh = bh["passed_slugs"]
        lines.append(f"### {etype}")
        lines.append(f"Survivors: {len(passed_bh)}")
        lines.append("")
        if passed_bh:
            lines += ["| slug | p_value | adj_alpha | n_trades |",
                      "|---|---|---|---|"]
            slug_set_bh = set(passed_bh)
            for row in bh["sorted_results"]:
                if row["slug"] in slug_set_bh:
                    lines.append(
                        f"| {row['slug']} | {row['p_value']:.4e} "
                        f"| {row['adjusted_alpha']:.4e} | {row['num_trades']} |"
                    )
            lines.append("")
        else:
            lines += ["_No survivors._", ""]

    # --- Biggest negatives ---
    lines += ["## Biggest negatives (by mean_expectancy_usd, bottom 10)", ""]
    neg_rows: list[dict[str, Any]] = []
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                neg_rows.append({
                    "slug": f"hyp_{cell['hypothesis_id']}_band_{band}_{etype}",
                    "mean_expectancy_usd": cell["mean_expectancy_usd"],
                    "n_events": cell["n_events"],
                    "total_trades": cell["total_trades"],
                })
    neg_rows.sort(key=lambda r: r["mean_expectancy_usd"])
    if neg_rows:
        lines += ["| slug | mean_expectancy_usd | n_events | total_trades |",
                  "|---|---|---|---|"]
        for row in neg_rows[:10]:
            lines.append(
                f"| {row['slug']} | {row['mean_expectancy_usd']:.4f} "
                f"| {row['n_events']} | {row['total_trades']} |"
            )
        lines.append("")

    # --- Skipped ---
    if all_skipped:
        lines += ["## Skipped work units (explicit skip/rejection reasons)", ""]
        lines += [
            "| event_id | event_type | release_date | symbol | latency_ms | reason |",
            "|---|---|---|---|---|---|",
        ]
        for s in sorted(all_skipped, key=lambda x: (x["event_id"], x["symbol"], x["latency_ms"])):
            lines.append(
                f"| {s['event_id']} | {s.get('event_type', '')} | {s.get('release_date', '')} "
                f"| {s['symbol']} | {s['latency_ms']} | {s['reason']} |"
            )
        lines.append("")

    lines += [
        "## Aggregation notes",
        "",
        "- mean_expectancy / mean_win_rate / mean_adverse_selection: arithmetic mean of per-event BacktestResult values",
        "- p5_tail: 5th-percentile of per-event expectancies (worst-event, **not** worst-trade)",
        "- p-values: scipy.stats.ttest_1samp on per-event expectancies vs null=0; p=1.0 when n<3",
        "",
        f"_{format_stamp_footer(stamp)}_",
    ]

    path = out_dir / "universe_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_latency_bands(lane: str, bands_override: str | None) -> tuple[list[float], list[float]]:
    """Return (all_bands, measured_bands).

    measured_bands is non-empty only if chi404 order-ack is available.
    """
    if bands_override:
        base = sorted({float(b.strip()) for b in bands_override.split(",") if b.strip()})
    elif lane == "cme":
        base = list(sorted(set(LATENCY_BANDS_MS)))
    else:
        base = list(sorted(set(LATENCY_BANDS_MS)))

    measured: list[float] = []
    if DEFAULT_CHI404_SUMMARY.is_file():
        try:
            import json as _json
            summary = _json.loads(DEFAULT_CHI404_SUMMARY.read_text(encoding="utf-8"))
            ack_ms, measured_flag, _ = resolve_order_ack_ms(summary)
            if measured_flag and ack_ms is not None:
                measured = [round(float(ack_ms), 6)]
        except Exception:  # noqa: BLE001
            pass

    all_bands = sorted(set(base + measured))
    return all_bands, measured


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Full-universe hypothesis screening runner")
    p.add_argument("--lane", default="cme", choices=("cme",),
                   help="Latency lane; selects default bands from LATENCY_BANDS_MS (default: cme)")
    p.add_argument("--bands", default=None,
                   help='Override latency bands as comma-separated floats e.g. "0.5,1.0"')
    p.add_argument("--event-type", default=None, dest="event_type",
                   help="Filter to a single event_type (e.g. CPI)")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated symbol filter (default: all symbols in events.csv)")
    p.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV, dest="events_csv")
    p.add_argument("--out", type=Path, default=None,
                   help="Output dir (default: research_cards/universe_<utcstamp>)")
    p.add_argument("--workers", type=int,
                   default=max(1, (os.cpu_count() or 2) - 2),
                   help="Pool worker count (default: cpu_count-2)")
    p.add_argument("--max-events", type=int, default=None, dest="max_events",
                   help="Limit events processed (smoke runs)")
    p.add_argument("--rescan", action="store_true", default=False,
                   help="Skip manifest cache and scan NPZ dir directly (useful when manifest is stale)")
    p.add_argument("--from-stage-a", default=None, dest="from_stage_a", metavar="SURVIVORS_JSON",
                   help="Path to stage-A survivors JSON. Restricts hypotheses/event_types run.")
    p.add_argument("--cells", default=None,
                   help='Explicit cell override e.g. "46:CPI,12:NFP" (hyp_id:event_type pairs).')
    p.add_argument("--shard", default=None, metavar="I/N",
                   help=(
                       "Run only shard I of N (e.g. --shard 0/2 for the first half). "
                       "Assignment is hash(event_id|symbol|band) %% N == I, so both machines "
                       "scanning the lake independently produce the same partition. "
                       "Omit to run all work units."
                   ))
    args = p.parse_args(argv)

    # Validate shard arg early so the user gets a clear error before any I/O
    shard_spec: tuple[int, int] | None = None
    if args.shard is not None:
        try:
            shard_spec = parse_shard(args.shard)
        except ValueError as exc:
            p.error(str(exc))

    # ---------------------------------------------------------------------------
    # Stage-A filtering: parse survivors + cells; build allowed set
    # ---------------------------------------------------------------------------
    stage_a_filter: dict[str, Any] | None = None
    # allowed_cells: {(hyp_id, event_type)} union of stage-A survivors + pass_through
    allowed_cells: set[tuple[int, str]] | None = None
    # per_etype_hyp_ids: {event_type: set[hyp_id]} for _worker hyp_ids filter
    per_etype_hyp_ids: dict[str, set[int]] | None = None
    # tested_cells from the stage-A report (full family for Holm honesty)
    stage_a_tested_cells: list[dict[str, Any]] = []
    from_stage_a_sha256: str | None = None

    if args.from_stage_a:
        sa_path = Path(args.from_stage_a)
        sa_bytes = sa_path.read_bytes()
        from_stage_a_sha256 = hashlib.sha256(sa_bytes).hexdigest()
        sa_data = json.loads(sa_bytes.decode("utf-8"))
        survivors: list[dict[str, Any]] = sa_data.get("survivors", [])
        pass_through: list[Any] = sa_data.get("pass_through", [])
        stage_a_tested_cells = sa_data.get("tested_cells", [])

        # Full set of event_types present in the original stage-A family
        tested_etypes: set[str] = {tc["event_type"] for tc in stage_a_tested_cells if "event_type" in tc}
        # Surviving event_types (from survivors list) — used for work-unit restriction below
        surviving_etypes: set[str] = {s["event_type"] for s in survivors if "event_type" in s}

        allowed_cell_sources: dict[tuple[int, str], set[str]] = {}
        duplicate_same_source: set[tuple[int, str]] = set()

        def _add_allowed_cell(hyp_id: int, etype: str, source: str) -> None:
            cell = (int(hyp_id), str(etype))
            sources = allowed_cell_sources.setdefault(cell, set())
            if source in sources and source != "cells":
                duplicate_same_source.add(cell)
            sources.add(source)

        for s in survivors:
            if "hyp_id" in s and "event_type" in s:
                _add_allowed_cell(int(s["hyp_id"]), s["event_type"], "survivor")
        # pass_through hyps advance for ALL event_types in the original tested family
        for pt in pass_through:
            pt_id = int(pt) if isinstance(pt, (int, str)) else int(pt.get("hyp_id", pt))
            for etype in tested_etypes:
                _add_allowed_cell(pt_id, etype, "pass_through")

        # Explicit --cells override adds additional cells
        if args.cells:
            for token in args.cells.split(","):
                token = token.strip()
                if ":" in token:
                    hid_str, et = token.split(":", 1)
                    _add_allowed_cell(int(hid_str.strip()), et.strip(), "cells")

        if duplicate_same_source:
            p.error(f"Stage-A filter has duplicate source cells: {sorted(duplicate_same_source)}")
        allowed_cells = set(allowed_cell_sources)

        # Build per-event_type hyp_id sets for _worker filtering
        per_etype_hyp_ids = {}
        for hyp_id, etype in allowed_cells:
            per_etype_hyp_ids.setdefault(etype, set()).add(hyp_id)
        _validate_requested_hypothesis_ids(per_etype_hyp_ids, p=p)

        allowed_cells_count = len(allowed_cells)
        stage_a_filter = {
            "survivors_file": str(sa_path),
            "allowed_cells_count": allowed_cells_count,
            "placeholders_added": 0,  # filled in by _apply_corrections
        }
        print(
            f"Stage-A filter active: {allowed_cells_count} allowed cells from {sa_path.name}",
            flush=True,
        )

    utcstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or (_REPO / "research_cards" / f"universe_{utcstamp}")

    symbol_filter: list[str] | None = None
    if args.symbols:
        symbol_filter = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbol_filter:
        symbol_filter = [DEFAULT_SYMBOL]

    all_bands, measured_bands = _resolve_latency_bands(args.lane, args.bands)
    if measured_bands:
        print(f"CHI404 measured order-ack band(s) added: {measured_bands} ms (tagged 'measured')", flush=True)

    print(f"Loading lake index…", flush=True)
    lake_index = load_lake_index(_REPO, rescan=args.rescan)
    print(f"  lake index entries: {len(lake_index)}", flush=True)

    work_units, skipped = build_work_units(
        args.events_csv,
        lake_index,
        latency_bands=all_bands,
        event_type_filter=args.event_type,
        symbol_filter=symbol_filter,
        max_events=args.max_events,
    )

    # Stage-A: restrict work units to event_types with >=1 allowed cell;
    # stamp each unit with the hyp_ids to run for that event_type.
    if per_etype_hyp_ids is not None:
        allowed_etypes = set(per_etype_hyp_ids.keys())
        work_units = [
            {**u, "hyp_ids": sorted(per_etype_hyp_ids[u["event_type"]])}
            for u in work_units
            if u.get("event_type") in allowed_etypes
        ]
    work_units = _stamp_expected_hypothesis_ids(work_units)

    total_before_shard = len(work_units)
    if shard_spec is not None:
        shard_i, shard_n = shard_spec
        work_units = apply_shard(work_units, shard_i, shard_n)
        print(
            f"Shard {shard_i}/{shard_n}: {len(work_units)} of {total_before_shard} units assigned to this shard",
            flush=True,
        )

    # OPT 2: sort work units smallest-first by NPZ file size.
    # Rationale: cheap units complete early → first progress signal arrives
    # sooner; with imap_unordered the last worker to finish sets wall-clock, so
    # starting large files early reduces tail packing.
    # Stable tiebreak by (event_id, symbol, latency_ms) preserves determinism.
    def _unit_sort_key(u: dict) -> tuple:
        npz = u.get("npz_path", "")
        try:
            sz = os.path.getsize(npz) if npz else 0
        except OSError:
            sz = 0
        return (sz, u["event_id"], u["symbol"], float(u["latency_ms"]))

    work_units = sorted(work_units, key=_unit_sort_key)

    cli_args = {
        "lane": args.lane,
        "bands_override": args.bands,
        "event_type": args.event_type,
        "symbols": args.symbols,
        "events_csv": str(args.events_csv),
        "rescan": args.rescan,
        "workers": args.workers,
        "max_events": args.max_events,
        "from_stage_a": args.from_stage_a,
        "from_stage_a_sha256": from_stage_a_sha256,
        "cells": args.cells,
        "shard": args.shard,
        "shard_index": shard_spec[0] if shard_spec else None,
        "shard_total": shard_spec[1] if shard_spec else None,
    }
    checkpoint_path = out_dir / "unit_results.jsonl"
    checkpoint_results = _prepare_checkpoint_results(
        checkpoint_path,
        work_units,
        _checkpoint_context(cli_args),
    )
    checkpoint_keys = set(checkpoint_results)
    reused_results = [
        checkpoint_results[_unit_shard_key(unit)]
        for unit in work_units
        if _unit_shard_key(unit) in checkpoint_results
    ]
    total_work_units = len(work_units)
    work_units = [
        unit for unit in work_units
        if _unit_shard_key(unit) not in checkpoint_keys
    ]

    print(
        f"Work units: {total_work_units}  reused: {len(reused_results)}  "
        f"remaining: {len(work_units)}  skipped: {len(skipped)}",
        flush=True,
    )
    if total_work_units == 0:
        print("No work units — check --symbols, --event-type, and data/npz/ contents.", flush=True)
        # Still write minimal output so callers can see the skipped list
        stamp = build_certification_stamp(
            execution_mode="UNIVERSE_REPLAY",
            data_version="databento_mbo",
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        _early_embargo = sum(1 for s in skipped if s.get("reason") == "embargo_2026")
        payload = {
            "schema": "universe_result_v1",
            "run_start_utc": utcstamp,
            "run_end_utc": utcstamp,
            "total_elapsed_s": 0.0,
            "git_commit": _git_commit(_REPO),
            "units_run": 0,
            "units_skipped": len(skipped),
            "skip_reason_counts": _skip_reason_counts(skipped),
            "embargo": {
                "start": RESEARCH_EMBARGO_START,
                "units_skipped_embargo": _early_embargo,
            },
            "skipped": skipped,
            "certification_stamp": stamp,
        }
        (out_dir / "universe_result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_dir / 'universe_result.json'}", flush=True)
        return 0

    run_start_utc = datetime.now(timezone.utc).isoformat()
    t_start = time.monotonic()

    def _unit_status(r: dict[str, Any]) -> str:
        if r.get("skip_reason"):
            return "SKIP"
        if r.get("error"):
            return "ERROR"
        return "ok"

    # Fail-fast: never grind a whole universe when the CODE is broken. Empty NPZ
    # now SKIP instantly (no 12h grind), so only a pile of real ERRORs with zero
    # OK indicates a broken run — abort on that. Skips never trigger this (an
    # all-empty slice just finishes fast). Error-based ⇒ it also never fires on a
    # healthy run, so the pool is never torn down mid-flight.
    failfast_errors = int(os.environ.get("HFT3_UNIVERSE_FAILFAST_ERRORS", "100"))
    fresh_ok_count = 0
    fresh_err_count = 0
    aborted = False

    def _should_abort() -> bool:
        return fresh_err_count >= failfast_errors and fresh_ok_count == 0

    unit_results: list[dict[str, Any]] = list(reused_results)
    if args.workers == 1:
        # Sequential path — avoids spawn overhead in tests and single-core envs
        for i, unit in enumerate(work_units, 1):
            r = _worker(unit)
            st = _unit_status(r)
            fresh_ok_count += st == "ok"
            fresh_err_count += st == "ERROR"
            print(f"  [{i}/{len(work_units)}] {unit['event_id']} {unit['symbol']} {unit['latency_ms']}ms {st}", flush=True)
            unit_results.append(r)
            _append_checkpoint_result(checkpoint_path, r)
            if _should_abort():
                aborted = True
                break
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.workers, initializer=_init_worker) as pool:
            for i, result in enumerate(pool.imap_unordered(_worker, work_units), 1):
                st = _unit_status(result)
                fresh_ok_count += st == "ok"
                fresh_err_count += st == "ERROR"
                print(
                    f"  [{i}/{len(work_units)}] {result['event_id']} "
                    f"{result['symbol']} {result['latency_ms']}ms "
                    f"elapsed={result['elapsed_s']}s {st}",
                    flush=True,
                )
                unit_results.append(result)
                _append_checkpoint_result(checkpoint_path, result)
                if _should_abort():
                    aborted = True
                    break  # context-manager __exit__ tears the pool down safely

    if aborted:
        fresh_results = unit_results[len(reused_results):]
        n = len(fresh_results)
        n_err = sum(1 for u in fresh_results if u.get("error"))
        n_skip = sum(1 for u in fresh_results if u.get("skip_reason"))
        msg = (f"FAIL-FAST ABORT: {n_err} fresh errors with 0 fresh OK after {n} fresh units. "
               f"The replay path is broken (not empty data — empties skip). "
               f"Not grinding the remaining {len(work_units) - n} units. "
               f"Check the traceback above / HFT3_NPZ_ROOT.")
        print("\n" + "=" * 80 + f"\n{msg}\n" + "=" * 80, flush=True)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "universe_result.json").write_text(json.dumps({
            "schema": "universe_result_v1",
            "status": "ABORTED_NO_PROGRESS",
            "abort_reason": msg,
            "run_end_utc": datetime.now(timezone.utc).isoformat(),
            "units_processed": n, "units_ok": 0, "units_errored": n_err, "units_skipped": n_skip,
            "npz_root": os.environ.get("HFT3_NPZ_ROOT", "(default repo/data/npz)"),
        }, indent=2), encoding="utf-8")
        print(f"Wrote {out_dir / 'universe_result.json'} (status ABORTED_NO_PROGRESS)")
        return 2

    total_elapsed = time.monotonic() - t_start
    run_end_utc = datetime.now(timezone.utc).isoformat()

    # Sort unit_results for determinism
    unit_results.sort(key=lambda u: (u["event_id"], u["symbol"], float(u["latency_ms"])))

    aggregated = _aggregate_results(unit_results)
    corrections = _apply_corrections(
        aggregated,
        stage_a_tested_cells=stage_a_tested_cells if stage_a_filter else None,
        stage_a_filter=stage_a_filter,
    )

    # --- Robustness producers (additive; does not alter Holm/BH/placeholders) ---
    robustness = _compute_robustness(
        aggregated,
        unit_results=unit_results,
        stage_a_tested_cells=stage_a_tested_cells,
    )

    stamp = build_certification_stamp(
        execution_mode="UNIVERSE_REPLAY",
        data_version="databento_mbo",
        execution_adapter_mode="hftbacktest_simulated_exchange",
        queue_model="LogProbQueueModel2",
        fee_model="FeeModel",
    )

    result_path = write_universe_result(
        out_dir,
        unit_results=unit_results,
        skipped=skipped,
        aggregated=aggregated,
        corrections=corrections,
        robustness=robustness,
        latency_bands=all_bands,
        cli_args=cli_args,
        stamp=stamp,
        run_start_utc=run_start_utc,
        run_end_utc=run_end_utc,
        total_elapsed_s=total_elapsed,
        stage_a_filter=stage_a_filter,
        checkpoint={
            "path": str(checkpoint_path),
            "reused_units": len(reused_results),
            "new_units": len(unit_results) - len(reused_results),
            "remaining_units_started": len(work_units),
        },
    )
    report_path = write_universe_report(
        out_dir,
        unit_results=unit_results,
        skipped=skipped,
        aggregated=aggregated,
        corrections=corrections,
        latency_bands=all_bands,
        stamp=stamp,
        run_start_utc=run_start_utc,
        total_elapsed_s=total_elapsed,
    )

    n_errored = sum(1 for u in unit_results if u.get("error"))
    print(
        f"\nDone. units_run={len(unit_results)} errored={n_errored} "
        f"skipped={len(_combined_skip_rows(skipped, unit_results))}",
        flush=True,
    )
    print(f"Wrote {result_path}", flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
