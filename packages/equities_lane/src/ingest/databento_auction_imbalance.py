"""Databento auction imbalance schema pulls (quarantined equities lane)."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional


def pull_auction_imbalance(
    symbol: str,
    start_utc: datetime,
    end_utc: datetime,
    *,
    dataset: str = "XNAS.ITCH",
    output_path: Optional[Path] = None,
) -> Path:
    """Pull venue auction imbalance feed — not continuous book imbalance."""
    api_key = os.getenv("DATABENTO_API_KEY")
    if not api_key:
        raise RuntimeError("DATABENTO_API_KEY required for auction imbalance pull")
    import databento as db

    client = db.Historical(api_key)
    dest = output_path or Path(f"data/equities/raw/{symbol}_imbalance.dbn.zst")
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    client.timeseries.get_range(
        dataset=dataset,
        schema="imbalance",
        symbols=[symbol],
        stype_in="raw_symbol",
        start=start_utc,
        end=end_utc,
        path=str(dest),
    )
    return dest
