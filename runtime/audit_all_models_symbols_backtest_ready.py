#!/usr/bin/env python3
"""Audit MBO NPZ readiness: all registry models x default CME symbols x WF periods."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from data_system.src.events_parser import load_and_parse_events
from data_system.src.npz_resolver import resolve_npz_for_event
from data_system.src.event_data_resolver import resolve_sensor_for_event
from economic_event_universe.registry import default_cme_symbols
from features_engine.src.model_registry import load_model_registry
from workbench.src.data.event_catalog import (
    _repo_paths,
    list_campaign_events,
    load_model_binding,
    load_periods,
    row_to_event_context,
)
from workbench.src.data.personal_lock import is_personal_sandbox_date


def binding_requires_sensor(binding: dict[str, Any]) -> bool:
    required = {str(x).lower() for x in (binding.get("required_datasets") or [])}
    return bool(required & {"vix_sensor", "sensors", "cross_asset_sensors"})


def release_year(release_date: str) -> int:
    return datetime.strptime(release_date, "%Y-%m-%d").year


def preprocess_rows(df, repo_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        release_date = str(row["release_date"])
        if is_personal_sandbox_date(release_date, repo_root):
            continue
        parsed = tuple(str(s) for s in row["parsed_symbols"])
        ctx = row_to_event_context(str(row["event_type"]), str(row["window_name"]))
        rows.append(
            {
                "event_id": str(row["event_id"]),
                "release_date": release_date,
                "year": release_year(release_date),
                "context": ctx,
                "parsed_symbols": parsed,
            }
        )
    return rows


def build_npz_cache(repo_root: Path, rows: list[dict], symbols: list[str]) -> dict[tuple[str, str], bool]:
    cache: dict[tuple[str, str], bool] = {}
    for r in rows:
        eid = r["event_id"]
        parsed = r["parsed_symbols"]
        for sym in symbols:
            if sym not in parsed:
                continue
            key = (eid, sym)
            if key in cache:
                continue
            _, present, _ = resolve_npz_for_event(repo_root, eid, sym, parsed)
            cache[key] = present
    return cache


def build_sensor_cache(repo_root: Path, rows: list[dict]) -> dict[str, bool]:
    cache: dict[str, bool] = {}
    for r in rows:
        eid = r["event_id"]
        if eid in cache:
            continue
        _, present = resolve_sensor_for_event(repo_root, eid)
        cache[eid] = present
    return cache


def count_for_model_symbol(
    allowed: set[str],
    require_sensor: bool,
    symbol: str,
    periods,
    rows: list[dict],
    npz_cache: dict[tuple[str, str], bool],
    sensor_cache: dict[str, bool],
) -> dict[str, Any]:
    by_period: dict[str, dict] = {}
    total = 0
    ready = 0
    for period in periods:
        pt = 0
        pr = 0
        for r in rows:
            if r["year"] < period.start_year or r["year"] > period.end_year:
                continue
            if symbol not in r["parsed_symbols"]:
                continue
            if allowed and r["context"] not in allowed:
                continue
            if require_sensor and not sensor_cache.get(r["event_id"], False):
                continue
            pt += 1
            if npz_cache.get((r["event_id"], symbol), False):
                pr += 1
        by_period[period.name] = {
            "total_events": pt,
            "npz_ready": pr,
            "pct_ready": round(100.0 * pr / pt, 2) if pt else None,
        }
        total += pt
        ready += pr
    return {
        "total_events": total,
        "npz_ready": ready,
        "pct_ready": round(100.0 * ready / total, 2) if total else None,
        "periods": by_period,
    }


def main() -> int:
    repo_root = _REPO
    reg = load_model_registry().get("models", {})
    model_slugs = sorted(reg.keys())
    models_meta = [
        {
            "slug": slug,
            "legacy_id": (reg[slug] or {}).get("legacy_id", slug),
            "kind": (reg[slug] or {}).get("kind", ""),
        }
        for slug in model_slugs
    ]
    symbols = list(default_cme_symbols())
    periods = load_periods(repo_root)
    csv_path = _repo_paths(repo_root)["events_csv"]
    df = load_and_parse_events(str(csv_path))
    rows = preprocess_rows(df, repo_root)
    npz_cache = build_npz_cache(repo_root, rows, symbols)
    sensor_cache = build_sensor_cache(repo_root, rows)

    per_combo: list[dict] = []
    per_model_symbol: dict[str, dict[str, dict]] = {}
    error_models: dict[str, str] = {}
    grand_total = 0
    grand_ready = 0

    for slug in model_slugs:
        per_model_symbol[slug] = {}
        try:
            binding = load_model_binding(repo_root, slug)
        except Exception as ex:
            err = str(ex)
            error_models[slug] = err
            for symbol in symbols:
                row = {
                    "model": slug,
                    "legacy_id": reg[slug].get("legacy_id", slug),
                    "symbol": symbol,
                    "error": err,
                    "total_events": 0,
                    "npz_ready": 0,
                    "pct_ready": None,
                    "periods": {},
                }
                per_combo.append(row)
                per_model_symbol[slug][symbol] = {
                    "total_events": 0,
                    "npz_ready": 0,
                    "pct_ready": None,
                    "periods": {},
                    "error": err,
                }
            continue

        allowed = set(binding["allowed_contexts"])
        require_sensor = binding_requires_sensor(binding)
        for symbol in symbols:
            stats = count_for_model_symbol(
                allowed, require_sensor, symbol, periods, rows, npz_cache, sensor_cache
            )
            row = {
                "model": slug,
                "legacy_id": reg[slug].get("legacy_id", slug),
                "symbol": symbol,
                "error": None,
                **stats,
            }
            per_combo.append(row)
            per_model_symbol[slug][symbol] = {**stats, "error": None}
            grand_total += stats["total_events"]
            grand_ready += stats["npz_ready"]

    per_model_summary = []
    for slug in model_slugs:
        sym_stats = per_model_symbol[slug]
        pcts = [s["pct_ready"] for s in sym_stats.values() if s.get("pct_ready") is not None]
        totals = [s.get("total_events", 0) for s in sym_stats.values()]
        readys = [s.get("npz_ready", 0) for s in sym_stats.values()]
        by_sym_pct = {sym: sym_stats[sym].get("pct_ready") for sym in symbols}
        per_model_summary.append(
            {
                "model": slug,
                "legacy_id": reg[slug].get("legacy_id", slug),
                "kind": reg[slug].get("kind", ""),
                "pct_by_symbol": by_sym_pct,
                "pct_min_across_symbols": min(pcts) if pcts else None,
                "pct_max_across_symbols": max(pcts) if pcts else None,
                "total_events_all_symbols": sum(totals),
                "npz_ready_all_symbols": sum(readys),
                "pct_all_symbols_combined": round(100.0 * sum(readys) / sum(totals), 2)
                if sum(totals)
                else None,
            }
        )

    combos_with_events = [c for c in per_combo if c.get("total_events", 0) > 0 and not c.get("error")]
    combos_sorted = sorted(
        combos_with_events,
        key=lambda c: (c.get("pct_ready") if c.get("pct_ready") is not None else -1.0, c["total_events"]),
    )
    worst5 = combos_sorted[:5]

    models_100_any = []
    models_100_none = []
    for m in per_model_summary:
        pcts = [p for p in m["pct_by_symbol"].values() if p is not None]
        if any(p == 100.0 for p in pcts):
            models_100_any.append(m["model"])
        else:
            models_100_none.append(m["model"])

    overall_pct = round(100.0 * grand_ready / grand_total, 2) if grand_total else 0.0

    spot_ok = None
    if model_slugs and symbols and periods:
        slug0, sym0, per0 = model_slugs[0], symbols[0], periods[0]
        evs = list_campaign_events(slug0, per0, sym0, repo_root)
        got = per_model_symbol[slug0][sym0]["periods"][per0.name]
        spot_ok = got["total_events"] == len(evs) and got["npz_ready"] == sum(1 for e in evs if e.npz_present)

    zero_event_combos = sum(1 for c in per_combo if c.get("total_events", 0) == 0 and not c.get("error"))

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "events_csv": str(csv_path),
        "events_csv_rows": len(df),
        "promotion_rows_after_personal_filter": len(rows),
        "symbols": symbols,
        "periods": [p.name for p in periods],
        "model_count": len(model_slugs),
        "model_symbol_combos": len(model_slugs) * len(symbols),
        "method": "list_campaign_events filters; npz/sensor cached; spot-checked vs list_campaign_events",
        "spot_check_list_campaign_events": spot_ok,
        "overall": {
            "event_slots_total": grand_total,
            "event_slots_npz_ready": grand_ready,
            "event_slots_missing_npz": grand_total - grand_ready,
            "pct_ready": overall_pct,
            "full_matrix_ready_today": grand_ready == grand_total and grand_total > 0,
        },
        "zero_event_model_symbol_combos": zero_event_combos,
        "models_meta": models_meta,
        "per_model_summary": per_model_summary,
        "per_model_symbol": per_model_symbol,
        "worst_5_model_symbol": worst5,
        "models_100pct_ready_on_at_least_one_symbol": sorted(models_100_any),
        "models_never_100pct_on_any_symbol": sorted(models_100_none),
        "binding_errors": error_models,
    }

    out_path = repo_root / "runtime" / "data_audits" / "all_models_symbols_backtest_ready.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "overall": out["overall"],
                "worst_5": worst5,
                "models_100_any": len(models_100_any),
                "models_100_none": len(models_100_none),
                "spot_check": spot_ok,
                "path": str(out_path),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
