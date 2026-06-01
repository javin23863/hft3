"""Load quote fixtures for parity lane."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from options_lane.src.models import LegQuote


def load_quote_ndjson(path: str | Path) -> list[LegQuote]:
    quotes: list[LegQuote] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"Warning: skipping malformed NDJSON line in {path}: {exc}", file=sys.stderr)
            continue
        try:
            quotes.append(
                LegQuote(
                    role=str(row.get("role", "")),
                    symbol=str(row.get("symbol", "")),
                    bid=float(row.get("bid", 0.0)),
                    ask=float(row.get("ask", 0.0)),
                    timestamp_ns=int(row.get("timestamp_ns", 0)),
                    strike=float(row["strike"]) if row.get("strike") is not None else None,
                    right=row.get("right"),
                )
            )
        except (ValueError, TypeError) as exc:
            print(f"Warning: skipping invalid quote row in {path}: {exc}", file=sys.stderr)
            continue
    return quotes
