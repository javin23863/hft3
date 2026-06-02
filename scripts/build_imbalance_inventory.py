#!/usr/bin/env python3
"""Aggregate futures/options/equities datasets for imbalance support inventory."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
for sub in ("packages", "apps"):
    p = ROOT / sub
    if p.is_dir() and str(p) not in sys.path:
        sys.path.insert(0, str(p))

from hft3_bootstrap import workbench_root


def _dataset_id(
    asset_class: str,
    vendor: str,
    dataset: str,
    schema: str,
    symbol_universe: str,
) -> str:
    return f"{asset_class}:{vendor}:{dataset}:{schema}:{symbol_universe}"


def _support_from_schema(
    schema: str,
    *,
    has_mbo_npz: bool = False,
    auction_feed: bool = False,
) -> dict[str, str]:
    s = schema.lower().replace("_", "-")
    if s == "mbo" or has_mbo_npz:
        book = "full"
        ofi = "full"
    elif s in ("mbp-10", "mbp10"):
        book = "full"
        ofi = "proxy"
    elif s in ("mbp-1", "mbp1", "cbbo-1m"):
        book = "proxy"
        ofi = "trade-only" if s == "cbbo-1m" else "proxy"
    elif s == "trades":
        book = "unavailable"
        ofi = "trade-only"
    elif s == "ohlcv-1d":
        book = "unavailable"
        ofi = "unavailable"
    else:
        book = "unavailable"
        ofi = "unavailable"
    auction = "full" if auction_feed else "unavailable"
    return {
        "book_imbalance_support": book,
        "order_flow_imbalance_support": ofi,
        "auction_imbalance_support": auction,
    }


def _npz_index(repo: Path) -> dict[str, list[str]]:
    npz_dir = repo / "data" / "npz"
    index: dict[str, list[str]] = {}
    if not npz_dir.is_dir():
        return index
    for p in npz_dir.glob("*_mbo.npz"):
        sym = p.name.split("_")[0]
        index.setdefault(sym, []).append(str(p.relative_to(repo)))
    return index


def _futures_rows(repo: Path) -> list[dict[str, Any]]:
    hot_path = workbench_root(repo) / "config" / "hot_memory_universe.yaml"
    raw = yaml.safe_load(hot_path.read_text(encoding="utf-8")) if hot_path.is_file() else {}
    npz_index = _npz_index(repo)
    rows: list[dict[str, Any]] = []
    for row in raw.get("instruments") or []:
        if not row.get("tradable", True) and not row.get("order_book_available", True):
            if row.get("instrument_type") == "index_sensor":
                mbo_status = "SENSOR_ONLY"
            else:
                mbo_status = "DISABLED"
        elif not row.get("order_book_available", True):
            mbo_status = "MBO_DEGRADED"
        else:
            rs = str(row.get("research_symbol", ""))
            mbo_status = "MBO_HISTORICAL" if rs in npz_index and npz_index[rs] else "MBO_MISSING"
        schema = "mbo" if mbo_status not in ("SENSOR_ONLY", "DISABLED") else "none"
        has_npz = bool(npz_index.get(str(row.get("research_symbol", "")), []))
        npz_paths = npz_index.get(str(row.get("research_symbol", "")), [])
        inst = {
            "canonical_symbol": row.get("canonical_internal_symbol"),
            "research_symbol": row.get("research_symbol"),
            "databento_dataset": (
                row.get("venue", "GLBX") + ".MDP3"
                if row.get("venue") == "GLBX"
                else row.get("venue")
            ),
            "expected_schema": schema,
            "mbo_status": mbo_status,
            "npz_paths": npz_paths,
        }
        sup = _support_from_schema(schema, has_mbo_npz=has_npz and mbo_status == "MBO_HISTORICAL")
        if mbo_status in ("MBO_MISSING", "MBO_DEGRADED", "SENSOR_ONLY"):
            action = "quarantine" if mbo_status != "SENSOR_ONLY" else "ignore"
        elif has_npz:
            action = "use"
        else:
            action = "enrich"
        sym = inst.get("research_symbol") or inst.get("canonical_symbol", "")
        rows.append(
            {
                "dataset_id": _dataset_id(
                    "futures",
                    "databento",
                    str(inst.get("databento_dataset", "GLBX.MDP3")),
                    schema,
                    sym,
                ),
                "asset_class": "futures",
                "source_vendor": "databento",
                "venue": inst.get("databento_dataset", "GLBX.MDP3"),
                "symbol_universe": sym,
                "instrument_universe": inst.get("canonical_symbol"),
                "date_coverage": "events.csv macro windows; NPZ per event",
                "available_schemas": ["mbo", "mbp-10", "trades", "definition"],
                "mbo_available": mbo_status == "MBO_HISTORICAL",
                "mbp_10_available": False,
                "trades_available": has_npz,
                "auction_imbalance_available": False,
                "definition_available": True,
                "timestamp_precision": "nanoseconds",
                "event_sequencing_quality": "full" if schema == "mbo" and has_npz else "none",
                "session_calendar_coverage": "CME Globex via events.csv",
                "instrument_metadata_coverage": "hot_memory_universe.yaml",
                **sup,
                "known_gaps": (
                    []
                    if has_npz
                    else [f"mbo_status={mbo_status}", "no_npz_on_disk"]
                ),
                "recommended_action": action,
                "config_paths": [
                    "apps/workbench/config/hot_memory_universe.yaml",
                    "packages/data_system/config/events.csv",
                ],
            }
        )
    return rows


def _equities_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    uni_path = repo / "packages" / "equities_lane" / "config" / "universe.yaml"
    decadal_path = repo / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
    uni = yaml.safe_load(uni_path.read_text(encoding="utf-8")) if uni_path.is_file() else {}
    decadal = yaml.safe_load(decadal_path.read_text(encoding="utf-8")) if decadal_path.is_file() else {}
    db_cfg = uni.get("databento") or {}
    primary = str(db_cfg.get("schema_primary", "mbo"))
    degraded = str(db_cfg.get("schema_degraded", "mbp-1"))
    dataset = str(db_cfg.get("dataset", "XNAS.ITCH"))
    sup_p = _support_from_schema(primary)
    rows.append(
        {
            "dataset_id": _dataset_id("equities", "databento", dataset, primary, "low_float_universe"),
            "asset_class": "equities",
            "source_vendor": "databento",
            "venue": dataset,
            "symbol_universe": "filtered low-float universe",
            "instrument_universe": "packages/equities_lane/config/universe.yaml",
            "date_coverage": "session-level decadal catalog",
            "available_schemas": [primary, degraded, "mbp-10", "ohlcv-1d", "cbbo-1m", "imbalance"],
            "mbo_available": True,
            "mbp_10_available": False,
            "trades_available": True,
            "auction_imbalance_available": False,
            "definition_available": True,
            "timestamp_precision": "nanoseconds",
            "event_sequencing_quality": "full",
            "session_calendar_coverage": "US RTH + premarket",
            "instrument_metadata_coverage": "float_pit.csv",
            **sup_p,
            "schema_fallback": "mbp-10",
            "known_gaps": ["auction imbalance ingest not yet populated"],
            "recommended_action": "use",
            "config_paths": [str(uni_path.relative_to(repo))],
        }
    )
    for sess in decadal.get("sessions") or []:
        if sess.get("skip_pull"):
            action = "quarantine"
            gaps = [sess.get("skip_reason", "skip_pull")]
        else:
            action = "enrich"
            gaps = []
        schema = str(sess.get("schema", decadal.get("defaults", {}).get("schema", "mbo")))
        ds = str(sess.get("dataset", dataset))
        sym = str(sess.get("symbol", ""))
        sup = _support_from_schema(schema)
        rows.append(
            {
                "dataset_id": _dataset_id("equities", "databento", ds, schema, f"{sym}:{sess.get('date', '')}"),
                "asset_class": "equities",
                "source_vendor": "databento",
                "venue": ds,
                "symbol_universe": sym,
                "instrument_universe": sess.get("id"),
                "date_coverage": str(sess.get("date", "")),
                "available_schemas": [schema, "ohlcv-1d"],
                "mbo_available": schema == "mbo" and not sess.get("skip_pull"),
                "mbp_10_available": False,
                "trades_available": schema == "mbo",
                "auction_imbalance_available": False,
                "definition_available": True,
                "timestamp_precision": "nanoseconds",
                "event_sequencing_quality": "full" if schema == "mbo" else "degraded",
                "session_calendar_coverage": "single session",
                "instrument_metadata_coverage": "decadal_runners.yaml",
                **sup,
                "known_gaps": gaps,
                "recommended_action": action,
                "config_paths": [str(decadal_path.relative_to(repo))],
            }
        )
    opt_block = (decadal.get("defaults") or {}).get("options") or {}
    if opt_block.get("enabled"):
        oschema = str(opt_block.get("schema", "cbbo-1m"))
        osup = _support_from_schema(oschema)
        rows.append(
            {
                "dataset_id": _dataset_id(
                    "equities_options",
                    "databento",
                    str(opt_block.get("dataset", "OPRA.PILLAR")),
                    oschema,
                    "chain_per_session",
                ),
                "asset_class": "equities_options",
                "source_vendor": "databento",
                "venue": opt_block.get("dataset", "OPRA.PILLAR"),
                "symbol_universe": "OPRA chain per decadal session",
                "instrument_universe": "options_chain_pull",
                "date_coverage": "inherits session window",
                "available_schemas": [oschema],
                "mbo_available": False,
                "mbp_10_available": False,
                "trades_available": False,
                "auction_imbalance_available": False,
                "definition_available": True,
                "timestamp_precision": "minutes",
                "event_sequencing_quality": "none",
                "session_calendar_coverage": "per parent session",
                "instrument_metadata_coverage": "chain_rules in decadal_runners.yaml",
                **osup,
                "known_gaps": ["not full L3; contract-level imbalance needs eligibility gate"],
                "recommended_action": "enrich",
                "config_paths": [str(decadal_path.relative_to(repo))],
            }
        )
    return rows


def _options_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    path = repo / "packages" / "options_lane" / "config" / "parity_universe.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) if path.is_file() else {}
    for group in raw.get("parity_groups") or []:
        gid = str(group.get("id", "unknown"))
        for leg in group.get("legs") or []:
            ds = str(leg.get("dataset", "GLBX.MDP3"))
            sym = str(leg.get("symbol", ""))
            role = str(leg.get("role", ""))
            schema = "mbp-1"
            if role == "future":
                schema = "mbo"
            sup = _support_from_schema(schema)
            rows.append(
                {
                    "dataset_id": _dataset_id("options", "databento", ds, schema, f"{gid}:{role}:{sym}"),
                    "asset_class": "options" if role in ("call", "put") else "futures",
                    "source_vendor": "databento",
                    "venue": ds,
                    "symbol_universe": sym,
                    "instrument_universe": gid,
                    "date_coverage": "parity replay windows",
                    "available_schemas": [schema, "definition"],
                    "mbo_available": schema == "mbo",
                    "mbp_10_available": False,
                    "trades_available": schema == "mbo",
                    "auction_imbalance_available": False,
                    "definition_available": True,
                    "timestamp_precision": "nanoseconds" if schema == "mbo" else "top_of_book",
                    "event_sequencing_quality": "full" if schema == "mbo" else "proxy",
                    "session_calendar_coverage": "parity_group",
                    "instrument_metadata_coverage": "parity_universe.yaml",
                    **sup,
                    "known_gaps": ["mbp-1 only for option legs; quote eligibility required"],
                    "recommended_action": "enrich",
                    "config_paths": [str(path.relative_to(repo))],
                }
            )
    return rows


def build_imbalance_inventory(repo: Path | None = None) -> dict[str, Any]:
    repo = repo or ROOT
    datasets = _futures_rows(repo) + _equities_rows(repo) + _options_rows(repo)
    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "dataset_count": len(datasets),
        "datasets": datasets,
    }


def render_markdown(inv: dict[str, Any]) -> str:
    lines = [
        "# HFT3 imbalance data inventory",
        "",
        f"Generated: {inv['generated_at_utc']}",
        "",
        "Machine-readable: `runtime/data_audits/hft3_imbalance_inventory.json`.",
        "Regenerate: `python scripts/build_imbalance_inventory.py`.",
        "",
        "| dataset_id | asset_class | venue | book | order-flow | auction | action |",
        "|------------|-------------|-------|------|------------|---------|--------|",
    ]
    for d in inv.get("datasets", []):
        lines.append(
            f"| `{d['dataset_id']}` | {d['asset_class']} | {d.get('venue', '')} | "
            f"{d.get('book_imbalance_support')} | {d.get('order_flow_imbalance_support')} | "
            f"{d.get('auction_imbalance_support')} | {d.get('recommended_action')} |"
        )
    lines.extend(
        [
            "",
            "## Schema labeling rules",
            "",
            "- **mbo**: order-level; true `order_flow_imbalance`.",
            "- **mbp-10**: aggregated depth; book imbalance full; OFI proxy only.",
            "- **mbp-1**: top-of-book; book proxy; no true OFI.",
            "- **imbalance** (Databento): auction imbalance only — not continuous book imbalance.",
        ]
    )
    return "\n".join(lines)


def write_inventory(repo: Path | None = None) -> tuple[Path, Path]:
    repo = repo or ROOT
    inv = build_imbalance_inventory(repo)
    out_dir = repo / "runtime" / "data_audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "hft3_imbalance_inventory.json"
    json_path.write_text(json.dumps(inv, indent=2), encoding="utf-8")
    md_path = repo / "docs" / "hft3_imbalance_inventory.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(inv), encoding="utf-8")
    return json_path, md_path


if __name__ == "__main__":
    jp, mp = write_inventory()
    print(f"Wrote {jp}")
    print(f"Wrote {mp}")
