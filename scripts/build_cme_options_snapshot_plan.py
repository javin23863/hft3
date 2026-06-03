"""Build CME options snapshot plans aligned to macro futures event snapshots.

This script is additive to the CME MBO replay lane. It does not infer option
contracts. Executable rows require a user-supplied JSON symbol map containing
real Databento options symbols for each futures symbol.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "packages"))

from data_system.src.events_parser import load_and_parse_events  # noqa: E402
from economic_event_universe.windows import snapshot_offsets  # noqa: E402


EXECUTABLE_POLICY = "configured_cme_options_symbol"
MISSING_MAPPING_POLICY = "missing_options_symbol_mapping"


def _as_utc(value: Any) -> datetime:
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(str(value))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _split_csv(values: Iterable[str] | str | None) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    out: list[str] = []
    for value in values:
        out.extend(part.strip() for part in str(value).split(",") if part.strip())
    return out


def _parse_offsets(raw: str | None) -> list[int] | None:
    vals = _split_csv(raw)
    if not vals:
        return None
    return sorted({int(v) for v in vals})


def _base_symbol(symbol: str) -> str:
    return symbol.split(".", 1)[0]


def _coerce_leg(entry: Any) -> dict[str, Any]:
    if isinstance(entry, str):
        return {"options_symbol": entry}
    if not isinstance(entry, dict):
        raise ValueError(f"option leg must be a string or object, got {type(entry).__name__}")
    leg = dict(entry)
    if "options_symbol" not in leg and "symbol" in leg:
        leg["options_symbol"] = leg["symbol"]
    return leg


def _coerce_leg_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [_coerce_leg(v) for v in value]
    return [_coerce_leg(value)]


def load_symbol_map(path: Path | None) -> dict[str, list[dict[str, Any]]]:
    if path is None:
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and "symbols" in raw:
        raw = raw["symbols"]
    if not isinstance(raw, dict):
        raise ValueError("symbol map must be a JSON object or contain a 'symbols' object")
    return {str(k): _coerce_leg_list(v) for k, v in raw.items()}


def _legs_for_future(symbol_map: dict[str, list[dict[str, Any]]], future_symbol: str) -> list[dict[str, Any]]:
    if future_symbol in symbol_map:
        return symbol_map[future_symbol]
    base = _base_symbol(future_symbol)
    if base in symbol_map:
        return symbol_map[base]
    return []


def _safe_label(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    return slug.strip("_") or "option"


def _row_for_leg(
    *,
    event: Any,
    future_symbol: str,
    leg: dict[str, Any],
    offset_sec: int,
    snapshot_utc: datetime,
    quote_window_seconds: int,
    default_dataset: str,
    default_schema: str,
    default_stype_in: str,
) -> dict[str, Any]:
    end_utc = snapshot_utc
    start_utc = end_utc - timedelta(seconds=quote_window_seconds)
    options_symbol = str(leg.get("options_symbol", "")).strip()
    label = str(leg.get("label") or leg.get("role") or options_symbol or "unmapped")
    row: dict[str, Any] = {
        "event_id": str(event["event_id"]),
        "event_type": str(event["event_type"]),
        "release_date": str(event["release_date"]),
        "future_symbol": future_symbol,
        "options_symbol": options_symbol,
        "options_symbol_label": _safe_label(label),
        "offset_sec": int(offset_sec),
        "anchor_utc": _iso_utc(_as_utc(event["anchor_utc"])),
        "snapshot_timestamp_utc": _iso_utc(snapshot_utc),
        "options_window_start_utc": _iso_utc(start_utc),
        "options_window_end_utc": _iso_utc(end_utc),
        "quote_window_seconds": int(quote_window_seconds),
        "dataset": str(leg.get("dataset", default_dataset)),
        "schema": str(leg.get("schema", default_schema)),
        "stype_in": str(leg.get("stype_in", default_stype_in)),
        "download_now": bool(options_symbol),
        "download_policy": EXECUTABLE_POLICY if options_symbol else MISSING_MAPPING_POLICY,
        "purpose": "CME options feature snapshot aligned to macro futures event snapshot",
        "leakage_guard": "options_window_end_utc <= snapshot_timestamp_utc; no option quotes after the snapshot timestamp",
    }
    for key in ("expiry", "strike", "right", "contract_multiplier", "underlying_raw_symbol"):
        if key in leg:
            row[key] = leg[key]
    return row


def build_plan(
    events_csv: Path,
    symbol_map: dict[str, list[dict[str, Any]]],
    *,
    event_ids: Sequence[str] | None = None,
    event_types: Sequence[str] | None = None,
    symbols: Sequence[str] | None = None,
    offsets_sec: Sequence[int] | None = None,
    quote_window_seconds: int = 60,
    default_dataset: str = "GLBX.MDP3",
    default_schema: str = "mbp-1",
    default_stype_in: str = "raw_symbol",
    include_missing_mappings: bool = True,
) -> list[dict[str, Any]]:
    if quote_window_seconds <= 0:
        raise ValueError("quote_window_seconds must be positive")
    wanted_events = set(event_ids or [])
    wanted_types = set(event_types or [])
    wanted_symbols = set(symbols or [])

    df = load_and_parse_events(str(events_csv))
    rows: list[dict[str, Any]] = []
    for _, event in df.iterrows():
        if wanted_events and str(event["event_id"]) not in wanted_events:
            continue
        if wanted_types and str(event["event_type"]) not in wanted_types:
            continue
        event_symbols = [str(s).strip() for s in event["parsed_symbols"] if str(s).strip()]
        if wanted_symbols:
            event_symbols = [s for s in event_symbols if s in wanted_symbols or _base_symbol(s) in wanted_symbols]
        if not event_symbols:
            continue
        event_offsets = list(offsets_sec) if offsets_sec is not None else list(snapshot_offsets(str(event["event_type"])))
        anchor_utc = _as_utc(event["anchor_utc"])
        for future_symbol in event_symbols:
            legs = _legs_for_future(symbol_map, future_symbol)
            if not legs and include_missing_mappings:
                legs = [{}]
            for leg in legs:
                for offset in event_offsets:
                    snapshot_utc = anchor_utc + timedelta(seconds=int(offset))
                    rows.append(
                        _row_for_leg(
                            event=event,
                            future_symbol=future_symbol,
                            leg=leg,
                            offset_sec=int(offset),
                            snapshot_utc=snapshot_utc,
                            quote_window_seconds=quote_window_seconds,
                            default_dataset=default_dataset,
                            default_schema=default_schema,
                            default_stype_in=default_stype_in,
                        )
                    )
    rows.sort(key=lambda r: (r["event_id"], r["future_symbol"], r["options_symbol_label"], r["offset_sec"]))
    return rows


def write_outputs(plan: list[dict[str, Any]], output_path: Path, *, symbol_map_path: Path | None) -> dict[str, Any]:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(plan, indent=2, sort_keys=True), encoding="utf-8")
    manifest = {
        "status": "completed",
        "plan_path": str(output_path),
        "symbol_map_path": str(symbol_map_path) if symbol_map_path else "",
        "n_rows": len(plan),
        "n_executable_rows": sum(1 for r in plan if r.get("download_now") is True),
        "n_missing_mapping_rows": sum(1 for r in plan if r.get("download_policy") == MISSING_MAPPING_POLICY),
        "event_ids": sorted({r["event_id"] for r in plan}),
        "future_symbols": sorted({r["future_symbol"] for r in plan}),
        "options_symbols": sorted({r["options_symbol"] for r in plan if r.get("options_symbol")}),
    }
    manifest_path = output_path.with_name(output_path.stem + "_manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scripts.build_cme_options_snapshot_plan")
    p.add_argument("--events-csv", default="packages/data_system/config/events.csv")
    p.add_argument("--symbol-map", default="", help="JSON map from futures symbols to real Databento options symbols")
    p.add_argument("--output", default="research_cards/cme/options_snapshots/cme_options_snapshot_plan.json")
    p.add_argument("--event-id", action="append", default=[])
    p.add_argument("--event-type", action="append", default=[])
    p.add_argument("--symbols", default="", help="Comma-separated futures symbols or bases, e.g. MES,ES.v.0")
    p.add_argument("--offsets-sec", default="", help="Comma-separated offsets; default uses event universe offsets")
    p.add_argument("--quote-window-seconds", type=int, default=60)
    p.add_argument("--dataset", default="GLBX.MDP3")
    p.add_argument("--schema", default="mbp-1")
    p.add_argument("--stype-in", default="raw_symbol")
    p.add_argument("--exclude-missing-mappings", action="store_true")
    args = p.parse_args(argv)

    symbol_map_path = Path(args.symbol_map) if args.symbol_map else None
    symbol_map = load_symbol_map(symbol_map_path)
    plan = build_plan(
        Path(args.events_csv),
        symbol_map,
        event_ids=_split_csv(args.event_id),
        event_types=_split_csv(args.event_type),
        symbols=_split_csv(args.symbols),
        offsets_sec=_parse_offsets(args.offsets_sec),
        quote_window_seconds=args.quote_window_seconds,
        default_dataset=args.dataset,
        default_schema=args.schema,
        default_stype_in=args.stype_in,
        include_missing_mappings=not args.exclude_missing_mappings,
    )
    manifest = write_outputs(plan, Path(args.output), symbol_map_path=symbol_map_path)
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
