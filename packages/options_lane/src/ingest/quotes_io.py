"""Load quote fixtures for parity lane."""
from __future__ import annotations

import json
from pathlib import Path

from options_lane.src.models import LegQuote


def load_quote_ndjson(path: str | Path) -> list[LegQuote]:
    quotes: list[LegQuote] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        quotes.append(
            LegQuote(
                role=str(row["role"]),
                symbol=str(row["symbol"]),
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                timestamp_ns=int(row["timestamp_ns"]),
                strike=float(row["strike"]) if row.get("strike") is not None else None,
                right=row.get("right"),
            )
        )
    return quotes
