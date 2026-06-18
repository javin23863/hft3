"""fs_v1 row-loop VectorBT screening path (Phase 5).

Reuses feature-store NPZ rows with PIT visible-row selection, mirroring Stage A.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Tuple

import numpy as np

from data_system.src.feature_store import (
    FEATURE_VERSION,
    feature_index_hash,
    feature_store_root,
    load_manifest,
    load_store,
    store_path,
)

FS_V1_BAR_CONSTRUCTION_ID = "fs_v1_row_loop_from_feature_store"

REGIME_LABELS_ORDERED = (
    "normal",
    "event_shock",
    "liquidity_vacuum",
    "stop_cascade",
    "prop_flatten",
    "book_rebuild",
    "chop",
    "trend_continuation",
    "spread_stress",
)
_REGIME_SLOT_START = 41
_VIX_SYMBOL = "VIX.OPT"


@dataclass(frozen=True)
class FsV1ScreenContext:
    symbol: str
    event_id: str
    store_path: Path
    store: dict[str, Any]
    feature_latency_ms: float
    content_hash: str
    manifest_hash: str
    has_vix: bool
    vix_cols: tuple[str, ...]
    vix_ts: np.ndarray | None
    vix_X: np.ndarray | None


def _manifest_record_hash(record: Mapping[str, Any]) -> str:
    import hashlib
    import json

    payload = {k: record[k] for k in sorted(record) if k in {"symbol", "event_id", "content_hash", "feature_index_hash"}}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _store_symbol_variants(symbol: str) -> tuple[str, ...]:
    sym = str(symbol or "MES").strip()
    base = sym.split(".")[0].upper()
    variants: list[str] = []
    for candidate in (sym, f"{base}.v.0", base):
        if candidate and candidate not in variants:
            variants.append(candidate)
    return tuple(variants)


def resolve_fs_v1_screen_context(
    *,
    repo_root: Path,
    event_id: str,
    symbol: str,
    feature_store_root_override: Path | None = None,
    feature_latency_ms: float = 1.0,
) -> FsV1ScreenContext | None:
    root = feature_store_root_override or feature_store_root(repo_root)
    sp: Path | None = None
    resolved_symbol = symbol
    for sym in _store_symbol_variants(symbol):
        candidate_path = store_path(root, sym, event_id)
        if candidate_path.is_file():
            sp = candidate_path
            resolved_symbol = sym
            break
    if sp is None:
        return None
    try:
        store = load_store(sp)
    except (OSError, ValueError):
        return None
    if len(store.get("ts", [])) < 2:
        return None

    manifest = load_manifest(root)
    rec = manifest.get((symbol, event_id), {})
    content_hash = str(rec.get("content_hash") or "")
    manifest_hash = _manifest_record_hash(rec) if rec else "no_manifest_record"

    vix_ts: np.ndarray | None = None
    vix_X: np.ndarray | None = None
    vix_cols: list[str] = []
    vix_sp = store_path(root, _VIX_SYMBOL, event_id)
    if not vix_sp.is_file():
        alt = root / _VIX_SYMBOL / f"{_VIX_SYMBOL}_{event_id}_features_v1.npz"
        if alt.is_file():
            vix_sp = alt
        else:
            vix_sp = None  # type: ignore[assignment]
    if vix_sp is not None and Path(vix_sp).is_file():
        try:
            audit = {"ts", "ts_event_raw", "ts_recv_raw", "_attrs_json", "feature_index_hash"}
            with np.load(str(vix_sp), allow_pickle=True) as va:
                vix_ts = np.array(va["ts"], dtype=np.int64)
                if "columns" in va.files:
                    vix_cols = [str(c) for c in va["columns"]]
                    vix_X = np.array(va["X"])
                elif "X" in va.files:
                    vix_X = np.array(va["X"])
                else:
                    keys = sorted(
                        k
                        for k in va.files
                        if k not in audit
                        and va[k].ndim == 1
                        and va[k].dtype.kind == "f"
                        and len(va[k]) == len(vix_ts)
                    )
                    if keys:
                        vix_cols = keys
                        vix_X = np.column_stack([va[k] for k in keys])
        except (OSError, ValueError, KeyError):
            vix_ts = None
            vix_X = None
            vix_cols = []

    has_vix = vix_ts is not None and vix_X is not None and len(vix_ts) > 0
    return FsV1ScreenContext(
        symbol=resolved_symbol,
        event_id=event_id,
        store_path=sp,
        store=store,
        feature_latency_ms=float(feature_latency_ms),
        content_hash=content_hash,
        manifest_hash=manifest_hash,
        has_vix=has_vix,
        vix_cols=tuple(vix_cols),
        vix_ts=vix_ts,
        vix_X=vix_X,
    )


def ohlcv_from_feature_store(store: Mapping[str, Any]) -> np.ndarray:
    """Build OHLCV matrix with leading timestamp column from fs_v1 store rows."""
    ts = np.asarray(store["ts"], dtype=np.float64)
    X = np.asarray(store["X"], dtype=np.float64)
    n = len(ts)
    mid = X[:, 40] if X.shape[1] > 40 else np.zeros(n)
    best_bid = np.asarray(store.get("best_bid", mid), dtype=np.float64)
    best_ask = np.asarray(store.get("best_ask", mid), dtype=np.float64)
    bbo_valid = np.asarray(store.get("bbo_valid", np.ones(n, dtype=bool)))
    close = np.where(bbo_valid & (mid > 0), mid, (best_bid + best_ask) / 2.0)
    close = np.where(np.isfinite(close) & (close > 0), close, np.nan)
    if np.any(~np.isfinite(close)):
        last = 0.0
        for i in range(n):
            if np.isfinite(close[i]) and close[i] > 0:
                last = float(close[i])
            else:
                close[i] = last
    vol = np.ones(n, dtype=np.float64)
    # columns: ts, open, high, low, close, volume
    return np.column_stack([ts, close, close, close, close, vol])


def build_fs_v1_signal_computer(ctx: FsV1ScreenContext) -> Callable[..., Tuple[np.ndarray, np.ndarray]]:
    """Return a VectorBT signal_computer using PIT fs_v1 visible rows."""

    def compute(cand, ohlcv, parsed, repo_root) -> Tuple[np.ndarray, np.ndarray]:
        from features_engine.src.hypotheses.modules import MarketState
        from features_engine.src.hypotheses.registry import get_active_hypotheses
        from features_engine.src.model_registry import get_hyp_id_for_slug, resolve_model_id

        resolved = resolve_model_id(cand.model_id)
        hyp_id = get_hyp_id_for_slug(resolved)
        by_hyp_id = {h.hyp_id: h for h in get_active_hypotheses()}
        hypothesis = by_hyp_id.get(hyp_id)
        if hypothesis is None:
            raise ValueError(f"model_id {cand.model_id} (hyp_id={hyp_id}) not in active hypotheses")

        store = ctx.store
        ts = np.asarray(store["ts"], dtype=np.int64)
        X = np.asarray(store["X"], dtype=np.float64)
        event_ctx_id = np.asarray(store["event_ctx_id"])
        event_ctx_vocab = list(store.get("event_ctx_vocab") or [])
        regime_state_id = np.asarray(store["regime_state_id"])
        regime_state_vocab = list(store.get("regime_state_vocab") or [])
        vol_state_id = np.asarray(store["vol_state_id"])
        vol_state_vocab = list(store.get("vol_state_vocab") or [])
        liq_state_id = np.asarray(store["liq_state_id"])
        liq_state_vocab = list(store.get("liq_state_vocab") or [])

        n = len(ts)
        signal = np.zeros(n, dtype=np.float64)
        feat_latency_ns = int(ctx.feature_latency_ms * 1_000_000)
        signal_threshold = float(cand.strategy_params.get("signal_threshold", 0.0) or 0.0)

        state = MarketState(
            primary_features={},
            cross_asset_features={},
            regime_state="normal",
            event_context="NORMAL",
            volatility_state="NORMAL",
            liquidity_state="NORMAL",
            latency_ms=ctx.feature_latency_ms,
            current_inventory=0,
            feature_vector=None,
            regime_posterior={},
        )

        for i in range(n):
            vis_ts = int(ts[i]) - feat_latency_ns
            j = int(np.searchsorted(ts, vis_ts, side="right")) - 1
            if j < 0:
                continue
            vec_j = X[j]
            state.feature_vector = vec_j
            state.event_context = (
                str(event_ctx_vocab[int(event_ctx_id[j])]) if event_ctx_vocab else "NORMAL"
            )
            state.regime_state = (
                str(regime_state_vocab[int(regime_state_id[j])]) if regime_state_vocab else "normal"
            )
            state.volatility_state = (
                str(vol_state_vocab[int(vol_state_id[j])]) if vol_state_vocab else "NORMAL"
            )
            state.liquidity_state = (
                str(liq_state_vocab[int(liq_state_id[j])]) if liq_state_vocab else "NORMAL"
            )
            state.regime_posterior = {
                lbl: float(vec_j[_REGIME_SLOT_START + k])
                for k, lbl in enumerate(REGIME_LABELS_ORDERED)
            }
            if ctx.has_vix and ctx.vix_ts is not None and ctx.vix_X is not None:
                vix_j = int(np.searchsorted(ctx.vix_ts, vis_ts, side="right")) - 1
                if vix_j >= 0 and ctx.vix_cols:
                    state.cross_asset_features = {
                        "VIX": {
                            col: float(ctx.vix_X[vix_j, ci])
                            for ci, col in enumerate(ctx.vix_cols)
                            if ci < ctx.vix_X.shape[1]
                        }
                    }
                else:
                    state.cross_asset_features = {}
            else:
                state.cross_asset_features = {}

            signal[i] = float(hypothesis.evaluate(state))

        entry_signal = np.where(signal > signal_threshold, 1.0, 0.0)
        exit_signal = np.where(signal < -signal_threshold, -1.0, 0.0)
        return entry_signal, exit_signal

    return compute


def fs_v1_feature_set_id() -> str:
    return FEATURE_VERSION


def fs_v1_feature_set_hash() -> str:
    return feature_index_hash()
