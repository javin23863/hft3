"""Macro event CLI — full 44-type catalog truth + events.csv runnable subset."""

from __future__ import annotations

import json
import os
import sys
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


def catalog_help_text(repo_root: Optional[Path] = None) -> str:
    root = repo_root or _REPO_ROOT
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    for sub in ("packages", "apps"):
        p = str(root / sub)
        if p not in sys.path:
            sys.path.insert(0, p)

    from economic_event_universe.catalog_report import format_catalog_banner

    lines = [format_catalog_banner(), ""]
    ids = list_event_ids()
    lines.append(f"events.csv runnable rows: {len(ids)}")
    for etype in sorted(events_by_type()):
        sample = events_by_type()[etype][:2]
        lines.append(f"  {etype}: {len(events_by_type()[etype])} — e.g. {', '.join(sample)}")
    lines.append("")
    lines.append("Pass --event-id <id> on replay/workbench/gate scripts (from events.csv today).")
    lines.append("Optional env: HFT3_DEFAULT_EVENT_ID=<id> (automation only).")
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

    p = argparse.ArgumentParser(description="Macro event catalog (44 types; events.csv = runnable subset)")
    p.add_argument("--type", default=None, help="Filter events.csv by event_type")
    p.add_argument("--json", action="store_true", help="Emit catalog coverage JSON")
    args = p.parse_args()

    if args.json:
        from economic_event_universe.catalog_report import build_macro_catalog_summary

        print(json.dumps(build_macro_catalog_summary(_REPO_ROOT).to_dict(), indent=2))
        return 0

    if args.type:
        for eid in events_by_type().get(args.type, []):
            print(eid)
        return 0

    print(catalog_help_text())
    for eid in list_event_ids():
        print(eid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
