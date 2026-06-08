"""FloatMetadataAtSession — point-in-time float metadata lookup result."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Any


@dataclass(frozen=True)
class FloatMetadataAtSession:
    underlying_symbol: str
    session_date: str
    as_of_date: str
    float_shares: int
    source: str
    raw_path: str

    def validate(self) -> None:
        if self.float_shares < 0:
            raise ValueError("float_shares must be >= 0")
        if not self.as_of_date:
            raise ValueError("as_of_date required")
        try:
            as_of = date.fromisoformat(self.as_of_date)
            sess = date.fromisoformat(self.session_date)
        except ValueError as e:
            raise ValueError(f"date parse failure: {e}")
        if as_of > sess:
            raise ValueError(
                f"float as_of_date {self.as_of_date} is after session_date {self.session_date} "
                f"(point-in-time violation)"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_float_pit_csv(path: str, underlying: str, session_date: str) -> FloatMetadataAtSession | None:
    """Look up a single float row for (underlying, session_date).

    Returns the most recent as_of_date <= session_date. Raises ValueError
    if a row with as_of_date > session_date is found (leakage).
    """
    import csv
    from pathlib import Path as _P

    p = _P(path)
    if not p.exists():
        return None
    best: FloatMetadataAtSession | None = None
    with p.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            sym = row.get("symbol") or row.get("ticker") or row.get("underlying") or ""
            if sym.strip().upper() != underlying.strip().upper():
                continue
            as_of = row.get("as_of_date") or row.get("date") or row.get("as_of") or ""
            try:
                float_shares = int(row.get("float_shares") or row.get("float") or 0)
            except ValueError:
                float_shares = 0
            entry = FloatMetadataAtSession(
                underlying_symbol=underlying,
                session_date=session_date,
                as_of_date=as_of,
                float_shares=float_shares,
                source=row.get("source", "float_pit_csv"),
                raw_path=str(p),
            )
            try:
                entry.validate()
            except ValueError:
                raise
            if best is None:
                best = entry
            elif entry.as_of_date > best.as_of_date:
                best = entry
    return best
