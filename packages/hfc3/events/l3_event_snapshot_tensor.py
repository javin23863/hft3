"""Phase 4 — multi-symbol Level-3 MBO event snapshot tensor at anchor offsets."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from data_system.src.events_parser import load_and_parse_events
from features_engine.src.features.feature_index import vector_to_feature_dict
from features_engine.src.features.mbo_features import MBOFeatureExtractor, OrderBook
from features_engine.src.features.npz_feed import iter_mbo_events, load_npz_events
from features_engine.src.regime.event_context import EventContextEngine
from features_engine.src.regime.regime_filter import RegimeFilter
from data_system.src.event_data_resolver import load_sensor_df, resolve_mbo_npz_for_event
from economic_event_universe.snapshot_offsets import default_snapshot_offsets_sec
from hft3_bootstrap import data_system_root, workbench_root

SNAPSHOT_OFFSETS_SEC: Tuple[int, ...] = default_snapshot_offsets_sec()

REPO = Path(__file__).resolve().parents[2]
_TICK_SIZE_CACHE: Dict[str, float] = {}


def _tick_size_for_symbol(research_symbol: str, repo_root: Path) -> float:
    global _TICK_SIZE_CACHE
    if not _TICK_SIZE_CACHE:
        hot_path = workbench_root(repo_root) / "config" / "hot_memory_universe.yaml"
        if hot_path.is_file():
            import yaml

            raw = yaml.safe_load(hot_path.read_text(encoding="utf-8")) or {}
            for inst in raw.get("instruments") or []:
                sym = str(inst.get("research_symbol", ""))
                inc = inst.get("min_price_increment")
                if sym and inc is not None:
                    _TICK_SIZE_CACHE[sym] = float(inc)
    if research_symbol in _TICK_SIZE_CACHE:
        return _TICK_SIZE_CACHE[research_symbol]
    base = research_symbol.split(".")[0]
    for sym, inc in _TICK_SIZE_CACHE.items():
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


@dataclass
class L3SnapshotRow:
    event_id: str
    symbol: str
    canonical_symbol: str
    offset_sec: int
    anchor_ts_ns: int
    snapshot_ts_ns: int
    best_bid: float
    best_ask: float
    mid_price: float
    spread: float
    top_1_depth_bid: int
    top_1_depth_ask: int
    top_3_depth_bid: int
    top_3_depth_ask: int
    top_5_depth_bid: int
    top_5_depth_ask: int
    top_10_depth_bid: int
    top_10_depth_ask: int
    book_slope: float
    book_slope_change: float
    cancel_to_add_ratio: float
    near_touch_cancel_pressure: float
    bid_add_cancel_ratio: float
    ask_add_cancel_ratio: float
    buy_aggressor_volume: int
    sell_aggressor_volume: int
    aggressor_volume_imbalance: float
    queue_depletion_rate_bid: float
    queue_depletion_rate_ask: float
    refill_ratio: float
    liquidity_vacuum_score: float
    absorption_score: float
    iceberg_reload_score: float
    reload_drop_score: float
    realized_vol_state: float
    distance_to_round_number: float
    regime_posterior_json: str
    event_context: str
    latency_band_ms: float
    mbo_missing: bool
    mbo_degraded: bool
    data_source: str = "MBO_DERIVED"
    npz_symbol_used: str = ""


def _resolve_npz(
    repo_root: Path, event_id: str, research_symbol: str, parsed: Sequence[str]
) -> Tuple[Optional[Path], str]:
    path, present, sym_used = resolve_mbo_npz_for_event(
        repo_root, event_id, research_symbol, tuple(parsed)
    )
    if present:
        return path, sym_used
    return None, sym_used


def _capture_row(
    *,
    event_id: str,
    symbol: str,
    canonical: str,
    offset_sec: int,
    anchor_ns: int,
    snap_ns: int,
    book: OrderBook,
    extractor: MBOFeatureExtractor,
    event_ctx: str,
    regime_json: str,
    latency_ms: float,
    missing: bool,
    feat_dict: Optional[Dict[str, float]] = None,
    npz_symbol_used: str = "",
) -> L3SnapshotRow:
    if missing:
        return L3SnapshotRow(
            event_id=event_id,
            symbol=symbol,
            canonical_symbol=canonical,
            offset_sec=offset_sec,
            anchor_ts_ns=anchor_ns,
            snapshot_ts_ns=snap_ns,
            best_bid=0.0,
            best_ask=0.0,
            mid_price=0.0,
            spread=0.0,
            top_1_depth_bid=0,
            top_1_depth_ask=0,
            top_3_depth_bid=0,
            top_3_depth_ask=0,
            top_5_depth_bid=0,
            top_5_depth_ask=0,
            top_10_depth_bid=0,
            top_10_depth_ask=0,
            book_slope=0.0,
            book_slope_change=0.0,
            cancel_to_add_ratio=0.0,
            near_touch_cancel_pressure=0.0,
            bid_add_cancel_ratio=0.0,
            ask_add_cancel_ratio=0.0,
            buy_aggressor_volume=0,
            sell_aggressor_volume=0,
            aggressor_volume_imbalance=0.0,
            queue_depletion_rate_bid=0.0,
            queue_depletion_rate_ask=0.0,
            refill_ratio=0.0,
            liquidity_vacuum_score=0.0,
            absorption_score=0.0,
            iceberg_reload_score=0.0,
            reload_drop_score=0.0,
            realized_vol_state=0.0,
            distance_to_round_number=0.0,
            regime_posterior_json="{}",
            event_context=event_ctx,
            latency_band_ms=latency_ms,
            mbo_missing=True,
            mbo_degraded=True,
            data_source="MBO_PARTIAL_TAPE",
            npz_symbol_used=npz_symbol_used,
        )
    bid = book.get_best_bid()
    ask = book.get_best_ask()
    mid = (bid + ask) / 2.0 if bid > 0 and ask < float("inf") else 0.0
    spread = ask - bid if mid > 0 else 0.0
    d1b, d1a = book.top_k_depth(1)
    d3b, d3a = book.top_k_depth(3)
    d5b, d5a = book.top_k_depth(5)
    d10b, d10a = book.top_k_depth(10)
    fd = feat_dict or {}
    return L3SnapshotRow(
        event_id=event_id,
        symbol=symbol,
        canonical_symbol=canonical,
        offset_sec=offset_sec,
        anchor_ts_ns=anchor_ns,
        snapshot_ts_ns=snap_ns,
        best_bid=bid,
        best_ask=ask,
        mid_price=mid,
        spread=spread,
        top_1_depth_bid=d1b,
        top_1_depth_ask=d1a,
        top_3_depth_bid=d3b,
        top_3_depth_ask=d3a,
        top_5_depth_bid=d5b,
        top_5_depth_ask=d5a,
        top_10_depth_bid=d10b,
        top_10_depth_ask=d10a,
        book_slope=float(fd.get("book_slope", 0.0)),
        book_slope_change=float(fd.get("book_slope_change", 0.0)),
        cancel_to_add_ratio=float(fd.get("cancel_to_add_ratio", 0.0)),
        near_touch_cancel_pressure=float(fd.get("near_touch_cancel_pressure", 0.0)),
        bid_add_cancel_ratio=float(fd.get("bid_add_cancel_ratio", 0.0)),
        ask_add_cancel_ratio=float(fd.get("ask_add_cancel_ratio", 0.0)),
        buy_aggressor_volume=int(fd.get("buy_aggressor_volume", extractor.buy_agg_vol)),
        sell_aggressor_volume=int(fd.get("sell_aggressor_volume", extractor.sell_agg_vol)),
        aggressor_volume_imbalance=float(fd.get("aggressor_volume_imbalance", 0.0)),
        queue_depletion_rate_bid=float(fd.get("queue_depletion_rate_bid", 0.0)),
        queue_depletion_rate_ask=float(fd.get("queue_depletion_rate_ask", 0.0)),
        refill_ratio=float(fd.get("refill_ratio", 0.0)),
        liquidity_vacuum_score=float(fd.get("liquidity_vacuum_score", 0.0)),
        absorption_score=float(fd.get("absorption_score", 0.0)),
        iceberg_reload_score=float(fd.get("iceberg_reload_score", 0.0)),
        reload_drop_score=float(fd.get("reload_drop_score", 0.0)),
        realized_vol_state=float(fd.get("realized_vol_state", 0.0)),
        distance_to_round_number=float(
            fd.get("distance_to_round_number", 0.0)
        ),
        regime_posterior_json=regime_json,
        event_context=event_ctx,
        latency_band_ms=latency_ms,
        mbo_missing=missing,
        mbo_degraded=missing,
        data_source="MBO_DERIVED",
        npz_symbol_used=npz_symbol_used,
    )


def _walk_snapshots_for_symbol(
    repo_root: Path,
    event_id: str,
    research_symbol: str,
    canonical: str,
    parsed_symbols: Sequence[str],
    anchor_ns: int,
    target_offsets_ns: Dict[int, int],
    latency_ms: float,
    event_engine: EventContextEngine,
) -> List[L3SnapshotRow]:
    npz_path, sym_used = _resolve_npz(repo_root, event_id, research_symbol, parsed_symbols)
    if npz_path is None:
        rows = []
        for off_sec, snap_ns in sorted(target_offsets_ns.items(), key=lambda x: x[0]):
            rows.append(
                L3SnapshotRow(
                    event_id=event_id,
                    symbol=research_symbol,
                    canonical_symbol=canonical,
                    offset_sec=off_sec,
                    anchor_ts_ns=anchor_ns,
                    snapshot_ts_ns=snap_ns,
                    best_bid=0.0,
                    best_ask=0.0,
                    mid_price=0.0,
                    spread=0.0,
                    top_1_depth_bid=0,
                    top_1_depth_ask=0,
                    top_3_depth_bid=0,
                    top_3_depth_ask=0,
                    top_5_depth_bid=0,
                    top_5_depth_ask=0,
                    top_10_depth_bid=0,
                    top_10_depth_ask=0,
                    book_slope=0.0,
                    book_slope_change=0.0,
                    cancel_to_add_ratio=0.0,
                    near_touch_cancel_pressure=0.0,
                    bid_add_cancel_ratio=0.0,
                    ask_add_cancel_ratio=0.0,
                    buy_aggressor_volume=0,
                    sell_aggressor_volume=0,
                    aggressor_volume_imbalance=0.0,
                    queue_depletion_rate_bid=0.0,
                    queue_depletion_rate_ask=0.0,
                    refill_ratio=0.0,
                    liquidity_vacuum_score=0.0,
                    absorption_score=0.0,
                    iceberg_reload_score=0.0,
                    reload_drop_score=0.0,
                    realized_vol_state=0.0,
                    distance_to_round_number=0.0,
                    regime_posterior_json="{}",
                    event_context="UNKNOWN",
                    latency_band_ms=latency_ms,
                    mbo_missing=True,
                    mbo_degraded=True,
                    data_source="MBO_MISSING",
                    npz_symbol_used="",
                )
            )
        return rows

    tick = _tick_size_for_symbol(sym_used, repo_root)
    extractor = MBOFeatureExtractor(tick_size=tick)
    regime_filter = RegimeFilter()
    raw = load_npz_events(str(npz_path))
    events = list(iter_mbo_events(raw))
    if not events:
        return [
            L3SnapshotRow(
                event_id=event_id,
                symbol=research_symbol,
                canonical_symbol=canonical,
                offset_sec=off_sec,
                anchor_ts_ns=anchor_ns,
                snapshot_ts_ns=snap_ns,
                best_bid=0.0,
                best_ask=0.0,
                mid_price=0.0,
                spread=0.0,
                top_1_depth_bid=0,
                top_1_depth_ask=0,
                top_3_depth_bid=0,
                top_3_depth_ask=0,
                top_5_depth_bid=0,
                top_5_depth_ask=0,
                top_10_depth_bid=0,
                top_10_depth_ask=0,
                book_slope=0.0,
                book_slope_change=0.0,
                cancel_to_add_ratio=0.0,
                near_touch_cancel_pressure=0.0,
                bid_add_cancel_ratio=0.0,
                ask_add_cancel_ratio=0.0,
                buy_aggressor_volume=0,
                sell_aggressor_volume=0,
                aggressor_volume_imbalance=0.0,
                queue_depletion_rate_bid=0.0,
                queue_depletion_rate_ask=0.0,
                refill_ratio=0.0,
                liquidity_vacuum_score=0.0,
                absorption_score=0.0,
                iceberg_reload_score=0.0,
                reload_drop_score=0.0,
                realized_vol_state=0.0,
                distance_to_round_number=0.0,
                regime_posterior_json="{}",
                event_context="UNKNOWN",
                latency_band_ms=latency_ms,
                mbo_missing=True,
                mbo_degraded=True,
                data_source="MBO_PARTIAL_TAPE",
                npz_symbol_used=sym_used,
            )
            for off_sec, snap_ns in sorted(target_offsets_ns.items(), key=lambda x: x[0])
        ]

    last_ts = -1
    for mbo in events:
        if mbo.timestamp_ns < last_ts:
            raise ValueError(f"NPZ {npz_path} local_ts not monotonic at {mbo.timestamp_ns}")
        last_ts = mbo.timestamp_ns

    max_ns = max(target_offsets_ns.values())
    captured: Dict[int, L3SnapshotRow] = {}
    pending = sorted(target_offsets_ns.items(), key=lambda x: x[1])

    for mbo in events:
        if mbo.timestamp_ns > max_ns:
            break
        while pending and mbo.timestamp_ns > pending[0][1]:
            off_sec, snap_ns = pending.pop(0)
            ctx = event_engine.resolve_ns(snap_ns)
            pre_feat = vector_to_feature_dict(extractor._vec)  # noqa: SLF001 — F_t at snap_ns
            posterior = regime_filter.update(pre_feat, ctx)
            captured[off_sec] = _capture_row(
                event_id=event_id,
                symbol=research_symbol,
                canonical=canonical,
                offset_sec=off_sec,
                anchor_ns=anchor_ns,
                snap_ns=snap_ns,
                book=extractor.book,
                extractor=extractor,
                event_ctx=ctx,
                regime_json=json.dumps(posterior),
                latency_ms=latency_ms,
                missing=False,
                feat_dict=pre_feat,
                npz_symbol_used=sym_used,
            )
        vec = extractor.process_event(mbo)
        feat_dict = vector_to_feature_dict(vec)
        while pending and mbo.timestamp_ns == pending[0][1]:
            off_sec, snap_ns = pending.pop(0)
            ctx = event_engine.resolve_ns(snap_ns)
            posterior = regime_filter.update(feat_dict, ctx)
            captured[off_sec] = _capture_row(
                event_id=event_id,
                symbol=research_symbol,
                canonical=canonical,
                offset_sec=off_sec,
                anchor_ns=anchor_ns,
                snap_ns=snap_ns,
                book=extractor.book,
                extractor=extractor,
                event_ctx=ctx,
                regime_json=json.dumps(posterior),
                latency_ms=latency_ms,
                missing=False,
                feat_dict=feat_dict,
                npz_symbol_used=sym_used,
            )

    # fill offsets never reached by tape
    for off_sec, snap_ns in pending:
        ctx = event_engine.resolve_ns(snap_ns)
        captured[off_sec] = _capture_row(
            event_id=event_id,
            symbol=research_symbol,
            canonical=canonical,
            offset_sec=off_sec,
            anchor_ns=anchor_ns,
            snap_ns=snap_ns,
            book=extractor.book,
            extractor=extractor,
            event_ctx=ctx,
            regime_json="{}",
            latency_ms=latency_ms,
            missing=True,
            npz_symbol_used=sym_used,
        )
    return [captured[k] for k in sorted(captured.keys())]


def build_l3_event_tensor(
    repo_root: Path,
    event_id: str,
    *,
    symbols: Optional[Sequence[str]] = None,
    latency_band_ms: float = 1.0,
    offsets_sec: Sequence[int] = SNAPSHOT_OFFSETS_SEC,
) -> pd.DataFrame:
    """Build MBO-derived snapshot tensor rows for all requested symbols."""
    csv_path = data_system_root(repo_root) / "config" / "events.csv"
    df = load_and_parse_events(str(csv_path))
    row = df[df["event_id"] == event_id]
    if row.empty:
        raise ValueError(f"event_id not in events.csv: {event_id}")
    r = row.iloc[0]
    anchor_ns = int(pd.Timestamp(r["anchor_utc"]).tz_convert("UTC").value)

    parsed = [x.strip() for x in str(r["symbols"]).split(",")]
    sym_list = list(symbols) if symbols else parsed
    target_offsets_ns = {off: anchor_ns + off * 1_000_000_000 for off in offsets_sec}

    event_engine = EventContextEngine(str(csv_path), event_id=event_id)
    all_rows: List[L3SnapshotRow] = []

    canonical_map = {}
    hot_path = workbench_root(repo_root) / "config" / "hot_memory_universe.yaml"
    if hot_path.is_file():
        import yaml

        raw = yaml.safe_load(hot_path.read_text(encoding="utf-8")) or {}
        for inst in raw.get("instruments") or []:
            canonical_map[str(inst.get("research_symbol", ""))] = str(
                inst.get("canonical_internal_symbol", "")
            )

    for sym in sym_list:
        canonical = canonical_map.get(sym, sym.split(".")[0])
        all_rows.extend(
            _walk_snapshots_for_symbol(
                repo_root,
                event_id,
                sym,
                canonical,
                parsed,
                anchor_ns,
                dict(target_offsets_ns),
                latency_band_ms,
                event_engine,
            )
        )

    return pd.DataFrame([asdict(x) for x in all_rows])


def build_event_cross_asset_frame(
    repo_root: Path,
    event_id: str,
    *,
    offsets_sec: Sequence[int] | None = None,
    **tensor_kwargs: Any,
) -> pd.DataFrame:
    """Cross-asset features from L3 tensor + optional VIX sensor parquet."""
    from hfc3.features.cross_asset_l3_event_features import build_cross_asset_l3_features

    tensor_df = build_l3_event_tensor(repo_root, event_id, offsets_sec=offsets_sec, **tensor_kwargs)
    sensor_df = load_sensor_df(repo_root, event_id)
    offs = offsets_sec or SNAPSHOT_OFFSETS_SEC
    rows = []
    for off in offs:
        sensor_slice = sensor_df
        if len(sensor_df) and "offset_sec" in sensor_df.columns:
            sensor_slice = sensor_df[sensor_df["offset_sec"] == int(off)]
        feats = build_cross_asset_l3_features(
            tensor_df, offset_sec=int(off), sensor_df=sensor_slice
        )
        rows.append({"offset_sec": int(off), **feats})
    return pd.DataFrame(rows)


def write_l3_event_tensor(
    repo_root: Path,
    event_id: str,
    *,
    output_dir: Optional[Path] = None,
    **kwargs: Any,
) -> Tuple[Path, Path]:
    """Write parquet tensor + meta json under runtime/event_snapshots/."""
    out_dir = output_dir or (repo_root / "runtime" / "event_snapshots")
    out_dir.mkdir(parents=True, exist_ok=True)
    df = build_l3_event_tensor(repo_root, event_id, **kwargs)
    sensor_df = load_sensor_df(repo_root, event_id)
    parquet_path = out_dir / f"{event_id}_l3_tensor.parquet"
    meta_path = out_dir / f"{event_id}_l3_tensor_meta.json"
    df.to_parquet(parquet_path, index=False)
    meta = {
        "event_id": event_id,
        "offsets_sec": list(kwargs.get("offsets_sec") or SNAPSHOT_OFFSETS_SEC),
        "row_count": int(len(df)),
        "symbols": sorted(df["symbol"].unique().tolist()) if len(df) else [],
        "mbo_missing_symbols": sorted(df.loc[df["mbo_missing"], "symbol"].unique().tolist())
        if len(df)
        else [],
        "data_source": "MBO_DERIVED",
        "filtration": "snapshot at T+offset uses MBO events with timestamp_ns <= snapshot_ts_ns",
        "sensor_present": bool(len(sensor_df)),
        "sensor_rows": int(len(sensor_df)),
    }
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return parquet_path, meta_path
