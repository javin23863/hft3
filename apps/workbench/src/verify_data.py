"""Fail-closed MBO NPZ preflight for workbench runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from data_system.src.data_roots import verify_data_for_event
from data_system.src.events_parser import load_and_parse_events


def _events_csv(repo: Path) -> Path:
    for candidate in (
        repo / "packages" / "data_system" / "config" / "events.csv",
        repo / "data_system" / "config" / "events.csv",
    ):
        if candidate.is_file():
            return candidate
    return repo / "packages" / "data_system" / "config" / "events.csv"


def verify_data(
    repo: Path,
    *,
    event_id: str,
    symbol: str,
) -> dict[str, Any]:
    df = load_and_parse_events(str(_events_csv(repo)))
    rows = df[df["event_id"] == event_id]
    if rows.empty:
        return {
            "ok": False,
            "event_id": event_id,
            "symbol": symbol,
            "error": f"event_id not in catalog: {event_id}",
        }
    row = rows.iloc[0]
    parsed = tuple(str(s) for s in row["parsed_symbols"])
    check = verify_data_for_event(repo, event_id, symbol, parsed)
    check["event_type"] = str(row.get("event_type", ""))
    check["release_date"] = str(row.get("release_date", ""))
    if not check["ok"]:
        check["error"] = (
            f"MBO NPZ missing for {symbol} / {event_id}. "
            f"Expected under one of: {check.get('search_dirs')}. "
            f"Run: {check.get('sync_command')}"
        )
    return check


def verify_data_optional(
    repo: Path,
    event_id: Optional[str],
    symbol: Optional[str],
) -> dict[str, Any]:
    if not event_id or not symbol:
        return {"ok": False, "error": "event_id and symbol are required"}
    return verify_data(repo, event_id=event_id, symbol=symbol)
