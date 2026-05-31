"""Load daily OHLCV from CSV fixture or per-symbol parquet under data/equities/daily/."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from equities_lane.src.models import DailyBar


def load_daily_bars(path: str | Path, symbol: str | None = None) -> list[DailyBar]:
    p = Path(path)
    if p.is_dir():
        if not symbol:
            raise ValueError("symbol required when daily_bars path is a directory")
        parquet = p / f"{symbol.upper()}.parquet"
        if not parquet.exists():
            return []
        return load_daily_parquet(parquet, symbol)
    return _load_daily_csv(p)


def load_daily_parquet(path: Path, symbol: str) -> list[DailyBar]:
    if not path.exists():
        return []
    df = pd.read_parquet(path)
    sym = symbol.upper()
    if "symbol" in df.columns:
        df = df[df["symbol"].astype(str).str.upper() == sym]
    bars: list[DailyBar] = []
    for _, row in df.iterrows():
        bars.append(
            DailyBar(
                symbol=symbol,
                date=str(row["date"])[:10],
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
            )
        )
    return bars


def daily_coverage_calendar_days(path: Path, session_date: str) -> int:
    """Calendar span from earliest daily bar to session_date (exclusive)."""
    if not path.exists() or path.stat().st_size == 0:
        return 0
    df = pd.read_parquet(path)
    if df.empty or "date" not in df.columns:
        return 0
    dates = pd.to_datetime(df["date"])
    session = pd.Timestamp(session_date)
    before = dates[dates < session]
    if before.empty:
        return 0
    return int((session - before.min()).days)


def _load_daily_csv(path: Path) -> list[DailyBar]:
    import csv

    rows: list[DailyBar] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                DailyBar(
                    symbol=row["symbol"].strip(),
                    date=row["date"].strip(),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
    return rows
