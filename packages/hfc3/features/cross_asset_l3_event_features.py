"""Phase 5 — cross-asset features from MBO-derived L3 snapshot tensor."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pandas as pd

EQUITY_CANONICAL = ("ES", "MES", "NQ", "MNQ", "YM", "MYM", "RTY", "M2K")
EQUITY_IMBALANCE_ORDER = EQUITY_CANONICAL
IMBALANCE_THRESHOLD = 0.15
RATES_CANONICAL = ("ZT", "ZF", "ZN", "ZB", "UB", "SR3", "ZQ")
METALS_CANONICAL = ("GC", "MGC", "HG")
ENERGY_CANONICAL = ("CL", "MCL", "NG")
FX_CANONICAL = ("6E",)
VOL_SENSORS = ("VIX_ATM_STRIKE", "VIX", "VVIX", "VX1", "VX2")


def _slice_offset(df: pd.DataFrame, offset_sec: int) -> pd.DataFrame:
    return df[df["offset_sec"] == offset_sec].copy()


def _row_for(df: pd.DataFrame, canonical: str) -> Optional[pd.Series]:
    sub = df[df["canonical_symbol"] == canonical]
    if sub.empty:
        return None
    return sub.iloc[0]


def _vacuum_score(row: Optional[pd.Series]) -> float:
    if row is None or row.get("mbo_missing"):
        return float("nan")
    return float(row.get("liquidity_vacuum_score", 0.0))


def _imbalance(row: Optional[pd.Series]) -> float:
    if row is None or row.get("mbo_missing"):
        return float("nan")
    return float(row.get("aggressor_volume_imbalance", 0.0))


def _first_row(df: pd.DataFrame, *canonicals: str) -> Optional[pd.Series]:
    for c in canonicals:
        row = _row_for(df, c)
        if row is not None:
            return row
    return None


def _mid(row: Optional[pd.Series]) -> float:
    if row is None or row.get("mbo_missing"):
        return float("nan")
    return float(row.get("mid_price", 0.0))


def _first_equity_imbalance_ordinal(tensor_df: pd.DataFrame, *, max_offset_sec: int) -> float:
    """Earliest offset <= max_offset_sec where an equity index crosses imbalance threshold."""
    first_hit: dict[str, int] = {}
    eligible = tensor_df[tensor_df["offset_sec"] <= max_offset_sec]
    for off in sorted(eligible["offset_sec"].unique()):
        snap = _slice_offset(eligible, int(off))
        for canon in EQUITY_IMBALANCE_ORDER:
            if canon in first_hit:
                continue
            row = _row_for(snap, canon)
            if row is None or row.get("mbo_missing"):
                continue
            imb = abs(float(row.get("aggressor_volume_imbalance", 0.0)))
            if imb >= IMBALANCE_THRESHOLD:
                first_hit[canon] = int(off)
    if not first_hit:
        return float("nan")
    winner = min(
        first_hit.items(),
        key=lambda item: (item[1], EQUITY_IMBALANCE_ORDER.index(item[0])),
    )
    return float(EQUITY_IMBALANCE_ORDER.index(winner[0]))


def build_cross_asset_l3_features(
    tensor_df: pd.DataFrame,
    *,
    offset_sec: int = 0,
    sensor_df: Optional[pd.DataFrame] = None,
) -> Dict[str, float]:
    """
    Build cross-asset MBO relationship features at one anchor offset.
    VIX/VVIX come from optional sensor_df (contextual, not MBO).
    """
    snap = _slice_offset(tensor_df, offset_sec)
    out: Dict[str, float] = {}

    es = _first_row(snap, "ES", "MES")
    nq = _first_row(snap, "NQ", "MNQ")
    ym = _first_row(snap, "YM", "MYM")
    rty = _first_row(snap, "RTY", "M2K")
    zn = _row_for(snap, "ZN")
    zb = _row_for(snap, "ZB")
    gc = _first_row(snap, "GC", "MGC")
    hg = _row_for(snap, "HG")
    cl = _first_row(snap, "CL", "MCL")
    ng = _row_for(snap, "NG")
    fx = _row_for(snap, "6E")

    es_mid = _mid(es)
    nq_mid = _mid(nq)
    if es_mid == es_mid and nq_mid == nq_mid:
        out["nq_minus_es_microstructure_pressure"] = nq_mid - es_mid

    rty_vac = _vacuum_score(rty)
    es_vac = _vacuum_score(es)
    if rty_vac == rty_vac and es_vac == es_vac:
        out["rty_minus_es_liquidity_stress"] = rty_vac - es_vac

    ym_imb = _imbalance(ym)
    es_imb = _imbalance(es)
    if ym_imb == ym_imb and es_imb == es_imb:
        out["ym_minus_es_orderflow_confirmation"] = ym_imb - es_imb

    vacs = [v for v in (_vacuum_score(es), _vacuum_score(nq), _vacuum_score(ym), _vacuum_score(rty)) if v == v]
    if vacs:
        out["equity_index_mbo_dispersion"] = float(pd.Series(vacs).std())

    ordinal = _first_equity_imbalance_ordinal(tensor_df, max_offset_sec=offset_sec)
    if ordinal == ordinal:
        out["first_equity_index_to_show_aggressor_imbalance"] = ordinal

    zn_vac = _vacuum_score(zn)
    if zn_vac == zn_vac:
        out["treasury_liquidity_vacuum_score"] = zn_vac
    if es_vac == es_vac and zn_vac == zn_vac:
        out["rates_first_vs_equities_first"] = zn_vac - es_vac

    gc_vac = _vacuum_score(gc)
    if gc_vac == gc_vac and zn_vac == zn_vac:
        out["gc_vs_zn_liquidity_stress"] = gc_vac - zn_vac

    cl_vac = _vacuum_score(cl)
    ng_vac = _vacuum_score(ng)
    if cl_vac == cl_vac:
        out["cl_orderflow_shock_score"] = cl_vac
    if ng_vac == ng_vac:
        out["ng_orderflow_shock_score"] = ng_vac

    fx_imb = _imbalance(fx)
    if fx_imb == fx_imb:
        out["dollar_pressure_mbo_proxy"] = fx_imb

    if sensor_df is not None and len(sensor_df):
        sub = sensor_df
        if "offset_sec" in sensor_df.columns:
            sub = sensor_df[sensor_df["offset_sec"] == offset_sec]
        if "sensor" in sub.columns:
            vix_row = sub[sub["sensor"].isin(("VIX_ATM_STRIKE", "VIX"))]
        else:
            vix_row = sub
        if len(vix_row):
            vix = float(vix_row.iloc[0].get("level", float("nan")))
            out["vix_atm_strike"] = vix
            if es_vac == es_vac and vix == vix:
                out["volatility_sensor_confirms_equity_mbo_stress"] = 1.0 if vix > 20 and es_vac > 0.5 else 0.0

    out["cross_asset_feature_count"] = float(len(out))
    return out


def tensor_to_cross_asset_frame(
    tensor_df: pd.DataFrame,
    offsets: Optional[List[int]] = None,
) -> pd.DataFrame:
    """One row per offset with cross-asset feature dict flattened."""
    offsets = offsets or sorted(tensor_df["offset_sec"].unique().tolist())
    rows = []
    for off in offsets:
        feats = build_cross_asset_l3_features(tensor_df, offset_sec=int(off))
        row = {"offset_sec": off, **feats}
        rows.append(row)
    return pd.DataFrame(rows)
