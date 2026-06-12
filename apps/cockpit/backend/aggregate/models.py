"""Models zone — the 50-hypothesis tracker grid + promotion funnel.

Each row merges the registry (id/name/family tag/structural-dead flag) with the
Stage A cells (trade counts + expectancy, aggregated across event types). The
structurally-dead set is surfaced as a first-class "silent-zero" signal — the
anti-VIX/anti-prop-dead-zero guard, made visible rather than hidden.

The registry is imported lazily; if the features-engine import graph is
unavailable (missing scipy etc.) we fall back to a vendored snapshot so the
dashboard never hard-depends on the trading core at request time.
"""
from __future__ import annotations

from typing import Optional

from .. import loaders, paths, schemas

# --- Vendored fallback snapshot of registry.py (kept in sync manually) ------
_FAMILIES_FALLBACK = {
    1: "Second-wave continuation", 2: "Stop-run exhaustion fade",
    3: "Liquidity vacuum continuation", 4: "Depth-refill imbalance",
    5: "Spread blowout/recompression", 6: "Aggressor deceleration fade",
    7: "Forced liquidation cascade", 8: "False breakout trap",
    9: "Cancel storm before move", 10: "Queue depletion trigger",
    11: "Book slope collapse", 12: "Absorption fade", 13: "Iceberg/reload detection",
    14: "Liquidity defense break", 15: "One-sided add/cancel imbalance",
    16: "ES -> MES lead-lag", 17: "NQ -> MNQ lead-lag", 18: "ES/NQ divergence snapback",
    19: "ZN/ZB -> ES/NQ macro impulse", 20: "Micro contract retail lag",
    21: "Round-number stop sweep", 22: "Prior high/low breakout trap",
    23: "Opening candle chase", 24: "VWAP defense/break", 25: "DOM illusion trap",
    26: "Late candle entry fade", 27: "Stop-loss cascade continuation",
    28: "Panic market-order spread tax", 29: "End-of-day forced flatten flow",
    30: "Cutoff panic exits", 31: "No-overnight inventory squeeze",
    32: "Daily loss-limit defense", 33: "Trailing drawdown pressure",
    34: "Profit-lock behavior", 35: "Max-contract crowding in micros",
    36: "Prop reset/reopen window", 37: "Friday/weekend de-risking",
    38: "Economic-event restriction flattening", 39: "Quote pull before volatility",
    40: "Re-quote race after shock", 41: "Thin-book continuation",
    42: "Passive trap fill", 43: "Rebate trap avoidance", 44: "Spread regime change",
    45: "Ghost Route MBO queue-decay", 46: "VIX spike event fade",
    47: "VIX quote-pull liquidity vacuum", 48: "VIX implied-realized vol gap",
    49: "VIX depth imbalance direction", 50: "VIX level-conditioned continuation",
}
_PROP_DEAD_FALLBACK = frozenset({20, 30, 32, 35, 36, 38})
_CROSS_ASSET_FALLBACK = frozenset({16, 17, 18, 19, 20})
_VIX_FALLBACK = frozenset({46, 47, 48, 49, 50})


def _registry() -> tuple[dict, frozenset, frozenset, frozenset]:
    try:
        from features_engine.src.hypotheses.registry import (  # type: ignore
            CROSS_ASSET_HYP_IDS, PROP_STRUCTURALLY_DEAD_HYP_IDS, VIX_HYP_IDS,
            HypothesisRegistry,
        )
        fam = HypothesisRegistry().families
        return fam, PROP_STRUCTURALLY_DEAD_HYP_IDS, CROSS_ASSET_HYP_IDS, VIX_HYP_IDS
    except Exception:
        return _FAMILIES_FALLBACK, _PROP_DEAD_FALLBACK, _CROSS_ASSET_FALLBACK, _VIX_FALLBACK


def _family_tag(hid: int, cross: frozenset, vix: frozenset) -> str:
    if hid in vix:
        return "vix"
    if hid in cross:
        return "cross_asset"
    if 29 <= hid <= 38 or hid == 20:
        return "prop"
    return "core"


def _aggregate_cells() -> dict[int, dict]:
    """hyp_id -> {total_trades, sum_expectancy_weighted, n_event_types, ...}."""
    agg: dict[int, dict] = {}
    for cell in loaders.stage_a_cells_compact():
        hid = cell.get("hypothesis_id")
        if hid is None:
            continue
        rec = agg.setdefault(hid, {"total_trades": 0, "n_event_types": 0,
                                   "exp_sum": 0.0, "exp_n": 0, "worst_tail": None})
        trades = cell.get("total_trades", 0) or 0
        rec["total_trades"] += trades
        rec["n_event_types"] += 1
        exp = cell.get("mean_expectancy_usd")
        if isinstance(exp, (int, float)):
            rec["exp_sum"] += exp
            rec["exp_n"] += 1
        tail = cell.get("p5_expectancy_tail_usd")
        if isinstance(tail, (int, float)):
            rec["worst_tail"] = tail if rec["worst_tail"] is None else min(rec["worst_tail"], tail)
    return agg


def build() -> dict:
    families, prop_dead, cross, vix = _registry()
    cells = _aggregate_cells()

    rows = []
    for hid in sorted(families):
        agg = cells.get(hid, {})
        trades = agg.get("total_trades", 0)
        exp_n = agg.get("exp_n", 0)
        mean_exp: Optional[float] = (agg["exp_sum"] / exp_n) if exp_n else None
        is_dead = hid in prop_dead
        if is_dead:
            status = "structurally_dead"
        elif trades == 0 and agg:
            status = "no_trades"
        elif mean_exp is not None and mean_exp > 0 and trades > 0:
            status = "positive"
        elif agg:
            status = "negative"
        else:
            status = "untested"
        rows.append({
            "id": hid,
            "name": families[hid],
            "family": _family_tag(hid, cross, vix),
            "structurally_dead": is_dead,
            "status": status,
            "total_trades": trades,
            "n_event_types": agg.get("n_event_types", 0),
            "mean_expectancy_usd": round(mean_exp, 4) if mean_exp is not None else None,
            "worst_event_tail_usd": agg.get("worst_tail"),
        })

    # Promotion funnel
    survivors = paths.read_json(paths.STAGE_A_SURVIVORS)
    n_survivors = len(survivors) if isinstance(survivors, list) else (
        len(survivors.get("survivors", [])) if isinstance(survivors, dict) else 0)
    stage_a_raw = loaders.stage_a_raw()
    n_screened = len({c.get("hypothesis_id") for c in (stage_a_raw.get("cells", []) if isinstance(stage_a_raw, dict) else [])})

    silent_zero = [{"id": hid, "name": families[hid]} for hid in sorted(prop_dead)]

    return {
        "zone": "models",
        "generated_utc": paths.now_iso(),
        "health": schemas.AMBER if silent_zero else schemas.GREEN,
        "registry_total": len(families),
        "funnel": {
            "registry": len(families),
            "screened_stage_a": n_screened,
            "survivors_stage_a": n_survivors,
            "structurally_dead": len(prop_dead),
        },
        "silent_zero": {
            "count": len(silent_zero),
            "hypotheses": silent_zero,
            "note": "no feature producer / no context / hardcoded 0 — never alive, not tested-and-rejected. "
                    "Source of truth: scripts/audit_prop_slots.py",
        },
        "rows": rows,
    }
