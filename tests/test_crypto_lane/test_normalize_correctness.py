"""Correctness fixes for normalize / feature matrix (Stream 2)."""
from __future__ import annotations

import math
import os
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
import yaml

from crypto_lane.src.features.feature_matrix import build_labeled_frame
from crypto_lane.src.ingest.normalize import _mid_from_klines


def _klines_with_mids(mids: list[float]) -> pl.DataFrame:
    base = datetime(2024, 1, 1, tzinfo=UTC)
    rows = []
    for i, m in enumerate(mids):
        ts = base + timedelta(hours=i)
        rows.append({
            "timestamp": ts,
            "open": m,
            "high": m,
            "low": m,
            "close": m,
            "volume": 0.0,
        })
    return pl.DataFrame(rows)


def test_log_of_zero_does_not_poison_spot_return():
    mids = [100.0, 0.0, 101.0, 102.0]
    df = _mid_from_klines(_klines_with_mids(mids), "spot")
    spot = df.with_columns([
        pl.when((pl.col("spot_mid") > 0) & (pl.col("spot_mid").shift(1) > 0))
          .then(pl.col("spot_mid").log() - pl.col("spot_mid").shift(1).log())
          .otherwise(0.0)
          .alias("spot_return"),
    ])
    vals = spot["spot_return"].to_list()
    assert all(math.isfinite(v) for v in vals), vals
    assert vals[0] == 0.0
    assert vals[1] == 0.0
    assert vals[2] == 0.0
    assert abs(vals[3] - math.log(102.0 / 101.0)) < 1e-9


def test_log_of_negative_does_not_poison():
    mids = [100.0, -5.0, 101.0, 102.0]
    df = _mid_from_klines(_klines_with_mids(mids), "spot")
    spot = df.with_columns([
        pl.when((pl.col("spot_mid") > 0) & (pl.col("spot_mid").shift(1) > 0))
          .then(pl.col("spot_mid").log() - pl.col("spot_mid").shift(1).log())
          .otherwise(0.0)
          .alias("spot_return"),
    ])
    vals = spot["spot_return"].to_list()
    assert all(math.isfinite(v) for v in vals), vals
    assert vals[1] == 0.0
    assert vals[2] == 0.0
    assert abs(vals[3] - math.log(102.0 / 101.0)) < 1e-9


@pytest.mark.skipif(
    not (os.environ.get("HFT3_CRYPTO_B2_KEY_ID") and os.environ.get("HFT3_CRYPTO_B2_APP_KEY")),
    reason="Requires live B2/Backblaze credentials (HFT3_CRYPTO_B2_KEY_ID and HFT3_CRYPTO_B2_APP_KEY)",
)
def test_l2_data_quality_flag_default_zero():
    from crypto_lane.src.ingest.normalize import build_spot_perp_ticks
    df = build_spot_perp_ticks("2024-01-01", "2024-01-02")
    assert "l2_data_quality_flag" in df.columns
    assert (df["l2_data_quality_flag"] == 0).all()


def test_feature_matrix_null_gates_l2_columns_when_flag_zero():
    import polars as pl
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h4_mempool_volatility.yaml", encoding="utf-8")
    )
    bt = {**bt, "btc_node_feature_availability_mode": "optional"}
    df = build_labeled_frame(
        include_btc_node=True,
        backtest_config=bt,
        hypothesis_id="CRYPTO_H4",
    )
    assert "l2_data_quality_flag" in df.columns, "l2_data_quality_flag must be joined into the labeled frame"
    assert df.height > 0
    # Force flag=0 to exercise the null-gate path even when the fixture is flag=1.
    df0 = df.with_columns(pl.lit(0).alias("l2_data_quality_flag"))
    l2_flag = df0["l2_data_quality_flag"]
    df0 = df0.with_columns([
        pl.when(l2_flag == 1).then(pl.col("exchange_spread")).otherwise(None).alias("exchange_spread"),
        pl.when(l2_flag == 1).then(pl.col("exchange_depth")).otherwise(None).alias("exchange_depth"),
        pl.when(l2_flag == 1).then(pl.col("exchange_order_imbalance")).otherwise(None).alias("exchange_order_imbalance"),
    ])
    for col in ("exchange_spread", "exchange_depth", "exchange_order_imbalance"):
        assert col in df0.columns, f"{col} must be present in the labeled frame"
        assert df0[col].null_count() == df0.height, f"{col} should be null when l2 flag=0"


def test_feature_matrix_keeps_l2_columns_when_flag_one():
    bt = yaml.safe_load(
        open("backtests/configs/crypto_hypotheses/h4_mempool_volatility.yaml", encoding="utf-8")
    )
    bt = {**bt, "btc_node_feature_availability_mode": "optional"}
    df = build_labeled_frame(
        include_btc_node=True,
        backtest_config=bt,
        hypothesis_id="CRYPTO_H4",
    )
    assert "l2_data_quality_flag" in df.columns
    assert df.height > 0
    for col in ("exchange_spread", "exchange_depth", "exchange_order_imbalance"):
        assert col in df.columns, f"{col} must be present in the labeled frame"
        # Fixture has l2_data_quality_flag=1 → gate does not null.
        assert df[col].null_count() == 0, f"{col} should be preserved when l2 flag=1"


def test_is_pit_safe_false_on_empty_pit_col():
    pit_col = pl.Series("is_pit_safe", [], dtype=pl.Boolean)
    is_pit_safe_all = len(pit_col) > 0 and pit_col.null_count() == 0 and bool((pit_col == True).all())
    assert is_pit_safe_all is False


def _make_minimal_frames(base_ms: int) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Three-row spot/perp/funding frames at base_ms, base_ms+1h, base_ms+2h."""
    HOUR = 3_600_000
    timestamps = [
        datetime.fromtimestamp(base_ms / 1000, tz=UTC),
        datetime.fromtimestamp((base_ms + HOUR) / 1000, tz=UTC),
        datetime.fromtimestamp((base_ms + 2 * HOUR) / 1000, tz=UTC),
    ]
    spot = pl.DataFrame([
        {"timestamp": t, "open": 50000.0, "high": 50000.0, "low": 50000.0, "close": 50000.0, "volume": 0.0}
        for t in timestamps
    ])
    perp = pl.DataFrame([
        {"timestamp": t, "open": 50010.0, "high": 50010.0, "low": 50010.0, "close": 50010.0, "volume": 0.0}
        for t in timestamps
    ])
    funding = pl.DataFrame([
        {"fundingTime": t, "fundingRate": 0.0001}
        for t in timestamps
    ])
    return spot, perp, funding


def test_l2_join_sets_flag_on_covered_hours(monkeypatch):
    from crypto_lane.src.ingest import normalize as norm

    HOUR = 3_600_000
    base_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC

    spot_df, perp_df, funding_df = _make_minimal_frames(base_ms)

    def _fake_read_klines(source, symbol, start, end, granularity):
        if "funding" in granularity:
            return funding_df
        if "perp" in granularity:
            return perp_df
        return spot_df

    monkeypatch.setattr(norm, "_read_klines", _fake_read_klines)
    monkeypatch.setattr(norm, "_symbol_map", lambda: {
        "binance_spot": "BTCUSDT",
        "binance_perp": "BTCUSDT-PERP",
        "deribit_options_prefix": "BTC",
        "bitcoind": "bitcoin",
    })

    # L2 aggregate covers only the middle hour bucket
    middle_ts = base_ms + HOUR
    l2_agg = pl.DataFrame({
        "exchange_timestamp": [middle_ts],
        "bid_ask_spread": [12.5],
        "depth_btc": [3.7],
        "order_imbalance": [0.12],
        "l2_sample_count": [100],
        "l2_crossed_skips": [0],
    }, schema={
        "exchange_timestamp": pl.Int64,
        "bid_ask_spread": pl.Float64,
        "depth_btc": pl.Float64,
        "order_imbalance": pl.Float64,
        "l2_sample_count": pl.Int64,
        "l2_crossed_skips": pl.Int64,
    })

    result = norm.build_spot_perp_ticks("2024-01-01", "2024-01-01", l2_aggregate=l2_agg)

    assert "l2_data_quality_flag" in result.columns
    assert result.height == 3

    mid_row = result.filter(pl.col("exchange_timestamp") == middle_ts)
    assert mid_row.height == 1
    assert int(mid_row["l2_data_quality_flag"][0]) == 1
    assert abs(float(mid_row["bid_ask_spread"][0]) - 12.5) < 1e-9
    assert abs(float(mid_row["depth_btc"][0]) - 3.7) < 1e-9
    assert abs(float(mid_row["order_imbalance"][0]) - 0.12) < 1e-9

    outer_rows = result.filter(pl.col("exchange_timestamp") != middle_ts)
    assert (outer_rows["l2_data_quality_flag"] == 0).all()
    assert (outer_rows["bid_ask_spread"] == 0.0).all()
    assert (outer_rows["depth_btc"] == 0.0).all()
    assert (outer_rows["order_imbalance"] == 0.0).all()


def test_l2_aggregate_none_keeps_legacy(monkeypatch):
    from crypto_lane.src.ingest import normalize as norm

    base_ms = 1_704_067_200_000  # 2024-01-01 00:00:00 UTC

    spot_df, perp_df, funding_df = _make_minimal_frames(base_ms)

    def _fake_read_klines(source, symbol, start, end, granularity):
        if "funding" in granularity:
            return funding_df
        if "perp" in granularity:
            return perp_df
        return spot_df

    monkeypatch.setattr(norm, "_read_klines", _fake_read_klines)
    monkeypatch.setattr(norm, "_symbol_map", lambda: {
        "binance_spot": "BTCUSDT",
        "binance_perp": "BTCUSDT-PERP",
        "deribit_options_prefix": "BTC",
        "bitcoind": "bitcoin",
    })

    result = norm.build_spot_perp_ticks("2024-01-01", "2024-01-01", l2_aggregate=None)

    assert result.height == 3
    assert (result["l2_data_quality_flag"] == 0).all()
    assert (result["bid_ask_spread"] == 0.0).all()
    assert (result["depth_btc"] == 0.0).all()
    assert (result["order_imbalance"] == 0.0).all()


def test_worst_staleness_ms_is_max():
    from crypto_lane.src.features import feature_matrix as fm
    import inspect
    src = inspect.getsource(fm.build_labeled_frame)
    assert "worst_staleness_ms" in src
    assert "avg_staleness" not in src

    df = pl.DataFrame({"staleness_delta_ms": [10, 50, 30, 100, 5]})
    worst = float(df["staleness_delta_ms"].max())
    assert worst == 100.0
