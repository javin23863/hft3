"""Point-in-time float metadata loader."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from equities_lane.src.ingest.daily_bars_io import load_daily_bars, load_daily_parquet
from equities_lane.src.models import DailyBar, FloatRecord


def load_float_csv(path: str | Path) -> list[FloatRecord]:
    rows: list[FloatRecord] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(
                FloatRecord(
                    symbol=row["symbol"].strip(),
                    as_of_date=row["as_of_date"].strip(),
                    float_shares=float(row["float_shares"]),
                    outstanding_shares=float(row["outstanding_shares"]),
                )
            )
    return rows


def lookup_float(
    records: list[FloatRecord],
    symbol: str,
    session_date: str,
) -> FloatRecord | None:
    """Point-in-time: latest record with as_of_date <= session_date."""
    target = date.fromisoformat(session_date)
    candidates = [
        r
        for r in records
        if r.symbol == symbol and date.fromisoformat(r.as_of_date) <= target
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda r: r.as_of_date)
    return candidates[-1]


def bars_before(
    bars: list[DailyBar],
    symbol: str,
    session_date: str,
) -> list[DailyBar]:
    target = date.fromisoformat(session_date)
    out = [
        b
        for b in bars
        if b.symbol == symbol and date.fromisoformat(b.date) < target
    ]
    out.sort(key=lambda b: b.date)
    return out
