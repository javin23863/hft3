"""Phase 2 + 9 — MBO cross-asset inventory and missing data jobs."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

_REPO_FOR_BOOTSTRAP = Path(__file__).resolve().parents[3]
if str(_REPO_FOR_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_REPO_FOR_BOOTSTRAP))
for sub in ("packages", "apps"):
    p = str(_REPO_FOR_BOOTSTRAP / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

import pandas as pd
import yaml

from hft3_bootstrap import data_system_root, repo_root as _repo_root, workbench_root

REPO = _repo_root()

DOWNLOAD_PRIORITY = [
    ["ES", "NQ", "YM", "RTY"],
    ["ZT", "ZF", "ZN", "ZB", "UB", "SR3", "ZQ"],
    ["GC", "HG"],
    ["CL", "NG"],
    ["6E"],
    ["VIX", "VVIX", "VX1", "VX2"],
    ["RB", "HO", "SI", "6J", "6B", "6A", "6C"],
    ["ZC", "ZS", "ZW", "KE", "ZL", "ZM"],
    ["LE", "GF", "HE"],
]

MBO_STATUS_VALUES = (
    "MBO_LIVE",
    "MBO_HISTORICAL",
    "MBO_MISSING",
    "MBO_DEGRADED",
    "SENSOR_ONLY",
    "DISABLED",
)

PILOT_BASKET_SYMBOLS = (
    "MES.v.0",
    "MNQ.v.0",
    "ES.v.0",
    "NQ.v.0",
    "ZN.v.0",
    "ZB.v.0",
    "RTY.v.0",
)

MANIFEST_REL = Path("packages") / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json"


def _load_pilot_basket_manifest(repo: Path) -> dict[str, Any] | None:
    for candidate in (
        repo / MANIFEST_REL,
        repo / "data_system" / "config" / "mbo_pilot_basket_20260605_manifest.json",
    ):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    return None


def _pilot_basket_gap_summary(manifest: dict[str, Any] | None) -> dict[str, Any]:
    if not manifest:
        return {}
    coverage = manifest.get("coverage") or {}
    return {
        "run_id": manifest.get("run_id"),
        "status": manifest.get("status"),
        "pilot_symbols": list(PILOT_BASKET_SYMBOLS),
        "partial_windows": manifest.get("partial_windows") or [],
        "no_market_windows": manifest.get("no_market_windows") or [],
        "missing_or_unavailable_slots": coverage.get("missing_or_unavailable_slots"),
        "present_runnable_npz_slots": coverage.get("present_runnable_npz_slots"),
    }


def _load_hot_universe(repo: Path) -> List[Dict[str, Any]]:
    path = workbench_root(repo) / "config" / "hot_memory_universe.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return list(raw.get("instruments") or [])


def _npz_index(repo: Path) -> Dict[str, List[str]]:
    from data_system.src.data_roots import npz_search_dirs

    index: Dict[str, List[str]] = {}
    seen_files: set[str] = set()
    for npz_dir in npz_search_dirs(repo):
        if not npz_dir.is_dir():
            continue
        for p in npz_dir.glob("*_mbo.npz"):
            key = p.name.lower()
            if key in seen_files:
                continue
            seen_files.add(key)
            sym = p.name.split("_")[0]
            rel = str(p.relative_to(repo)) if str(p).startswith(str(repo)) else str(p.resolve())
            index.setdefault(sym, []).append(rel)
    return index


def _events_symbols(repo: Path) -> Dict[str, List[str]]:
    csv_path = data_system_root(repo) / "config" / "events.csv"
    if not csv_path.is_file():
        csv_path = repo / "packages" / "data_system" / "config" / "events.csv"
    df = pd.read_csv(csv_path)
    out: Dict[str, set[str]] = {}
    for _, row in df.iterrows():
        eid = str(row["event_id"])
        syms = [x.strip() for x in str(row["symbols"]).split(",")]
        out[eid] = syms
    return {k: sorted(v) for k, v in out.items()}


def _classify_mbo_status(inst: Dict[str, Any], npz_index: Dict[str, List[str]]) -> str:
    if inst.get("instrument_type") == "index_sensor" or inst.get("index_sensor_available"):
        return "SENSOR_ONLY"
    if not inst.get("tradable", True):
        return "DISABLED"
    if not inst.get("order_book_available", True):
        return "MBO_DEGRADED"
    rs = inst.get("research_symbol", "")
    if rs in npz_index and npz_index[rs]:
        return "MBO_HISTORICAL"
    hist = inst.get("historical_feed_status", "MISSING")
    if hist == "MISSING":
        return "MBO_MISSING"
    return "MBO_DEGRADED"


def _priority_rank(canonical: str) -> int:
    for i, group in enumerate(DOWNLOAD_PRIORITY):
        if canonical in group:
            return i
    return len(DOWNLOAD_PRIORITY)


def build_inventory(repo: Path | None = None) -> Dict[str, Any]:
    repo = repo or REPO
    from data_system.src.data_roots import paid_data_root, npz_search_dirs

    instruments = _load_hot_universe(repo)
    npz_index = _npz_index(repo)
    event_syms = _events_symbols(repo)
    paid_root = paid_data_root(repo)
    search_dirs = [str(d) for d in npz_search_dirs(repo)]
    pilot_manifest = _load_pilot_basket_manifest(repo)
    pilot_gaps = _pilot_basket_gap_summary(pilot_manifest)

    rows: List[Dict[str, Any]] = []
    for inst in instruments:
        canonical = str(inst.get("canonical_internal_symbol", ""))
        research_sym = str(inst.get("research_symbol", ""))
        npz_paths = npz_index.get(research_sym, [])
        mbo_status = _classify_mbo_status(inst, npz_index)
        rows.append(
            {
                "canonical_symbol": canonical,
                "research_symbol": research_sym,
                "pilot_basket_symbol": research_sym in PILOT_BASKET_SYMBOLS,
                "data_vendor_symbol": inst.get("data_vendor_symbol"),
                "databento_dataset": inst.get("venue", "GLBX") + ".MDP3" if inst.get("venue") == "GLBX" else inst.get("venue"),
                "expected_schema": "mbo" if mbo_status not in ("SENSOR_ONLY", "DISABLED") else None,
                "hot_memory_tier": inst.get("hot_memory_tier"),
                "asset_class": inst.get("asset_class"),
                "tradable": inst.get("tradable", True),
                "mbo_status": mbo_status,
                "npz_paths": npz_paths,
                "npz_event_count": len(npz_paths),
                "replay_ready": bool(npz_paths) and mbo_status == "MBO_HISTORICAL",
                "hftbacktest_compatible": bool(npz_paths),
                "broker_symbol": inst.get("broker_symbol"),
                "event_windows_in_csv": sum(
                    1 for syms in event_syms.values() if research_sym in syms
                ),
                "live_feed_status": inst.get("live_feed_status", "MISSING"),
                "historical_feed_status": inst.get("historical_feed_status", "MISSING"),
            }
        )

    missing_jobs: List[Dict[str, Any]] = []
    for row in rows:
        if row["mbo_status"] not in ("MBO_MISSING", "MBO_DEGRADED"):
            continue
        if row["mbo_status"] == "SENSOR_ONLY":
            continue
        sym = row["research_symbol"]
        if not sym or sym in ("VIX", "VVIX"):
            missing_jobs.append(
                {
                    "missing_symbol": row["canonical_symbol"],
                    "research_symbol": sym,
                    "required_schema": None,
                    "dataset": "CBOE/CFE contextual",
                    "mbo_status": "SENSOR_ONLY",
                    "priority_rank": _priority_rank(row["canonical_symbol"]),
                    "blocks_hot_research": False,
                    "fallback_status": "Use licensed index sensor feed; do not force MBO schema",
                    "proposed_command": None,
                }
            )
            continue
        missing_jobs.append(
            {
                "missing_symbol": row["canonical_symbol"],
                "research_symbol": sym,
                "required_schema": "mbo",
                "dataset": row["databento_dataset"],
                "date_range_needed": "2018-present event windows per events.csv expansion",
                "event_types_needed": ["CPI", "NFP", "FOMC", "EIA", "USDA", "macro"],
                "priority_rank": _priority_rank(row["canonical_symbol"]),
                "estimated_cost_usd": None,
                "blocks_hot_research": row["hot_memory_tier"] in ("HOT_EXECUTABLE", "HOT_SENSOR"),
                "fallback_status": "MBO_DEGRADED — do not substitute L1/L2 as equivalent",
                "proposed_command": (
                    f"python workbench/scripts/backfill_catalog.py --model HYP_5 --symbol {sym} "
                    f"--download-missing --max-cost-usd 25"
                    if sym.endswith(".v.0")
                    else None
                ),
            }
        )

    missing_jobs.sort(key=lambda j: (j.get("priority_rank", 99), j["missing_symbol"]))

    return {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "paid_data_root": str(paid_root),
        "npz_search_dirs": search_dirs,
        "pilot_basket_note": (
            "MBO pilot basket (7 symbols × 720 windows) may live under paid_data_root; "
            "see docs/research/MBO_PILOT_BASKET_20260605.md"
        ),
        "pilot_basket_gaps": pilot_gaps,
        "instrument_count": len(rows),
        "mbo_historical_count": sum(1 for r in rows if r["mbo_status"] == "MBO_HISTORICAL"),
        "mbo_missing_count": sum(1 for r in rows if r["mbo_status"] == "MBO_MISSING"),
        "sensor_only_count": sum(1 for r in rows if r["mbo_status"] == "SENSOR_ONLY"),
        "npz_total_files": sum(len(v) for v in npz_index.values()),
        "instruments": rows,
        "missing_mbo_data_jobs": missing_jobs,
    }


def write_inventory(repo: Path | None = None) -> tuple[Path, Path, Path, Path]:
    repo = repo or REPO
    inv = build_inventory(repo)
    data_dir = repo / "runtime" / "data_audits"
    data_dir.mkdir(parents=True, exist_ok=True)

    inv_json = data_dir / "hfc3_mbo_cross_asset_inventory.json"
    inv_md = data_dir / "hfc3_mbo_cross_asset_inventory.md"
    jobs_json = data_dir / "hfc3_missing_mbo_data_jobs.json"
    jobs_md = data_dir / "hfc3_missing_mbo_data_jobs.md"

    inv_json.write_text(json.dumps(inv, indent=2), encoding="utf-8")
    jobs_json.write_text(json.dumps(inv["missing_mbo_data_jobs"], indent=2), encoding="utf-8")

    md_lines = [
        "# HFC3 MBO cross-asset inventory",
        "",
        f"Generated: {inv['generated_at_utc']}",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Instruments | {inv['instrument_count']} |",
        f"| MBO historical (NPZ on disk) | {inv['mbo_historical_count']} |",
        f"| MBO missing | {inv['mbo_missing_count']} |",
        f"| Sensor only | {inv['sensor_only_count']} |",
        f"| Total NPZ files | {inv['npz_total_files']} |",
        "",
        "**Success criterion:** MBO availability or explicit `MBO_MISSING` / `SENSOR_ONLY` — not L1/L2.",
        "",
        "## HOT tradable MBO status",
        "",
        "| Symbol | research_symbol | tier | mbo_status | npz_events | replay_ready |",
        "|--------|-----------------|------|------------|------------|--------------|",
    ]
    for r in inv["instruments"]:
        if r["hot_memory_tier"] not in ("HOT_EXECUTABLE", "HOT_SENSOR", "WARM"):
            continue
        md_lines.append(
            f"| {r['canonical_symbol']} | {r['research_symbol']} | {r['hot_memory_tier']} | "
            f"{r['mbo_status']} | {r['npz_event_count']} | {r['replay_ready']} |"
        )
    inv_md.write_text("\n".join(md_lines), encoding="utf-8")

    job_lines = [
        "# HFC3 missing MBO data jobs",
        "",
        f"Generated: {inv['generated_at_utc']}",
        "",
        "Priority order: equity index → rates → metals → energy → FX → vol sensors → warm → cold.",
        "",
        "| Priority | Symbol | schema | blocks HOT | proposed command |",
        "|----------|--------|--------|------------|------------------|",
    ]
    for j in inv["missing_mbo_data_jobs"]:
        if j.get("mbo_status") == "SENSOR_ONLY":
            continue
        cmd = j.get("proposed_command") or "—"
        job_lines.append(
            f"| {j.get('priority_rank', '?')} | {j['missing_symbol']} | {j.get('required_schema', '?')} | "
            f"{j.get('blocks_hot_research', False)} | `{cmd}` |"
        )
    jobs_md.write_text("\n".join(job_lines), encoding="utf-8")

    return inv_md, inv_json, jobs_md, jobs_json


if __name__ == "__main__":
    paths = write_inventory()
    for p in paths:
        print(f"Wrote {p}")
