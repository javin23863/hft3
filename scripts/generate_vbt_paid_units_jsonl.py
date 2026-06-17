#!/usr/bin/env python3
"""Generate JSONL work units for VectorBT paid-compute screening."""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

_DEFAULT_SYMBOL = "MES.v.0"
_DEFAULT_THESIS_TEMPLATE = (
    "Event-window microstructure strategy {model_id} on {event_type} release for {symbol}"
)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_") or "unit"


def _load_events(
    events_csv: Path,
    *,
    event_types: Optional[Set[str]],
    symbols: List[str],
    window_name: Optional[str],
    max_rows: Optional[int],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with events_csv.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            event_id = (row.get("event_id") or "").strip()
            event_type = (row.get("event_type") or "").strip()
            if not event_id or not event_type:
                continue
            if event_types and event_type not in event_types:
                continue
            if window_name and (row.get("window_name") or "").strip() != window_name:
                continue
            row_symbols = [s.strip() for s in (row.get("symbols") or "").split(",") if s.strip()]
            symbol = next((s for s in symbols if s in row_symbols), None)
            if symbol is None and row_symbols:
                symbol = row_symbols[0]
            if symbol is None:
                continue
            rows.append(
                {
                    "event_id": event_id,
                    "event_type": event_type,
                    "symbol": symbol,
                    "release_date": (row.get("release_date") or "").strip(),
                }
            )
            if max_rows is not None and len(rows) >= max_rows:
                break
    return rows


def _units_from_events(
    events: List[Dict[str, Any]],
    *,
    model_id: str,
    thesis_template: str,
) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    for ev in events:
        event_id = ev["event_id"]
        symbol = ev["symbol"]
        event_type = ev["event_type"]
        unit_id = _slug(f"{model_id}|{symbol}|{event_id}")
        thesis = thesis_template.format(
            model_id=model_id,
            event_type=event_type,
            symbol=symbol,
            event_id=event_id,
        )
        units.append(
            {
                "unit_id": unit_id,
                "event_id": event_id,
                "symbol": symbol,
                "event_type": event_type,
                "model_id": model_id,
                "thesis": thesis,
            }
        )
    return units


def _units_from_stage_a_survivors(
    survivors_path: Path,
    events_csv: Path,
    *,
    symbols: List[str],
    thesis_template: str,
    max_units: Optional[int],
) -> List[Dict[str, Any]]:
    payload = json.loads(survivors_path.read_text(encoding="utf-8"))
    survivors = payload.get("survivors") or []
    if not isinstance(survivors, list):
        raise ValueError("stage_a_survivors.json: survivors must be a list")

    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in _load_events(events_csv, event_types=None, symbols=symbols, window_name="TIGHT", max_rows=None):
        by_type.setdefault(row["event_type"], []).append(row)

    units: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for sv in survivors:
        hyp_id = str(sv.get("hyp_id") or sv.get("model_id") or "").strip()
        event_type = str(sv.get("event_type") or "").strip()
        if not hyp_id or not event_type:
            continue
        for ev in by_type.get(event_type, [])[:50]:
            event_id = ev["event_id"]
            symbol = ev["symbol"]
            unit_id = _slug(f"{hyp_id}|{symbol}|{event_id}")
            if unit_id in seen:
                continue
            seen.add(unit_id)
            units.append(
                {
                    "unit_id": unit_id,
                    "event_id": event_id,
                    "symbol": symbol,
                    "event_type": event_type,
                    "model_id": hyp_id,
                    "thesis": thesis_template.format(
                        model_id=hyp_id,
                        event_type=event_type,
                        symbol=symbol,
                        event_id=event_id,
                    ),
                }
            )
            if max_units is not None and len(units) >= max_units:
                return units
    return units


def write_units_jsonl(path: Path, units: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for unit in units:
            handle.write(json.dumps(unit, sort_keys=True) + "\n")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Generate VectorBT paid-screen unit JSONL")
    parser.add_argument("--out", type=Path, required=True, help="Output JSONL path")
    parser.add_argument(
        "--events-csv",
        type=Path,
        default=_REPO / "packages" / "data_system" / "config" / "events.csv",
    )
    parser.add_argument("--model-id", default="HYP_5")
    parser.add_argument("--symbols", default=_DEFAULT_SYMBOL)
    parser.add_argument("--event-types", default=None, help="Comma-separated event_type filter")
    parser.add_argument("--window-name", default="TIGHT")
    parser.add_argument("--smoke-count", type=int, default=None, help="Cap events for smoke JSONL")
    parser.add_argument(
        "--from-stage-a-survivors",
        type=Path,
        default=None,
        help="Expand stage_a_survivors.json into units",
    )
    parser.add_argument("--max-units", type=int, default=None)
    parser.add_argument("--thesis-template", default=_DEFAULT_THESIS_TEMPLATE)
    args = parser.parse_args(argv)

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    event_types: Optional[Set[str]] = None
    if args.event_types:
        event_types = {t.strip() for t in args.event_types.split(",") if t.strip()}

    if args.from_stage_a_survivors:
        units = _units_from_stage_a_survivors(
            args.from_stage_a_survivors,
            args.events_csv,
            symbols=symbols,
            thesis_template=args.thesis_template,
            max_units=args.max_units,
        )
    else:
        max_rows = args.smoke_count or args.max_units
        events = _load_events(
            args.events_csv,
            event_types=event_types,
            symbols=symbols,
            window_name=args.window_name,
            max_rows=max_rows,
        )
        units = _units_from_events(
            events,
            model_id=args.model_id,
            thesis_template=args.thesis_template,
        )
        if args.max_units is not None:
            units = units[: args.max_units]

    if not units:
        print("ERROR: zero units generated", file=sys.stderr)
        return 1

    out = args.out if args.out.is_absolute() else _REPO / args.out
    write_units_jsonl(out, units)
    print(f"Wrote {len(units)} units to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
