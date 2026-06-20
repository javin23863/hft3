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


def _store_vocab_list(value: Any) -> list:
    """Coerce feature-store vocab to list without numpy truthiness checks."""
    if value is None:
        return []
    return list(value)


@dataclass(frozen=True)
class LeaderLegStore:
    """PIT-aligned leader feature-store rows for cross-asset futures screening."""

    symbol: str
    ts: np.ndarray
    X: np.ndarray


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
    leader_legs: tuple[tuple[str, LeaderLegStore], ...] = ()
    leader_resolution_source: str = "unknown"
    missing_leader_symbols: tuple[str, ...] = ()


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


def _primary_features_from_row(vec: np.ndarray) -> dict[str, float]:
    """Extract screening slots used by cross-asset and primary hypotheses."""
    out: dict[str, float] = {}
    if vec.shape[0] > 0:
        out["aggressor_volume_imbalance"] = float(vec[0])
    if vec.shape[0] > 40:
        out["mid"] = float(vec[40])
    return out


def _leader_leg_features_from_row(vec: np.ndarray) -> dict[str, float]:
    return dict(_primary_features_from_row(vec))


def _load_leader_leg_store(
    root: Path,
    event_id: str,
    leader_symbol: str,
) -> LeaderLegStore | None:
    for sym in _store_symbol_variants(leader_symbol):
        candidate_path = store_path(root, sym, event_id)
        if not candidate_path.is_file():
            continue
        try:
            store = load_store(candidate_path)
        except (OSError, ValueError):
            continue
        store_ts = store.get("ts")
        if store_ts is None:
            continue
        ts = np.asarray(store_ts, dtype=np.int64)
        if len(ts) < 2:
            continue
        store_x = store.get("X")
        if store_x is None:
            continue
        X = np.asarray(store_x, dtype=np.float64)
        if X.shape[0] != len(ts):
            continue
        base = str(leader_symbol).split(".")[0].upper()
        return LeaderLegStore(symbol=base, ts=ts, X=X)
    return None


def recipe_requires_cross_asset_leaders(
    *,
    metadata: Mapping[str, Any] | None = None,
    model_id: str = "",
) -> bool:
    """Shared detector for cross-asset leader-leg requirements."""
    meta = dict(metadata or {})
    recipe = meta.get("feature_recipe") or {}
    if recipe.get("cross_asset_legs") or recipe.get("leader_symbols"):
        return True
    family = str(meta.get("feature_family") or meta.get("proposal_reason") or "").lower()
    if "cross_asset" in family:
        return True
    return "CROSS_ASSET" in str(model_id or "").upper()


def _import_cross_asset_assembly_module():
    """Load cross_asset_assembly without importing replay package __init__."""
    import importlib.util
    import sys

    mod_path = Path(__file__).resolve().parents[2] / "replay" / "cross_asset_assembly.py"
    spec = importlib.util.spec_from_file_location(
        "replay_cross_asset_assembly_isolated", mod_path
    )
    if spec is None or spec.loader is None:
        raise ImportError("cross_asset_assembly module unavailable")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _default_leaders_for_target(target_symbol: str) -> tuple[tuple[str, ...], str]:
    """Leader list without importing replay package __init__ (hftbacktest chain).

    Returns ``(leaders, resolution_source)`` where *resolution_source* is
    ``cross_asset_module`` when the replay helper loaded, else ``static_fallback``.
    """
    try:
        leaders = _import_cross_asset_assembly_module().default_leaders_for_target(
            target_symbol
        )
        return tuple(str(x) for x in leaders), "cross_asset_module"
    except Exception:
        pass
    base = str(target_symbol or "MES").split(".")[0].upper()
    fallback = {
        "MES": ("ES",),
        "MNQ": ("NQ",),
        "ES": ("NQ", "ZN"),
        "NQ": ("ES", "ZN"),
    }
    return fallback.get(base, ("ES",)), "static_fallback"


def _load_leader_legs(
    root: Path,
    event_id: str,
    target_symbol: str,
) -> tuple[tuple[tuple[str, LeaderLegStore], ...], tuple[str, ...], str]:
    """Load PIT leader legs; return (loaded, missing_symbols, resolution_source)."""
    target = str(target_symbol).split(".")[0].upper()
    expected_leaders, resolution_source = _default_leaders_for_target(target)
    legs: list[tuple[str, LeaderLegStore]] = []
    missing: list[str] = []
    for leader in expected_leaders:
        leg = _load_leader_leg_store(root, event_id, leader)
        if leg is not None:
            legs.append((leader, leg))
        else:
            missing.append(leader)
    return tuple(legs), tuple(missing), resolution_source


def cross_asset_features_at_vis_ts(
    ctx: FsV1ScreenContext,
    vis_ts: int,
) -> dict[str, dict[str, Any]]:
    """Build provenance-tagged leader (+ optional VIX) legs at one PIT visibility time."""
    try:
        enrich_cross_leg = _import_cross_asset_assembly_module().enrich_cross_leg
    except ImportError:
        enrich_cross_leg = None

    cross_feats: dict[str, dict[str, Any]] = {}
    for leader, leg in ctx.leader_legs:
        j = int(np.searchsorted(leg.ts, vis_ts, side="right")) - 1
        if j < 0:
            continue
        leg_feats = _leader_leg_features_from_row(leg.X[j])
        if enrich_cross_leg is not None:
            cross_feats[leader] = enrich_cross_leg(
                leg_feats,
                symbol=leader,
                source_timestamp_ns=int(leg.ts[j]),
            )
        else:
            cross_feats[leader] = {
                **leg_feats,
                "_symbol": leader,
                "_source_timestamp_ns": int(leg.ts[j]),
            }
    if ctx.has_vix and ctx.vix_ts is not None and ctx.vix_X is not None:
        vix_j = int(np.searchsorted(ctx.vix_ts, vis_ts, side="right")) - 1
        if vix_j >= 0 and ctx.vix_cols:
            vix_feats = {
                col: float(ctx.vix_X[vix_j, ci])
                for ci, col in enumerate(ctx.vix_cols)
                if ci < ctx.vix_X.shape[1]
            }
            if enrich_cross_leg is not None:
                cross_feats["VIX"] = enrich_cross_leg(
                    vix_feats,
                    symbol="VIX",
                    source_timestamp_ns=int(ctx.vix_ts[vix_j]),
                )
            else:
                cross_feats["VIX"] = {
                    **vix_feats,
                    "_symbol": "VIX",
                    "_source_timestamp_ns": int(ctx.vix_ts[vix_j]),
                }
    return cross_feats


def sample_cross_asset_features_for_manifest(ctx: FsV1ScreenContext) -> dict[str, dict[str, Any]]:
    """Representative cross-asset legs at the final primary-store decision timestamp."""
    store_ts = ctx.store.get("ts")
    if store_ts is None:
        return {}
    ts = np.asarray(store_ts, dtype=np.int64)
    if len(ts) == 0:
        return {}
    feat_latency_ns = int(ctx.feature_latency_ms * 1_000_000)
    vis_ts = int(ts[-1]) - feat_latency_ns
    return cross_asset_features_at_vis_ts(ctx, vis_ts)


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
    leader_legs, missing_leaders, leader_source = _load_leader_legs(
        root, event_id, resolved_symbol
    )
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
        leader_legs=leader_legs,
        leader_resolution_source=leader_source,
        missing_leader_symbols=missing_leaders,
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


def compute_fs_v1_raw_signal_series(
    ctx: FsV1ScreenContext,
    cand: Any,
) -> np.ndarray:
    """Compute continuous fs_v1 hypothesis signal once (threshold applied later)."""
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
    event_ctx_vocab = _store_vocab_list(store.get("event_ctx_vocab"))
    regime_state_id = np.asarray(store["regime_state_id"])
    regime_state_vocab = _store_vocab_list(store.get("regime_state_vocab"))
    vol_state_id = np.asarray(store["vol_state_id"])
    vol_state_vocab = _store_vocab_list(store.get("vol_state_vocab"))
    liq_state_id = np.asarray(store["liq_state_id"])
    liq_state_vocab = _store_vocab_list(store.get("liq_state_vocab"))

    n = len(ts)
    signal = np.zeros(n, dtype=np.float64)
    feat_latency_ns = int(ctx.feature_latency_ms * 1_000_000)

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
        state.primary_features = _primary_features_from_row(vec_j)
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
        state.cross_asset_features = cross_asset_features_at_vis_ts(ctx, vis_ts)
        signal[i] = float(hypothesis.evaluate(state))

    return signal


def build_fs_v1_signal_computer(ctx: FsV1ScreenContext) -> Callable[..., Tuple[np.ndarray, np.ndarray]]:
    """Return a VectorBT signal_computer using PIT fs_v1 visible rows."""

    def compute(cand, ohlcv, parsed, repo_root) -> Tuple[np.ndarray, np.ndarray]:
        signal = compute_fs_v1_raw_signal_series(ctx, cand)
        signal_threshold = float(cand.strategy_params.get("signal_threshold", 0.0) or 0.0)
        entry_signal = np.where(signal > signal_threshold, 1.0, 0.0)
        exit_signal = np.where(signal < -signal_threshold, -1.0, 0.0)
        return entry_signal, exit_signal

    return compute


def fs_v1_feature_set_id() -> str:
    return FEATURE_VERSION


def fs_v1_feature_set_hash() -> str:
    return feature_index_hash()
