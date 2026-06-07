#!/usr/bin/env python3
"""Audit MBO NPZ readiness: all registry models x default CME symbols x WF periods.

Fast variant: precomputes NPZ presence from manifest parquet and event metadata
from events.csv, then applies model binding (allowed_contexts) without per-event
Path.exists() calls. Apples-to-apples with the original audit but ~100x faster.
"""
from __future__ import annotations

import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

import pandas as pd

from economic_event_universe.registry import default_cme_symbols
from features_engine.src.model_registry import load_model_registry
from workbench.src.data.event_catalog import load_model_binding, load_periods, row_to_event_context


def main() -> int:
    repo_root = _REPO
    reg = load_model_registry().get("models", {})
    model_slugs = sorted(reg.keys())
    models_meta = []
    for slug in model_slugs:
        entry = reg[slug] or {}
        models_meta.append(
            {
                "slug": slug,
                "legacy_id": entry.get("legacy_id", slug),
                "kind": entry.get("kind", ""),
            }
        )

    symbols = list(default_cme_symbols())
    periods = load_periods(repo_root)

    # 1. Precompute eid -> {sym, ...} (all symbols that have NPZ for that eid) from data/npz/*.npz
    # The manifest's output_path points to raw DBN, not NPZ, so we glob data/npz/*.npz.
    npz_dir = repo_root / "data" / "npz"
    if not npz_dir.exists():
        print(f"no npz dir at {npz_dir}", file=sys.stderr)
        return 1
    import re
    eid_to_syms: dict[str, set[str]] = {}
    unparsed_files: list[str] = []
    for npz_path in npz_dir.glob("*.npz"):
        name = npz_path.stem
        parts = name.split("_")
        if len(parts) < 5:
            unparsed_files.append(name)
            continue
        sym = parts[0]
        yyyy_idx = None
        for i, p in enumerate(parts):
            if re.match(r"^\d{4}$", p):
                yyyy_idx = i
                break
        if yyyy_idx is None or yyyy_idx + 3 >= len(parts):
            unparsed_files.append(name)
            continue
        date = f"{parts[yyyy_idx]}_{parts[yyyy_idx+1]}_{parts[yyyy_idx+2]}"
        window = parts[yyyy_idx + 3]
        eid = "_".join(parts[1:yyyy_idx] + [date, window])
        eid_to_syms.setdefault(eid, set()).add(sym)
    print(f"eid_to_syms (from NPZ files): {len(eid_to_syms)} eids | unparsed: {len(unparsed_files)}", flush=True)
    if unparsed_files:
        print(f"  first 3 unparsed: {unparsed_files[:3]}", flush=True)

    # Symbol fallback order (matches resolve_npz_for_event)
    SYMBOL_FALLBACK = ("ES.v.0", "MNQ.v.0", "NQ.v.0")

    # 2. Precompute per-model allowed_contexts and required_datasets
    bindings: dict[str, dict] = {}
    for slug in model_slugs:
        try:
            b = load_model_binding(repo_root, slug)
            bindings[slug] = {
                "allowed_contexts": set(b.get("allowed_contexts", []) or []),
                "required_datasets": {str(x).lower() for x in (b.get("required_datasets") or [])},
            }
        except Exception as ex:
            print(f"  binding load failed for {slug}: {ex}", file=sys.stderr, flush=True)
            bindings[slug] = {"allowed_contexts": set(), "required_datasets": set()}

    # 3. Precompute event metadata from events.csv
    events_csv = repo_root / "packages" / "data_system" / "config" / "events.csv"
    edf = pd.read_csv(events_csv)
    edf["release_year"] = edf["release_date"].str[:4].astype(int)
    edf["parsed_symbols"] = edf["symbols"].fillna("").str.split(",")
    edf["parsed_symbols"] = edf["parsed_symbols"].apply(lambda lst: [s.strip() for s in lst] if isinstance(lst, list) else [])
    edf["ctx"] = edf.apply(lambda r: row_to_event_context(str(r["event_type"]), str(r["window_name"])), axis=1)
    print(f"events rows: {len(edf)}", flush=True)

    # 4. Precompute per-symbol-per-period event counts and ready counts (no model binding)
    # AND per-model filtered counts.
    per_sp_total: dict[tuple[str, str], list[str]] = {}  # (sym, period_name) -> list[event_id]
    for symbol in symbols:
        for period in periods:
            mask = (
                edf["parsed_symbols"].apply(lambda lst, sym=symbol: sym in lst)
                & edf["release_year"].between(period.start_year, period.end_year)
            )
            eids = edf.loc[mask, "event_id"].astype(str).tolist()
            per_sp_total[(symbol, period.name)] = eids

    # 5. Iterate all (model, symbol, period) using the precomputed list, filter by binding
    per_combo: list[dict] = []
    per_model_symbol: dict[str, dict[str, dict]] = {}
    blocked_models: list[str] = []
    error_models: dict[str, str] = {}

    grand_total = 0
    grand_ready = 0

    for slug in model_slugs:
        per_model_symbol[slug] = {}
        model_blocked = False
        allowed = bindings[slug]["allowed_contexts"]
        # Empty allowed_contexts means "all contexts allowed" (matches original audit behavior).
        all_contexts_allowed = not allowed
        require_sensor = bool(bindings[slug]["required_datasets"] & {"vix_sensor", "sensors", "cross_asset_sensors"})

        for symbol in symbols:
            total = 0
            ready = 0
            by_period: dict[str, dict] = {}
            err = None
            try:
                for period in periods:
                        eids = per_sp_total[(symbol, period.name)]
                        # Filter by allowed_contexts: empty allowed means all contexts OK
                        sym_mask = edf["parsed_symbols"].apply(lambda lst, sym=symbol: sym in lst)
                        yr_mask = edf["release_year"].between(period.start_year, period.end_year)
                        if all_contexts_allowed:
                            mask = sym_mask & yr_mask
                        else:
                            mask = sym_mask & yr_mask & edf["ctx"].isin(allowed)
                        sub = edf.loc[mask]
                        t = len(sub)
                        if t == 0:
                            by_period[period.name] = {"total_events": 0, "npz_ready": 0, "pct_ready": None}
                            continue
                        r = 0
                        for eid in sub["event_id"].astype(str):
                            syms = eid_to_syms.get(eid)
                            if not syms:
                                continue
                            if symbol in syms:
                                r += 1
                                continue
                            # Try symbol fallback (matches resolve_npz_for_event)
                            for fb in SYMBOL_FALLBACK:
                                if fb in syms:
                                    r += 1
                                    break
                        by_period[period.name] = {
                            "total_events": t,
                            "npz_ready": r,
                            "pct_ready": round(100.0 * r / t, 2) if t else None,
                        }
                        total += t
                        ready += r
            except Exception as ex:
                err = f"{type(ex).__name__}: {ex}"
                traceback.print_exc()

            pct = round(100.0 * ready / total, 2) if total else None
            row = {
                "model": slug,
                "legacy_id": reg[slug].get("legacy_id", slug),
                "symbol": symbol,
                "total_events": total,
                "npz_ready": ready,
                "pct_ready": pct,
                "periods": by_period,
                "error": err,
            }
            per_combo.append(row)
            per_model_symbol[slug][symbol] = {
                "total_events": total,
                "npz_ready": ready,
                "pct_ready": pct,
                "periods": by_period,
                "error": err,
            }
            if err and slug not in blocked_models:
                blocked_models.append(slug)
            if err and slug not in error_models:
                error_models[slug] = err

            if not err and not model_blocked:
                grand_total += total
                grand_ready += ready

    # Per-model summary across symbols
    per_model_summary = []
    for slug in model_slugs:
        sym_stats = per_model_symbol[slug]
        pcts = [s["pct_ready"] for s in sym_stats.values() if s["pct_ready"] is not None]
        totals = [s["total_events"] for s in sym_stats.values()]
        readys = [s["npz_ready"] for s in sym_stats.values()]
        by_sym_pct = {
            sym: sym_stats[sym]["pct_ready"] for sym in symbols if sym in sym_stats
        }
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
                "pct_all_symbols_combined": round(
                    100.0 * sum(readys) / sum(totals), 2
                )
                if sum(totals)
                else None,
            }
        )

    combos_with_events = [c for c in per_combo if c["total_events"] > 0 and not c["error"]]
    combos_sorted = sorted(
        combos_with_events,
        key=lambda c: (c["pct_ready"] if c["pct_ready"] is not None else -1.0, c["total_events"]),
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
    full_matrix_ready = grand_ready == grand_total and grand_total > 0

    # Distribution buckets across all 51 models' pct_all_symbols_combined
    pct_buckets = {"0%": 0, "1-25%": 0, "26-50%": 0, "51-75%": 0, "76-99%": 0, "100%": 0, "None": 0}
    for m in per_model_summary:
        p = m["pct_all_symbols_combined"]
        if p is None:
            pct_buckets["None"] += 1
        elif p == 0:
            pct_buckets["0%"] += 1
        elif p == 100.0:
            pct_buckets["100%"] += 1
        elif p >= 76:
            pct_buckets["76-99%"] += 1
        elif p >= 51:
            pct_buckets["51-75%"] += 1
        elif p >= 26:
            pct_buckets["26-50%"] += 1
        else:
            pct_buckets["1-25%"] += 1

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(repo_root),
        "symbols": symbols,
        "periods": [p.name for p in periods],
        "model_count": len(model_slugs),
        "model_symbol_combos": len(model_slugs) * len(symbols),
        "overall": {
            "event_slots_total": grand_total,
            "event_slots_npz_ready": grand_ready,
            "pct_ready": overall_pct,
            "full_matrix_ready_today": full_matrix_ready,
        },
        "models_meta": models_meta,
        "per_model_summary": per_model_summary,
        "per_model_symbol": {
            slug: per_model_symbol[slug] for slug in model_slugs
        },
        "worst_5_model_symbol": worst5,
        "models_100pct_ready_on_at_least_one_symbol": sorted(models_100_any),
        "models_never_100pct_on_any_symbol": sorted(models_100_none),
        "blocked_or_binding_errors": error_models,
        "model_pct_distribution": pct_buckets,
        "audit_variant": "fast_v2_binding_aware",
    }

    out_path = repo_root / "runtime" / "data_audits" / "all_models_symbols_backtest_fast.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({
        "overall": out["overall"],
        "model_pct_distribution": pct_buckets,
        "worst_5": worst5,
        "path": str(out_path),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
