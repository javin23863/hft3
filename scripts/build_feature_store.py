#!/usr/bin/env python3
"""Build the feature store: extract feature vectors from lake NPZs and write per-unit store files.

Spawn-pool safe: worker is top-level and picklable.  Only plain scalars cross the
process boundary.  Uses spawn context (same as run_event_universe.py) on all
platforms.

HFT3_FEATURE_BACKEND must be 'cpp' inside each worker — hard-fail if not.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths  # noqa: E402

setup_repo_paths()

from data_system.src.npz_resolver import npz_root  # noqa: E402
from data_system.src.feature_store import (  # noqa: E402
    FEATURE_VERSION,
    append_manifest,
    content_hash,
    feature_index_hash,
    feature_store_root,
    load_manifest,
    store_path,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
NPZ_PATTERN = re.compile(r"^(?P<symbol>.+?)_(?P<event_id>.+)_mbo\.npz$")
RESEARCH_EMBARGO_START = "2026-01-01"  # ALPHA_CME.md §4 / DEPLOYMENT.md §4.2: research sweeps must never read data >= this date; first 2026 touch is the M9 paper-shadow bundle.

# ---------------------------------------------------------------------------
# tick_size resolution (mirrors hfc3/events/l3_event_snapshot_tensor.py pattern)
# ---------------------------------------------------------------------------

_TICK_CACHE: dict[str, float] = {}


def _build_tick_cache(repo_root: Path) -> None:
    global _TICK_CACHE
    if _TICK_CACHE:
        return
    try:
        from hft3_bootstrap import workbench_root
        hot_path = workbench_root(repo_root) / "config" / "hot_memory_universe.yaml"
        if hot_path.is_file():
            import yaml
            raw = yaml.safe_load(hot_path.read_text(encoding="utf-8")) or {}
            for inst in raw.get("instruments") or []:
                sym = str(inst.get("research_symbol", ""))
                inc = inst.get("min_price_increment")
                if sym and inc is not None:
                    _TICK_CACHE[sym] = float(inc)
    except Exception:
        pass


def resolve_tick_size(symbol: str, repo_root: Path) -> float:
    """Resolve tick size for symbol via hot_memory_universe.yaml (same logic as
    hfc3/events/l3_event_snapshot_tensor._tick_size_for_symbol).
    Falls back to CME product defaults then 0.25."""
    _build_tick_cache(repo_root)
    if symbol in _TICK_CACHE:
        return _TICK_CACHE[symbol]
    base = symbol.split(".")[0]
    for sym, inc in _TICK_CACHE.items():
        if sym.split(".")[0] == base:
            return inc
    if base in ("ZN", "ZT", "UB"):
        return 0.015625
    if base == "ZB":
        return 0.03125
    if base in ("CL", "MCL", "NG"):
        return 0.01
    if base in ("GC", "MGC", "HG", "SI"):
        return 0.1
    if base == "6E":
        return 0.00005
    return 0.25


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO),
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def _read_events_csv(events_csv: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(events_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(dict(row))
    return rows


def _scan_lake(repo_root: Path) -> list[tuple[str, str, str]]:
    """Return sorted [(symbol, event_id, npz_path_str)] from lake scan."""
    root = npz_root(repo_root)
    units: list[tuple[str, str, str]] = []
    if not root.is_dir():
        return units
    for f in sorted(root.glob("*.npz")):
        m = NPZ_PATTERN.match(f.name)
        if m:
            units.append((m.group("symbol"), m.group("event_id"), str(f)))
    units.sort(key=lambda t: (t[0], t[1]))
    return units


# ---------------------------------------------------------------------------
# Worker (top-level — picklable for spawn pool)
# ---------------------------------------------------------------------------

def _worker(unit: dict[str, Any]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    _REPO_W = _Path(__file__).resolve().parents[1]
    if str(_REPO_W) not in sys.path:
        sys.path.insert(0, str(_REPO_W))
    from hft3_bootstrap import setup_repo_paths as _setup
    _setup()

    # Must set before importing pipeline so backend resolves correctly
    os.environ["HFT3_FEATURE_BACKEND"] = "cpp"

    from features_engine.src.pipeline.market_state_pipeline import MarketStatePipeline
    from features_engine.src.regime.event_context import EventContextEngine
    from features_engine.src.features.npz_feed import load_npz_events, iter_mbo_events
    from features_engine.src.features.feature_index import FeatureIndex, FEATURE_DIM
    from data_system.src.feature_store import (
        FEATURE_VERSION as _FV,
        feature_index_hash as _fih,
        content_hash as _ch,
        store_path as _sp,
        append_manifest as _am,
    )

    symbol: str = unit["symbol"]
    event_id: str = unit["event_id"]
    npz_path: str = unit["npz_path"]
    tick_size: float = float(unit["tick_size"])
    out_root = _Path(unit["out_root"])
    event_type: str = unit.get("event_type", "")
    release_date: str = unit.get("release_date", "")
    git_commit: str = unit.get("git_commit", "unknown")
    source_sha: str = unit["source_npz_sha256"]
    extractor_backend = "cpp"

    engine = EventContextEngine()
    pipeline = MarketStatePipeline(tick_size=tick_size, latency_ms=0.0, event_engine=engine)

    if pipeline.feature_backend != "cpp":
        raise RuntimeError(
            f"HFT3_FEATURE_BACKEND=cpp required but pipeline.feature_backend={pipeline.feature_backend!r} "
            f"for {symbol} {event_id}. Build hft3_features_cpp first."
        )

    t0 = time.monotonic()
    try:
        raw = load_npz_events(npz_path)
        # Detect zero-MBO placeholder NPZs (data.shape==(0,) lake entries for
        # contracts not yet listed at that event date, e.g. MES.v.0 pre-Jun-2019).
        _ts_arr = raw.get("ts") if hasattr(raw, "get") else getattr(raw, "ts", None)
        if _ts_arr is not None and np.asarray(_ts_arr).shape == (0,):
            return {
                "symbol": symbol, "event_id": event_id, "status": "empty_source",
                "error": None, "elapsed_s": 0.0,
            }
        events = list(iter_mbo_events(raw))
        if not events:
            return {
                "symbol": symbol, "event_id": event_id, "status": "empty_source",
                "error": None, "elapsed_s": 0.0,
            }

        ts_list: list[int] = []
        X_list: list[np.ndarray] = []
        event_ctx_list: list[str] = []
        regime_list: list[str] = []
        vol_list: list[str] = []
        liq_list: list[str] = []
        best_bid_list: list[float] = []
        best_ask_list: list[float] = []
        bbo_valid_list: list[bool] = []

        last_ev = None
        for ev in events:
            pipeline.process_event_fast(ev)
            last_ev = ev

        # Re-run collecting state per event
        engine2 = EventContextEngine()
        pipeline2 = MarketStatePipeline(tick_size=tick_size, latency_ms=0.0, event_engine=engine2)
        if pipeline2.feature_backend != "cpp":
            raise RuntimeError("cpp backend required")

        for ev in events:
            pipeline2.process_event_fast(ev)
            state = pipeline2.finalize()
            if state is None:
                continue
            ts_list.append(ev.timestamp_ns)
            vec = state.feature_vector.copy()
            X_list.append(vec)
            event_ctx_list.append(state.event_context or "NORMAL")
            regime_list.append(state.regime_state or "normal")
            vol_list.append(state.volatility_state or "normal")
            liq_list.append(state.liquidity_state or "normal")

            mid = float(vec[FeatureIndex.MID_PRICE])
            spread = float(vec[FeatureIndex.SPREAD])
            if spread > 0 and mid > 0:
                best_bid_list.append(mid - spread / 2.0)
                best_ask_list.append(mid + spread / 2.0)
                bbo_valid_list.append(True)
            else:
                best_bid_list.append(float("nan"))
                best_ask_list.append(float("nan"))
                bbo_valid_list.append(False)

        if not ts_list:
            return {
                "symbol": symbol, "event_id": event_id, "status": "error",
                "error": "no states produced", "elapsed_s": time.monotonic() - t0,
            }

        ts_arr = np.array(ts_list, dtype=np.int64)
        if not np.all(np.diff(ts_arr) >= 0):
            raise ValueError(f"ts not monotonic for {symbol} {event_id}")

        X_arr = np.array(X_list, dtype=np.float64)

        # Encode categoricals
        def _encode(vals: list[str]) -> tuple[np.ndarray, list[str]]:
            vocab = sorted(set(vals))
            vocab_map = {v: i for i, v in enumerate(vocab)}
            return np.array([vocab_map[v] for v in vals], dtype=np.int8), vocab

        event_ctx_vocab_list = sorted(set(event_ctx_list))
        event_ctx_map = {v: i for i, v in enumerate(event_ctx_vocab_list)}
        event_ctx_id = np.array([event_ctx_map[v] for v in event_ctx_list], dtype=np.int16)

        regime_id, regime_vocab = _encode(regime_list)
        vol_id, vol_vocab = _encode(vol_list)
        liq_id, liq_vocab = _encode(liq_list)

        best_bid_arr = np.array(best_bid_list, dtype=np.float64)
        best_ask_arr = np.array(best_ask_list, dtype=np.float64)
        bbo_valid_arr = np.array(bbo_valid_list, dtype=bool)

        fih = _fih()
        out_path = _sp(out_root, symbol, event_id)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        save_arrays: dict[str, Any] = {
            "ts": ts_arr,
            "X": X_arr,
            "event_ctx_id": event_ctx_id,
            "event_ctx_vocab": np.array(event_ctx_vocab_list),
            "regime_state_id": regime_id,
            "regime_state_vocab": np.array(regime_vocab),
            "vol_state_id": vol_id,
            "vol_state_vocab": np.array(vol_vocab),
            "liq_state_id": liq_id,
            "liq_state_vocab": np.array(liq_vocab),
            "best_bid": best_bid_arr,
            "best_ask": best_ask_arr,
            "bbo_valid": bbo_valid_arr,
            "feature_version": np.array(_FV),
            "extractor_backend": np.array(extractor_backend),
            "feature_index_hash": np.array(fih),
            "source_npz_sha256": np.array(source_sha),
            "tick_size": np.array(tick_size),
            "symbol": np.array(symbol),
            "event_id": np.array(event_id),
        }
        np.savez_compressed(str(out_path), **save_arrays)

        ch = _ch({k: v for k, v in save_arrays.items() if isinstance(v, np.ndarray)})
        n_rows = int(len(ts_arr))

        manifest_record = {
            "event_id": event_id,
            "symbol": symbol,
            "store_path": str(out_path.relative_to(out_root)),
            "feature_version": _FV,
            "extractor_backend": extractor_backend,
            "feature_index_hash": fih,
            "source_npz_sha256": source_sha,
            "content_hash": ch,
            "n_rows": n_rows,
            "event_type": event_type,
            "release_date": release_date,
            "git_commit": git_commit,
            "created_utc": datetime.now(timezone.utc).isoformat(),
        }
        _am(out_root, manifest_record)

        elapsed = time.monotonic() - t0
        bytes_written = out_path.stat().st_size
        return {
            "symbol": symbol,
            "event_id": event_id,
            "status": "built",
            "error": None,
            "elapsed_s": round(elapsed, 3),
            "n_rows": n_rows,
            "bytes_written": bytes_written,
            "store_path": str(out_path),
            "content_hash": ch,
            "feature_index_hash": fih,
            "source_npz_sha256": source_sha,
        }

    except Exception as exc:
        return {
            "symbol": symbol,
            "event_id": event_id,
            "status": "error",
            "error": str(exc),
            "elapsed_s": round(time.monotonic() - t0, 3),
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build feature store from lake NPZs.",
    )
    parser.add_argument("--symbols", default="", help="Comma-separated symbol filter (default all)")
    parser.add_argument("--event-type", default="", help="Filter by event_type column")
    parser.add_argument("--max-units", type=int, default=None, help="Cap total units to process")
    parser.add_argument(
        "--workers",
        type=int,
        default=max(1, (os.cpu_count() or 2) - 2),
        help="Parallel worker processes (default cpu-2)",
    )
    parser.add_argument("--rebuild", action="store_true", help="Force rebuild even if cached")
    parser.add_argument("--out-root", default="", help="Override feature store root path")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    symbol_filter = [s.strip() for s in args.symbols.split(",") if s.strip()] if args.symbols else []
    event_type_filter = args.event_type.strip() or None

    out_root = Path(args.out_root) if args.out_root else feature_store_root(_REPO)
    out_root.mkdir(parents=True, exist_ok=True)

    git_commit = _git_commit()
    fih = feature_index_hash()

    # Load events.csv for join
    events_rows: dict[str, dict[str, str]] = {}
    if DEFAULT_EVENTS_CSV.is_file():
        for row in _read_events_csv(DEFAULT_EVENTS_CSV):
            events_rows[row["event_id"]] = row

    # Scan lake
    raw_units = _scan_lake(_REPO)

    # Apply symbol filter
    if symbol_filter:
        raw_units = [(s, e, p) for s, e, p in raw_units if s in symbol_filter]

    # Apply event_type filter + join events.csv + embargo + missing event row
    units: list[dict[str, Any]] = []
    counts = {"built": 0, "skipped_cached": 0, "embargo": 0, "no_event_row": 0, "empty_source": 0, "errors": 0}
    total_bytes = 0

    # Load existing manifest for incremental check
    manifest = load_manifest(out_root)

    work_units: list[dict[str, Any]] = []
    skipped_summary: list[dict[str, str]] = []

    for symbol, event_id, npz_path_str in raw_units:
        npz_path = Path(npz_path_str)
        ev_row = events_rows.get(event_id)
        if ev_row is None:
            skipped_summary.append({"symbol": symbol, "event_id": event_id, "reason": "no_event_row"})
            counts["no_event_row"] += 1
            continue

        release_date = ev_row.get("release_date", "")
        etype = ev_row.get("event_type", "")

        if release_date >= RESEARCH_EMBARGO_START:
            skipped_summary.append({"symbol": symbol, "event_id": event_id, "reason": "embargo_2026"})
            counts["embargo"] += 1
            continue

        if event_type_filter and etype != event_type_filter:
            continue

        source_sha = _sha256(npz_path)
        tick = resolve_tick_size(symbol, _REPO)

        # Incremental: skip if manifest has matching (source_sha, feature_version, backend, fih)
        if not args.rebuild:
            existing = manifest.get((symbol, event_id))
            if existing and (
                existing.get("source_npz_sha256") == source_sha
                and existing.get("feature_version") == FEATURE_VERSION
                and existing.get("extractor_backend") == "cpp"
                and existing.get("feature_index_hash") == fih
            ):
                counts["skipped_cached"] += 1
                continue

        work_units.append({
            "symbol": symbol,
            "event_id": event_id,
            "npz_path": npz_path_str,
            "tick_size": tick,
            "out_root": str(out_root),
            "event_type": etype,
            "release_date": release_date,
            "git_commit": git_commit,
            "source_npz_sha256": source_sha,
        })

    # Sort deterministically
    work_units.sort(key=lambda u: (u["symbol"], u["event_id"]))

    if args.max_units is not None:
        work_units = work_units[: args.max_units]

    total = len(work_units)
    print(
        f"Feature store build: {total} units to process "
        f"({counts['skipped_cached']} cached, {counts['embargo']} embargo, "
        f"{counts['no_event_row']} no_event_row, {counts['empty_source']} empty_source)",
        flush=True,
    )

    results: list[dict[str, Any]] = []

    if args.workers <= 1 or total == 0:
        for i, unit in enumerate(work_units, 1):
            r = _worker(unit)
            results.append(r)
            if i % 100 == 0:
                done = sum(1 for x in results if x.get("status") == "built")
                print(f"  [{i}/{total}] built={done} errors={sum(1 for x in results if x.get('status')=='error')}", flush=True)
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.workers) as pool:
            for i, r in enumerate(pool.imap_unordered(_worker, work_units), 1):
                results.append(r)
                status = r.get("status", "error")
                if i % 100 == 0 or i == total:
                    done = sum(1 for x in results if x.get("status") == "built")
                    print(
                        f"  [{i}/{total}] {r['symbol']} {r['event_id']} "
                        f"status={status} elapsed={r.get('elapsed_s', '?')}s",
                        flush=True,
                    )

    for r in results:
        s = r.get("status", "error")
        if s == "built":
            counts["built"] += 1
            total_bytes += r.get("bytes_written", 0)
        elif s == "empty_source":
            counts["empty_source"] += 1
        else:
            counts["errors"] += 1

    print(
        f"\nDone. built={counts['built']} skipped_cached={counts['skipped_cached']} "
        f"embargo={counts['embargo']} no_event_row={counts['no_event_row']} "
        f"empty_source={counts['empty_source']} errors={counts['errors']} "
        f"total_bytes={total_bytes:,}",
        flush=True,
    )


if __name__ == "__main__":
    main()
