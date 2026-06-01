"""Bronze reader schema validation tests (Phase B-1)."""
from __future__ import annotations

import polars as pl
import pytest

import crypto_lane.src.ingest.bronze_reader as br
from crypto_lane.src.ingest.bronze_reader import (
    EXPECTED_FUNDING_COLUMNS,
    EXPECTED_SPOT_COLUMNS,
    BronzeReadError,
    _validate_columns,
)

def _full_spot_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "exchange_timestamp": [1, 2, 3],
            "open": [1.0, 2.0, 3.0],
            "high": [1.0, 2.0, 3.0],
            "low": [1.0, 2.0, 3.0],
            "close": [1.0, 2.0, 3.0],
            "volume": [1.0, 2.0, 3.0],
        }
    )


def _full_funding_frame() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "last_funding_rate": ["0.0001", "0.0002", "0.0003"],
            "funding_interval_hours": ["8", "8", "8"],
        }
    )


def test_validate_columns_passes_for_complete_schema():
    _validate_columns(_full_spot_frame(), EXPECTED_SPOT_COLUMNS, "spot_klines")
    _validate_columns(_full_funding_frame(), EXPECTED_FUNDING_COLUMNS, "perp_funding_rate")


def test_validate_columns_raises_on_missing():
    df = _full_spot_frame()  # missing last_funding_rate
    with pytest.raises(BronzeReadError) as ei:
        _validate_columns(df, EXPECTED_FUNDING_COLUMNS, "perp_funding_rate")
    assert "schema drift" in str(ei.value)
    assert "last_funding_rate" in str(ei.value)


def test_read_parquet_raises_on_schema_drift(monkeypatch, tmp_path):
    """When the underlying parquet read returns a frame with bad columns,
    read_parquet_key must surface a BronzeReadError, not a KeyError."""
    # Use a perp-funding key so read_parquet_key dispatches to the funding
    # validator (which requires `last_funding_rate` and `funding_interval_hours`).
    cache = tmp_path / "k.parquet"
    _full_spot_frame().write_parquet(cache)
    monkeypatch.setattr(br, "_local_cache_path", lambda key: cache)
    monkeypatch.setattr(pl, "read_parquet", lambda *_a, **_k: _full_spot_frame())

    with pytest.raises(BronzeReadError) as ei:
        br.read_parquet_key("perp_funding_rate_x.parquet", source="binance")
    msg = str(ei.value)
    assert "schema drift" in msg
    assert "last_funding_rate" in msg


def test_read_parquet_accepts_real_perp_funding_data():
    """Regression: the validator must accept the actual perp_funding_rate schema
    (last_funding_rate + funding_interval_hours, no OHLCV)."""
    real_perp = pl.DataFrame(
        {
            "calc_time_ms": ["1704067200000"],
            "funding_interval_hours": ["8"],
            "last_funding_rate": ["0.0001"],
            "timestamp": [None],
            "symbol": ["BTCUSDT"],
        }
    )
    for col in ("funding_interval_hours", "last_funding_rate"):
        real_perp = real_perp.with_columns(pl.col(col).cast(pl.String))
    _validate_columns(real_perp, EXPECTED_FUNDING_COLUMNS, "perp_funding_rate_8h")
