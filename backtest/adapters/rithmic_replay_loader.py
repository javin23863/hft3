"""Resolve replay NPZ paths for Rithmic trial vs Databento event windows."""
from __future__ import annotations

from pathlib import Path

from data_system.src.events_parser import load_and_parse_events


def resolve_event_npz(
    event_id: str,
    repo_root: Path,
    *,
    events_csv: Path | None = None,
    symbol: str | None = None,
) -> Path:
    """Return Databento MBO NPZ path for a macro event_id from events.csv."""
    csv_path = events_csv or (repo_root / "data_system" / "config" / "events.csv")
    events = load_and_parse_events(str(csv_path))
    row = events[events["event_id"] == event_id]
    if row.empty:
        raise ValueError(f"event_id not in events.csv: {event_id}")

    sym = symbol or row.iloc[0]["parsed_symbols"][0]
    npz = repo_root / "data" / "npz" / f"{sym}_{event_id}_mbo.npz"
    if not npz.is_file():
        raise FileNotFoundError(f"NPZ missing for {event_id}: {npz}")
    return npz


def resolve_trial_npz(repo_root: Path, capture_date: str, symbol: str) -> Path:
    """Return quarantined Rithmic trial replay NPZ for a capture session date."""
    npz = (
        repo_root
        / "data"
        / "replay"
        / "hftbacktest"
        / "rithmic_trial"
        / capture_date
        / symbol
        / f"{symbol}_{capture_date}_trial.npz"
    )
    if not npz.is_file():
        raise FileNotFoundError(f"Trial NPZ missing: {npz}")
    return npz
