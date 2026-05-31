"""Walk-forward validation with event-time filtration."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from equities_lane.src.backtest.low_float_backtester import LowFloatBacktester
from equities_lane.src.ingest.float_metadata import load_float_csv, lookup_float
from equities_lane.src.types import UniverseConfig


@dataclass
class WalkForwardFold:
    fold_id: int
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str


def generate_folds(
    start_date: str,
    end_date: str,
    config: UniverseConfig,
) -> list[WalkForwardFold]:
    wf = config.walk_forward
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    folds: list[WalkForwardFold] = []
    fold_id = 0
    cursor = start
    while True:
        train_start = cursor
        train_end = train_start + timedelta(days=wf.train_days - 1)
        val_start = train_end + timedelta(days=1)
        val_end = val_start + timedelta(days=wf.val_days - 1)
        test_start = val_end + timedelta(days=1)
        test_end = test_start + timedelta(days=wf.test_days - 1)
        if test_end > end:
            break
        folds.append(
            WalkForwardFold(
                fold_id=fold_id,
                train_start=train_start.isoformat(),
                train_end=train_end.isoformat(),
                val_start=val_start.isoformat(),
                val_end=val_end.isoformat(),
                test_start=test_start.isoformat(),
                test_end=test_end.isoformat(),
            )
        )
        fold_id += 1
        cursor = cursor + timedelta(days=wf.step_days)
    return folds


def assert_no_float_lookahead(
    float_csv: str,
    symbol: str,
    session_date: str,
) -> dict[str, Any]:
    """Verify float lookup uses as_of_date <= session_date only."""
    records = load_float_csv(float_csv)
    rec = lookup_float(records, symbol, session_date)
    future = [
        r
        for r in records
        if r.symbol == symbol and r.as_of_date > session_date
    ]
    return {
        "session_date": session_date,
        "float_as_of": rec.as_of_date if rec else None,
        "future_records_excluded": len(future),
        "lookahead_ok": rec is None or rec.as_of_date <= session_date,
    }


def run_walk_forward_session(
    session_path: str,
    universe: UniverseConfig,
) -> dict[str, Any]:
    bt = LowFloatBacktester(universe)
    result = bt.run(session_path)
    return result.to_dict()
