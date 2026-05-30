"""Phase 1 — generate runtime/audits/hfc3_l3_cross_asset_repo_audit.{md,json}."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]

REQUIRED_EVENT_TYPES = [
    "CPI",
    "CORE_CPI",
    "PPI",
    "CORE_PPI",
    "PCE",
    "CORE_PCE",
    "NFP",
    "UNEMPLOYMENT_CLAIMS",
    "FOMC_STATEMENT",
    "FOMC_PRESS",
    "FOMC_MINUTES",
    "FED_SPEAKER",
    "TREASURY_AUCTION",
    "TREASURY_REFUNDING",
    "EIA_CRUDE",
    "EIA_NATGAS",
    "USDA_WASDE",
    "USDA_CROP",
    "CASH_EQUITY_OPEN",
    "FUTURES_ROLL",
    "FUTURES_EXPIRY",
    "OPTIONS_EXPIRY",
    "PROP_FLATTEN_TOPSTEP",
    "PROP_REOPEN",
    "FRIDAY_CLOSE",
]


def _scan_npz(repo: Path) -> Dict[str, Any]:
    npz_dir = repo / "data" / "npz"
    files = list(npz_dir.glob("*_mbo.npz")) if npz_dir.is_dir() else []
    by_symbol: Counter[str] = Counter()
    by_event_type: Counter[str] = Counter()
    for p in files:
        parts = p.stem.split("_")
        sym = parts[0]
        by_symbol[sym] += 1
        for token in parts[1:]:
            if token in ("CPI", "NFP", "PROP", "FOMC"):
                by_event_type[token] += 1
                break
    return {
        "count": len(files),
        "by_symbol": dict(by_symbol),
        "by_event_type_prefix": dict(by_event_type),
        "paths_sample": [str(p.relative_to(repo)) for p in sorted(files)[:5]],
    }


def _events_csv_audit(repo: Path) -> Dict[str, Any]:
    csv_path = repo / "data_system" / "config" / "events.csv"
    df = pd.read_csv(csv_path)
    syms: set[str] = set()
    for row in df["symbols"]:
        syms.update(x.strip() for x in str(row).split(","))
    present_types = set(df["event_type"].astype(str))
    missing_types = [t for t in REQUIRED_EVENT_TYPES if t not in present_types and not any(
        t.replace("_", "") in et or et in t for et in present_types
    )]
    return {
        "row_count": int(len(df)),
        "event_type_counts": df["event_type"].value_counts().to_dict(),
        "symbols_in_csv": sorted(syms),
        "window_names": sorted(df["window_name"].unique().tolist()),
        "required_types_missing": missing_types,
        "required_types_present": sorted(present_types),
    }


def _hot_universe(repo: Path) -> Dict[str, Any]:
    path = repo / "workbench" / "config" / "hot_memory_universe.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    instruments = raw.get("instruments") or []
    tiers: Counter[str] = Counter()
    mbo_tradable: List[str] = []
    sensors: List[str] = []
    for inst in instruments:
        tier = inst.get("hot_memory_tier", "?")
        tiers[tier] += 1
        sym = inst.get("canonical_internal_symbol", "?")
        if inst.get("instrument_type") == "index_sensor" or inst.get("index_sensor_available"):
            sensors.append(sym)
        elif inst.get("order_book_available") and inst.get("tradable", True):
            mbo_tradable.append(sym)
    return {
        "instrument_count": len(instruments),
        "tiers": dict(tiers),
        "mbo_tradable_canonical": sorted(mbo_tradable),
        "contextual_sensors": sorted(sensors),
    }


def _code_path_assumptions() -> Dict[str, List[str]]:
    return {
        "single_order_book": [
            "features_engine/src/features/mbo_features.py:114 — one OrderBook per MBOFeatureExtractor",
            "features_engine/src/pipeline/market_state_pipeline.py:26 — one MBOFeatureExtractor",
            "MBOEvent has no symbol field — single-stream assumption",
        ],
        "cross_asset_placeholder": [
            "features_engine/src/pipeline/market_state_pipeline.py:31,67 — cross_asset_features never populated",
            "backtest_pipeline/src/hft_strategy.py — cross_asset_features={} in depth fallback",
            "features_engine/src/hypotheses/modules.py:401+ — HYP 16-20 expect ES/NQ/ZN keys",
        ],
        "es_mes_defaults": [
            "backtest_pipeline/src/runner.py:30,50 — default product=MES, single BacktestAsset",
            "backtest_pipeline/src/signal_backtester.py — TICK_VALUE_MES default",
            "features_engine/src/structural_models/model_02_cross_asset_lead_lag.py — leader ES target MES",
            "workbench default symbol MES.v.0; ES.v.0 fallback for pre-2019 Discovery only",
        ],
        "cpi_nfp_focus": [
            "data_system/config/events.csv — CPI×19, NFP×33, PROP×4 only",
            "features_engine/src/regime/event_context.py:52-55 — explicit CPI_TIGHT/NFP_TIGHT",
            "No FOMC or cash-open rows in events.csv (labels exist in event_context only)",
        ],
        "mbo_canonical": [
            "data_system/src/databento_client.py:28 — schema=mbo GLBX.MDP3",
            "features_engine/src/features/npz_feed.py — HftBacktest structured MBO array",
            "No L1/L2 download path in databento_client (correct for this stack)",
        ],
        "multi_symbol_tensor_gaps": [
            "backtest/adapters/rithmic_replay_loader.py — resolve_event_npz single path per event",
            "backtest_pipeline/src/converter.py — one symbol per NPZ file",
            "No runtime/event_snapshots/ multi-symbol tensor builder existed before hfc3/",
            "ReplayRunner accepts one data_path only",
        ],
    }


def build_audit(repo: Path | None = None) -> Dict[str, Any]:
    repo = repo or REPO
    manifest_rows = 0
    manifest_cost = 0.0
    mp = repo / "data" / "manifest.parquet"
    if mp.is_file():
        mdf = pd.read_parquet(mp)
        manifest_rows = int(len(mdf))
        manifest_cost = float(mdf["cost"].sum()) if "cost" in mdf.columns else 0.0

    audit: Dict[str, Any] = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo),
        "phase": "hfc3_l3_cross_asset_repo_audit",
        "answers": {
            "1_symbols_with_event_windows": _events_csv_audit(repo)["symbols_in_csv"],
            "2_symbols_with_mbo_downloads": _scan_npz(repo)["by_symbol"],
            "3_symbols_with_npz_conversion": _scan_npz(repo)["by_symbol"],
            "4_symbols_hftbacktest_replay_ready": _scan_npz(repo)["by_symbol"],
            "5_event_types_supported": _events_csv_audit(repo)["required_types_present"],
            "6_event_types_missing": _events_csv_audit(repo)["required_types_missing"],
            "7_true_mbo_derived_features": [
                "MBOFeatureExtractor 64-dim vector slots 0-26 (aggressor, depth, spread, queue, iceberg, vol)",
                "OrderBook L3 apply_event ADD/CANCEL/MODIFY/TRADE",
                "Regime slots 41-49 from RegimeFilter posterior",
            ],
            "8_cross_asset_placeholders_only": [
                "MarketState.cross_asset_features dict — empty in pipeline",
                "FeatureIndex.MAX_CONTRACT_TRADE_IMBALANCE slot 34 — unpopulated",
                "HYP 16-20 cross-asset hypotheses gated by HFT3_CROSS_ASSET env",
            ],
            "9_single_instrument_code_paths": _code_path_assumptions()["single_order_book"],
            "10_es_mes_only_assumptions": _code_path_assumptions()["es_mes_defaults"],
            "11_cpi_nfp_only_assumptions": _code_path_assumptions()["cpi_nfp_focus"],
            "12_multi_symbol_mbo_tensor_changes_needed": _code_path_assumptions()["multi_symbol_tensor_gaps"],
        },
        "events_csv": _events_csv_audit(repo),
        "npz_inventory": _scan_npz(repo),
        "hot_memory_universe": _hot_universe(repo),
        "manifest": {"rows": manifest_rows, "total_cost_usd": manifest_cost},
        "code_path_assumptions": _code_path_assumptions(),
        "latency_bands_ms": [0.5, 1.0, 2.0, 5.0, 10.0],
        "latency_bands_source": "backtest_pipeline/src/runner.py:16",
    }
    return audit


def write_audit(repo: Path | None = None) -> tuple[Path, Path]:
    repo = repo or REPO
    audit = build_audit(repo)
    out_dir = repo / "runtime" / "audits"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "hfc3_l3_cross_asset_repo_audit.json"
    md_path = out_dir / "hfc3_l3_cross_asset_repo_audit.md"
    json_path.write_text(json.dumps(audit, indent=2), encoding="utf-8")

    lines = [
        "# HFC3 Level-3 cross-asset repo audit",
        "",
        f"Generated: {audit['generated_at_utc']}",
        "",
        "## Summary",
        "",
        f"- **events.csv rows:** {audit['events_csv']['row_count']}",
        f"- **NPZ files:** {audit['npz_inventory']['count']}",
        f"- **Manifest downloads:** {audit['manifest']['rows']} (${audit['manifest']['total_cost_usd']:.4f})",
        "",
        "## Audit answers",
        "",
    ]
    for key, val in audit["answers"].items():
        title = key.replace("_", " ").strip()
        lines.append(f"### {title}")
        if isinstance(val, list):
            for item in val:
                lines.append(f"- {item}")
        elif isinstance(val, dict):
            for k, v in val.items():
                lines.append(f"- **{k}:** {v}")
        else:
            lines.append(f"- {val}")
        lines.append("")

    lines.extend(
        [
            "## Code path assumptions",
            "",
        ]
    )
    for category, items in audit["code_path_assumptions"].items():
        lines.append(f"### {category}")
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(
        [
            "## Required changes for multi-symbol MBO event tensors",
            "",
            "1. Load one NPZ per instrument per event_id (not single MES path).",
            "2. Build per-symbol OrderBook state at anchor offsets T±{300..1}s.",
            "3. Populate `cross_asset_features` from MBO-derived state (not L1/L2 quotes).",
            "4. Extend `events.csv` / calendar for FOMC, rates, energy, USDA, etc.",
            "5. Mark VIX/VVIX as SENSOR_ONLY — never force into MBO schema.",
            "6. Wrapper replay: primary execution instrument + cross-asset feature feed.",
            "",
            "See `hfc3/events/l3_event_snapshot_tensor.py` and `hfc3/features/cross_asset_l3_event_features.py`.",
        ]
    )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path, json_path


if __name__ == "__main__":
    md, js = write_audit()
    print(f"Wrote {md}")
    print(f"Wrote {js}")
