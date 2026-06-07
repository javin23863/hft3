#!/usr/bin/env python3
"""Audit MBO NPZ readiness: all registry models x default CME symbols x WF periods."""
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

from economic_event_universe.registry import default_cme_symbols
from features_engine.src.model_registry import load_model_registry
from workbench.src.data.event_catalog import list_campaign_events, load_periods


def main() -> int:
    repo_root = _REPO
    reg = load_model_registry().get("models", {})
    models_meta = []
    model_slugs = sorted(reg.keys())
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

    per_combo: list[dict] = []
    per_model_symbol: dict[str, dict[str, dict]] = {}
    blocked_models: list[str] = []
    error_models: dict[str, str] = {}

    grand_total = 0
    grand_ready = 0

    for slug in model_slugs:
        per_model_symbol[slug] = {}
        model_blocked = False
        for symbol in symbols:
            total = 0
            ready = 0
            by_period: dict[str, dict] = {}
            err = None
            try:
                for period in periods:
                    events = list_campaign_events(slug, period, symbol, repo_root)
                    t = len(events)
                    r = sum(1 for e in events if e.npz_present)
                    by_period[period.name] = {
                        "total_events": t,
                        "npz_ready": r,
                        "pct_ready": round(100.0 * r / t, 2) if t else None,
                    }
                    total += t
                    ready += r
            except RuntimeError as ex:
                msg = str(ex)
                if "campaign_blocked" in msg or "blocked" in msg.lower():
                    model_blocked = True
                    err = msg
                else:
                    err = msg
            except Exception as ex:
                err = f"{type(ex).__name__}: {ex}"

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
            if err and model_blocked and slug not in blocked_models:
                blocked_models.append(slug)
            if err and not model_blocked and slug not in error_models:
                error_models[slug] = err

            if not err:
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
    }

    out_path = repo_root / "runtime" / "data_audits" / "all_models_symbols_backtest_ready.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"overall": out["overall"], "worst_5": worst5, "path": str(out_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
