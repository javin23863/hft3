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
# CME M6 full symbol universe (CME_M6_SWEEP_CONTROL_PLAN.md)
CME_M6_SYMBOLS = "MES.v.0,MNQ.v.0,ES.v.0,NQ.v.0,ZN.v.0,ZB.v.0,RTY.v.0"
_DEFAULT_THESIS_TEMPLATE = (
    "Event-window microstructure strategy {model_id} on {event_type} release for {symbol}"
)


def _hypothesis_model_id(hyp_id: int) -> str:
    return f"HYP_{hyp_id}"


def _parse_stage_a_allowed_cells(
    payload: Dict[str, Any],
) -> Set[tuple[int, str]]:
    """Mirror run_event_universe stage-A allowed (hyp_id, event_type) cells."""
    survivors = payload.get("survivors") or []
    pass_through = payload.get("pass_through") or []
    tested_cells = payload.get("tested_cells") or []
    tested_etypes: Set[str] = {
        str(tc["event_type"]).strip()
        for tc in tested_cells
        if isinstance(tc, dict) and tc.get("event_type")
    }
    allowed: Set[tuple[int, str]] = set()

    for row in survivors:
        if not isinstance(row, dict):
            continue
        if "hyp_id" in row and "event_type" in row:
            allowed.add((int(row["hyp_id"]), str(row["event_type"]).strip()))

    for pt in pass_through:
        pt_id: Optional[int] = None
        if isinstance(pt, int):
            pt_id = pt
        elif isinstance(pt, str) and pt.strip().isdigit():
            pt_id = int(pt.strip())
        elif isinstance(pt, dict) and pt.get("hyp_id") is not None:
            pt_id = int(pt["hyp_id"])
        if pt_id is None:
            continue
        for etype in tested_etypes:
            allowed.add((pt_id, etype))

    return allowed


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
    window_name: str,
) -> List[Dict[str, Any]]:
    payload = json.loads(survivors_path.read_text(encoding="utf-8"))
    allowed_cells = _parse_stage_a_allowed_cells(payload)
    if not allowed_cells:
        raise ValueError("stage_a_survivors.json: no allowed (hyp_id, event_type) cells")

    allowed_etypes = {etype for _, etype in allowed_cells}
    by_type: Dict[str, List[Dict[str, Any]]] = {}
    for row in _load_events(
        events_csv,
        event_types=allowed_etypes,
        symbols=symbols,
        window_name=window_name,
        max_rows=None,
    ):
        by_type.setdefault(row["event_type"], []).append(row)

    units: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for hyp_id, event_type in sorted(allowed_cells):
        model_id = _hypothesis_model_id(hyp_id)
        for ev in by_type.get(event_type, []):
            event_id = ev["event_id"]
            symbol = ev["symbol"]
            unit_id = _slug(f"{model_id}|{symbol}|{event_id}")
            if unit_id in seen:
                continue
            seen.add(unit_id)
            units.append(
                {
                    "unit_id": unit_id,
                    "event_id": event_id,
                    "symbol": symbol,
                    "event_type": event_type,
                    "model_id": model_id,
                    "hyp_id": hyp_id,
                    "thesis": thesis_template.format(
                        model_id=model_id,
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
    parser.add_argument("--symbols", default=CME_M6_SYMBOLS, help="Comma-separated symbols (default: CME M6 universe)")
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
            window_name=args.window_name,
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
