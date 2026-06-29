#!/usr/bin/env python
"""Prepare HftBacktest-only units from the NPZ lake manifest.

This script is deliberately manifest-driven: it does not choose a symbol or
model. Each lake row either becomes a prepared HBT unit or a blocker manifest
that remains visible to the canonical campaign manifest.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

from backtest_pipeline.src.hftbacktest_only_campaign_manifest import DEFAULT_AUTHORITY_REFS
from backtest_pipeline.src.hftbacktest_only_io import safe_stem, write_json_atomic
from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyPipelineError,
    HftBacktestOnlyPrepareConfig,
    prepare_hftbacktest_only_l3_from_lake,
)


DEFAULT_PRODUCT_METADATA = REPO / "config" / "hftbacktest" / "cme_lake_product_metadata.yaml"
DEFAULT_INSTRUMENT_REGISTRY = DEFAULT_PRODUCT_METADATA
SCHEMA_VERSION = "hft3_hbt_only_lake_manifest_prepare_summary_v1"
EXPLICIT_FIELDS_KEY = "_explicit_fields"
METADATA_POLICY = "explicit_per_symbol_contract_tick_lot_contract_required"


def prepare_from_lake_manifest(
    *,
    lake_manifest: Path,
    out_root: Path,
    instrument_registry: Path = DEFAULT_INSTRUMENT_REGISTRY,
    warmup_seconds: int = 30,
    replay_mode: str = "full_l3_event_replay",
    force_rebuild: bool = False,
) -> dict[str, Any]:
    rows = _load_lake_manifest(lake_manifest)
    metadata = _load_instrument_metadata(instrument_registry)
    prepared = 0
    reused = 0
    blocked = 0
    failures: dict[str, int] = {}

    for index, row in enumerate(rows):
        result = _prepare_one(
            row=row,
            row_index=index,
            out_root=out_root,
            metadata_by_symbol=metadata,
            metadata_source=instrument_registry,
            warmup_seconds=warmup_seconds,
            replay_mode=replay_mode,
            force_rebuild=force_rebuild,
        )
        status = str(result.get("status") or "")
        if result.get("reused_existing"):
            reused += 1
        if status == "hbt_full_l3_event_replay_prepared" or status == "research_smoke_derived_subset":
            prepared += 1
        else:
            blocked += 1
            blocker = str(result.get("blocker_code") or "unknown_blocker")
            failures[blocker] = failures.get(blocker, 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "lake_manifest": str(lake_manifest),
        "product_metadata": str(instrument_registry),
        "out_root": str(out_root),
        "row_count": len(rows),
        "prepared_count": prepared,
        "reused_count": reused,
        "blocked_count": blocked,
        "blocker_counts": dict(sorted(failures.items())),
        "replay_mode": replay_mode,
        "warmup_seconds": int(warmup_seconds),
        "authority_refs": list(DEFAULT_AUTHORITY_REFS),
        "metadata_policy": METADATA_POLICY,
    }


def _prepare_one(
    *,
    row: Mapping[str, Any],
    row_index: int,
    out_root: Path,
    metadata_by_symbol: Mapping[str, Mapping[str, Any]],
    metadata_source: Path,
    warmup_seconds: int,
    replay_mode: str,
    force_rebuild: bool,
) -> dict[str, Any]:
    symbol = str(row.get("symbol") or row.get("resolved_symbol") or "").strip()
    event_id = str(row.get("event_id") or "").strip()
    source_npz = Path(str(row.get("npz_path") or ""))
    trade_date = _trade_date_from_event_id(event_id)
    row_contract = str(row.get("contract") or row.get("resolved_contract") or "").strip()
    base_manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "plan": "docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md",
        "source": "npz_lake_manifest",
        "lake_manifest_row_index": row_index,
        "source_npz": str(source_npz),
        "source_npz_sha256": str(row.get("sha256") or ""),
        "source_rows": int(row.get("event_count") or 0),
        "symbol": symbol,
        "contract": row_contract,
        "event_id": event_id,
        "trade_date": trade_date,
        "normalized_npz": "",
        "initial_snapshot": "",
        "feed_type": "L3_MBO",
        "timezone": "UTC",
        "timestamp_units": "nanoseconds",
        "replay_mode": replay_mode,
        "warmup_seconds": int(warmup_seconds),
        "authority_refs": _authority_refs(None, metadata_source),
        "product_metadata_source": str(metadata_source),
        "metadata_policy": METADATA_POLICY,
    }
    manifest_path = _manifest_path(out_root, symbol or "unknown_symbol", trade_date, event_id or f"row_{row_index}")

    if not symbol or not event_id:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="authority_missing",
            blocker_detail="missing symbol or event_id",
        )
    if not source_npz.is_file():
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="data_blocker:source_npz_missing",
            blocker_detail=f"source_npz={source_npz}",
        )
    instrument = metadata_by_symbol.get(symbol)
    if not instrument:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="authority_missing",
            blocker_detail=f"instrument_metadata_missing:{symbol}",
        )
    contract = row_contract or str(instrument.get("contract") or "").strip()
    base_manifest = _with_instrument_metadata(
        base_manifest,
        instrument,
        metadata_source=metadata_source,
        contract=contract,
    )
    if instrument.get("tradable") is False or instrument.get("order_book_available") is False:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code=str(instrument.get("blocker_code") or "authority_missing"),
            blocker_detail=str(instrument.get("blocker_detail") or f"instrument_not_hbt_executable:{symbol}"),
        )

    if not contract:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="authority_missing",
            blocker_detail="instrument_metadata_missing:contract",
        )
    tick_size = _numeric_metadata(
        instrument,
        "tick_size",
        "min_price_increment",
        require_explicit=True,
    )
    lot_size = _numeric_metadata(instrument, "lot_size", require_explicit=True)
    contract_size = _numeric_metadata(
        instrument,
        "contract_size",
        "contract_multiplier",
        require_explicit=True,
    )
    missing = [
        name
        for name, value in (
            ("tick_size", tick_size),
            ("lot_size", lot_size),
            ("contract_size", contract_size),
        )
        if value is None or value <= 0
    ]
    if missing:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="authority_missing",
            blocker_detail="instrument_metadata_missing:" + ",".join(missing),
        )

    try:
        prepared = prepare_hftbacktest_only_l3_from_lake(
            HftBacktestOnlyPrepareConfig(
                source_npz=source_npz,
                symbol=symbol,
                contract=contract,
                event_id=event_id,
                trade_date=trade_date,
                out_root=out_root,
                warmup_seconds=warmup_seconds,
                replay_mode=replay_mode,
                tick_size=float(tick_size),
                lot_size=float(lot_size),
                contract_size=float(contract_size),
                force_rebuild=force_rebuild,
            )
        )
        return _stamp_prepared_manifest(prepared, base_manifest)
    except HftBacktestOnlyPipelineError as exc:
        return _write_blocker_manifest(
            manifest_path,
            base_manifest,
            blocker_code="data_blocker:prepare_failed",
            blocker_detail=str(exc),
        )


def _load_lake_manifest(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("--lake-manifest must be a JSON list")
    return [dict(row) for row in payload if isinstance(row, Mapping)]


def _load_instrument_metadata(path: Path) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = dict(payload.get("defaults") or {})
    out: dict[str, dict[str, Any]] = {}
    symbols = payload.get("symbols")
    if isinstance(symbols, Mapping):
        for symbol, raw in symbols.items():
            if not isinstance(raw, Mapping):
                continue
            merged = {**defaults, **dict(raw)}
            merged["symbol"] = str(symbol)
            merged[EXPLICIT_FIELDS_KEY] = tuple(str(key) for key in raw.keys())
            value = str(symbol).strip()
            if value:
                out[value] = merged
        return out

    for raw in payload.get("instruments") or []:
        if not isinstance(raw, Mapping):
            continue
        merged = {**defaults, **dict(raw)}
        merged[EXPLICIT_FIELDS_KEY] = tuple(str(key) for key in raw.keys())
        for key in ("symbol", "research_symbol"):
            value = str(merged.get(key) or "").strip()
            if value:
                out[value] = merged
    return out


def _numeric_metadata(
    metadata: Mapping[str, Any],
    *keys: str,
    default: float | None = None,
    require_explicit: bool = False,
) -> float | None:
    explicit_fields = {str(key) for key in metadata.get(EXPLICIT_FIELDS_KEY, ())}
    for key in keys:
        if require_explicit and key not in explicit_fields:
            continue
        value = metadata.get(key)
        if value is None:
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    return default


def _with_instrument_metadata(
    manifest: Mapping[str, Any],
    instrument: Mapping[str, Any],
    *,
    metadata_source: Path,
    contract: str,
) -> dict[str, Any]:
    payload = dict(manifest)
    payload.update(
        {
            "contract": contract,
            "canonical_internal_symbol": str(instrument.get("canonical_internal_symbol") or ""),
            "exchange": str(instrument.get("exchange") or ""),
            "venue": str(instrument.get("venue") or ""),
            "asset_class": str(instrument.get("asset_class") or ""),
            "contract_role": str(instrument.get("contract_role") or ""),
            "tick_value": instrument.get("tick_value"),
            "authority_refs": _authority_refs(instrument, metadata_source),
            "product_metadata_source": str(metadata_source),
            "product_metadata_symbol": str(instrument.get("symbol") or ""),
            "metadata_policy": METADATA_POLICY,
        }
    )
    return payload


def _authority_refs(instrument: Mapping[str, Any] | None, metadata_source: Path) -> list[str]:
    refs = [*DEFAULT_AUTHORITY_REFS, str(metadata_source)]
    if instrument is not None:
        refs.extend(str(ref) for ref in instrument.get("authority_refs") or [])
    return list(dict.fromkeys(ref for ref in refs if ref))


def _stamp_prepared_manifest(prepared: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    manifest_path = Path(str(prepared.get("manifest_path") or ""))
    additions = {
        key: value
        for key, value in metadata.items()
        if key
        in {
            "authority_refs",
            "product_metadata_source",
            "product_metadata_symbol",
            "metadata_policy",
            "canonical_internal_symbol",
            "exchange",
            "venue",
            "asset_class",
            "contract_role",
            "tick_value",
        }
    }
    payload = {**dict(prepared), **additions}
    if manifest_path.is_file():
        persisted = json.loads(manifest_path.read_text(encoding="utf-8"))
        persisted.update(additions)
        write_json_atomic(manifest_path, persisted)
    return payload


def _write_blocker_manifest(
    path: Path,
    manifest: Mapping[str, Any],
    *,
    blocker_code: str,
    blocker_detail: str,
) -> dict[str, Any]:
    payload = {
        **dict(manifest),
        "status": "blocked",
        "validation_status": "blocked",
        "admissibility_status": blocker_code.split(":", 1)[0],
        "blocker_code": blocker_code,
        "blocker_detail": blocker_detail,
    }
    write_json_atomic(path, payload)
    return {**payload, "manifest_path": str(path), "reused_existing": False}


def _manifest_path(out_root: Path, symbol: str, trade_date: str, event_id: str) -> Path:
    stem = safe_stem(event_id)
    return Path(out_root) / "prepared" / safe_stem(symbol) / trade_date / f"{stem}_manifest.json"


def _trade_date_from_event_id(event_id: str) -> str:
    match = re.search(r"(20\d{2})_(\d{2})_(\d{2})", event_id)
    if not match:
        return "unknown-date"
    return "-".join(match.groups())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare HBT-only units from a full NPZ lake manifest")
    parser.add_argument("--lake-manifest", type=Path, required=True)
    parser.add_argument("--out-root", type=Path, default=REPO / "data" / "hbt")
    parser.add_argument("--instrument-registry", type=Path, default=DEFAULT_INSTRUMENT_REGISTRY)
    parser.add_argument("--warmup-seconds", type=int, default=30)
    parser.add_argument(
        "--replay-mode",
        choices=("full_l3_event_replay", "added_orders_only"),
        default="full_l3_event_replay",
    )
    parser.add_argument("--force-rebuild", action="store_true")
    parser.add_argument("--summary-out", type=Path, default=None)
    args = parser.parse_args(argv)

    summary = prepare_from_lake_manifest(
        lake_manifest=args.lake_manifest,
        out_root=args.out_root,
        instrument_registry=args.instrument_registry,
        warmup_seconds=args.warmup_seconds,
        replay_mode=args.replay_mode,
        force_rebuild=args.force_rebuild,
    )
    if args.summary_out is not None:
        write_json_atomic(args.summary_out, summary)
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if summary["row_count"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
