"""Macro event CLI helpers — catalog from events.csv, no hardcoded CPI default."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[3]
EVENTS_CSV = _REPO_ROOT / "packages" / "data_system" / "config" / "events.csv"


def repo_events_csv(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return repo_root / "packages" / "data_system" / "config" / "events.csv"
    return EVENTS_CSV


def load_events_df(csv_path: Optional[Path] = None):
    from data_system.src.events_parser import load_and_parse_events

    return load_and_parse_events(str(csv_path or repo_events_csv()))


def list_event_ids(csv_path: Optional[Path] = None) -> list[str]:
    return [str(x) for x in load_events_df(csv_path)["event_id"].tolist()]


def events_by_type(csv_path: Optional[Path] = None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for _, row in load_events_df(csv_path).iterrows():
        out.setdefault(str(row["event_type"]), []).append(str(row["event_id"]))
    return out


def catalog_help_text() -> str:
    ids = list_event_ids()
    lines = [
        f"Macro catalog: {len(ids)} events in packages/data_system/config/events.csv",
    ]
    for etype in sorted(events_by_type()):
        sample = events_by_type()[etype][:2]
        lines.append(f"  {etype}: {len(events_by_type()[etype])} — e.g. {', '.join(sample)}")
    lines.append("Pass --event-id <id> on all replay/workbench/gate scripts.")
    lines.append("Optional env: HFT3_DEFAULT_EVENT_ID=<id> (automation only; not a repo default).")
    return "\n".join(lines)


def resolve_event_id(event_id: Optional[str]) -> str:
    if event_id and str(event_id).strip():
        return str(event_id).strip()
    env = os.getenv("HFT3_DEFAULT_EVENT_ID", "").strip()
    if env:
        return env
    raise SystemExit(f"Missing required --event-id.\n\n{catalog_help_text()}")


def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="List macro events from events.csv")
    p.add_argument("--type", default=None, help="Filter by event_type (CPI, NFP, PROP_FLATTEN_TOPSTEP, ...)")
    args = p.parse_args()
    if args.type:
        for eid in events_by_type().get(args.type, []):
            print(eid)
    else:
        print(catalog_help_text())
        for eid in list_event_ids():
            print(eid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
