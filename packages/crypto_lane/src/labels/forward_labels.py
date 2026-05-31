"""Forward-only label builders (never mixed into feature columns)."""
from __future__ import annotations

import numpy as np
import polars as pl


LABEL_COLUMNS = frozenset({
    "forward_basis_change",
    "forward_basis_compression_flag",
    "forward_net_funding_after_hedge",
    "forward_funding_persistence",
    "forward_iv_rv_convergence",
    "forward_vol_regime_change",
    "forward_realized_volatility",
    "forward_jump_flag",
    "forward_liquidity_stress_flag",
    "forward_volatility_compression",
    "forward_spread_compression",
    "forward_depth_recovery",
    "forward_spread_change",
    "forward_depth_change",
    "forward_slippage_increase",
    "event_window_return",
    "event_window_realized_volatility",
    "event_window_spread_change",
    "event_window_basis_change",
    "after_cost_basis_trade_pnl_proxy",
    "after_cost_carry_pnl_proxy",
})


def _horizon_steps(df: pl.DataFrame, horizon_ms: int) -> int:
    ts = df["exchange_timestamp"].to_numpy()
    if ts.size < 2:
        return 1
    dt = int(np.median(np.diff(ts)))
    return max(1, round(horizon_ms / max(dt, 1)))


def _has(df: pl.DataFrame, *cols: str) -> bool:
    return all(c in df.columns for c in cols)


def attach_forward_labels(
    df: pl.DataFrame,
    *,
    horizon_ms: int = 1000,
    annualization: float = 365.0 * 24.0 * 3600.0,
    cost_assumptions: dict | None = None,
) -> pl.DataFrame:
    """
    Add forward labels using only future rows (shift negative).
    Rows without full forward window get null labels; caller filters on target.
    """
    h = _horizon_steps(df, horizon_ms)
    exprs: list[pl.Expr] = []

    if _has(df, "spot_perp_basis"):
        exprs.extend([
            (pl.col("spot_perp_basis").shift(-h) - pl.col("spot_perp_basis")).alias("forward_basis_change"),
            (
                pl.col("spot_perp_basis").shift(-h).abs() < pl.col("spot_perp_basis").abs()
            ).cast(pl.Int32).alias("forward_basis_compression_flag"),
        ])
    if _has(df, "expected_net_funding_after_cost"):
        exprs.append(
            (
                pl.col("expected_net_funding_after_cost").shift(-h)
                - pl.col("expected_net_funding_after_cost")
            ).alias("forward_net_funding_after_hedge")
        )
    if _has(df, "funding_level"):
        exprs.append(
            (pl.col("funding_level").shift(-h) - pl.col("funding_level")).alias("forward_funding_persistence")
        )
    if _has(df, "iv_rv_spread"):
        exprs.extend([
            (pl.col("iv_rv_spread").shift(-h) - pl.col("iv_rv_spread")).alias("forward_iv_rv_convergence"),
            (
                pl.col("iv_rv_spread").shift(-h).abs() < pl.col("iv_rv_spread").abs()
            ).cast(pl.Int32).alias("forward_vol_regime_change"),
        ])
    if _has(df, "spot_realized_volatility"):
        exprs.extend([
            pl.col("spot_realized_volatility").shift(-h).alias("forward_realized_volatility"),
            (
                pl.col("spot_realized_volatility").shift(-h) < pl.col("spot_realized_volatility")
            ).cast(pl.Int32).alias("forward_volatility_compression"),
        ])
    if _has(df, "btc_fee_spike_zscore"):
        exprs.append(
            (pl.col("btc_fee_spike_zscore").shift(-h) > 3.0).cast(pl.Int32).alias("forward_jump_flag")
        )
    if _has(df, "exchange_spread"):
        exprs.extend([
            (
                pl.col("exchange_spread").shift(-h) > pl.col("exchange_spread") * 1.05
            ).cast(pl.Int32).alias("forward_liquidity_stress_flag"),
            (
                pl.col("exchange_spread").shift(-h) < pl.col("exchange_spread")
            ).cast(pl.Int32).alias("forward_spread_compression"),
            (pl.col("exchange_spread").shift(-h) - pl.col("exchange_spread")).alias("forward_spread_change"),
            (
                pl.col("exchange_spread").shift(-h) / pl.col("exchange_spread").clip(lower_bound=1e-9) - 1.0
            ).alias("forward_slippage_increase"),
        ])
    if _has(df, "exchange_depth"):
        exprs.extend([
            (
                pl.col("exchange_depth").shift(-h) > pl.col("exchange_depth")
            ).cast(pl.Int32).alias("forward_depth_recovery"),
            (pl.col("exchange_depth").shift(-h) - pl.col("exchange_depth")).alias("forward_depth_change"),
        ])
    if not exprs:
        return df

    out = df.with_columns(exprs)

    event_exprs: list[pl.Expr] = []
    if _has(out, "spot_mid"):
        event_exprs.append(
            (pl.col("spot_mid").shift(-h) / pl.col("spot_mid") - 1.0).alias("event_window_return")
        )
    if _has(out, "exchange_spread"):
        event_exprs.append(
            (pl.col("exchange_spread").shift(-h) - pl.col("exchange_spread")).alias("event_window_spread_change")
        )
    if _has(out, "spot_perp_basis"):
        event_exprs.append(
            (pl.col("spot_perp_basis").shift(-h) - pl.col("spot_perp_basis")).alias("event_window_basis_change")
        )
    if _has(out, "spot_realized_volatility"):
        event_exprs.append(
            pl.col("spot_realized_volatility").shift(-h).alias("event_window_realized_volatility")
        )
    if event_exprs:
        out = out.with_columns(event_exprs)

    cost = cost_assumptions or {}
    fee_bps = float(cost.get("fee_bps", 2))
    spread_bps = float(cost.get("spread_bps", 1))
    cost_rate = (fee_bps + spread_bps) / 10_000.0

    cost_exprs: list[pl.Expr] = []
    if _has(out, "forward_basis_change", "exchange_spread"):
        cost_exprs.append(
            (
                -pl.col("forward_basis_change").abs()
                - pl.col("exchange_spread") * cost_rate
            ).alias("after_cost_basis_trade_pnl_proxy")
        )
    if _has(out, "forward_net_funding_after_hedge", "exchange_spread"):
        cost_exprs.append(
            (
                pl.col("forward_net_funding_after_hedge")
                - pl.col("exchange_spread") * cost_rate
            ).alias("after_cost_carry_pnl_proxy")
        )
    if cost_exprs:
        out = out.with_columns(cost_exprs)

    return out


def information_coefficient(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    if y_true.size < 2 or np.std(y_true) < 1e-12 or np.std(y_pred) < 1e-12:
        return 0.0
    return float(np.corrcoef(y_true, y_pred)[0, 1])
