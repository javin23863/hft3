"""
Budget-locked Databento micro-probe: one symbol, one events.csv window.
Pre-flight get_cost(); abort if estimate > $5.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))

from dotenv import load_dotenv

load_dotenv(_REPO / ".env")

from data_system.src.databento_client import DatabentoResearchClient
from data_system.src.events_parser import load_and_parse_events

SOFT_PROBE_LIMIT_USD = 5.0
SYMBOLS = ["MES.v.0"]


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="Databento cost probe for one events.csv row")
    p.add_argument("--event-id", required=True, help="event_id from packages/data_system/config/events.csv")
    args = p.parse_args()
    event_id = args.event_id

    events = load_and_parse_events(str(_REPO / "packages" / "data_system" / "config" / "events.csv"))
    row = events[events["event_id"] == event_id]
    if row.empty:
        raise SystemExit(f"Event {event_id} not found in events.csv")

    r = row.iloc[0]
    start_utc = r["start_utc"]
    end_utc = r["end_utc"]

    client = DatabentoResearchClient()
    cost = client.client.metadata.get_cost(
        dataset="GLBX.MDP3",
        schema="mbo",
        symbols=SYMBOLS,
        stype_in="continuous",
        start=start_utc,
        end=end_utc,
    )
    print(f"Pre-flight cost estimate: ${cost:.4f} for {SYMBOLS} [{start_utc} -> {end_utc}]")

    if cost > SOFT_PROBE_LIMIT_USD:
        raise SystemExit(
            f"ABORT: cost ${cost:.2f} exceeds soft probe limit ${SOFT_PROBE_LIMIT_USD:.2f}. "
            "Narrow window or symbol before downloading."
        )

    client.download_event_window(
        event_id=event_id,
        symbols=SYMBOLS,
        start_utc=start_utc.to_pydatetime() if hasattr(start_utc, "to_pydatetime") else start_utc,
        end_utc=end_utc.to_pydatetime() if hasattr(end_utc, "to_pydatetime") else end_utc,
        stype_in="continuous",
    )
    print("Download complete. See data/manifest.parquet")


if __name__ == "__main__":
    main()
