"""Per-session equities + options experiment runner.

For each decadal session:
1. Load equity NDJSON (MBO L3).
2. Load options NDJSON (OPRA cbbo-1m) if present.
3. Run point-in-time checks (reject if contaminated).
4. Compute features (equity + option) for the decision timestamp.
5. Compute stock / option / combined expected value.
6. Run route comparison.
7. Emit a per-session record with route decision, lineage, leakage status.

Data isolation: writes only to ``research_cards/equities/``. Never writes to
``data/npz/`` or CME production paths.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _find_repo_root() -> Path:
    """Find repository root by locating .git directory or pyproject.toml."""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / ".git").exists() or (parent / "pyproject.toml").exists():
            return parent
    # Fallback: assume standard layout (packages/equities_lane/src/experiments/ -> 4 levels up)
    return p.parents[4]


_REPO = _find_repo_root()
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

import yaml

from equities_lane.src.config_loader import load_universe
from equities_lane.src.features.book_adapter import compute_features
from equities_lane.src.integrity.pit_filter import (
    check_equity_ticks,
    check_option_quotes,
    check_option_contracts,
    check_float_metadata,
)
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.ontology.float_metadata import parse_float_pit_csv
from equities_lane.src.ontology.payoff import (
    ROUTE_NO_TRADE,
    ROUTE_OPTION_ONLY,
    ROUTE_STOCK_AND_OPTION,
    ROUTE_STOCK_ONLY,
)
from equities_lane.src.ontology.session_context import EquitySessionContext
from equities_lane.src.options.chain_loader import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    IV_STATUS_SUCCESS,
    OptionsChainLoader,
)
from equities_lane.src.route.comparator import RouteInputs, compare_routes

_CONFIG = _REPO / "packages" / "equities_lane" / "config" / "universe.yaml"
_DECADAL = _REPO / "packages" / "equities_lane" / "config" / "decadal_runners.yaml"
_FLOAT_CSV = _REPO / "data" / "equities" / "metadata" / "float_pit.csv"
_REPORTS_ROOT = _REPO / "research_cards" / "equities"


def _load_sessions() -> list[dict]:
    raw = yaml.safe_load(_DECADAL.read_text(encoding="utf-8")) or {}
    return [s for s in raw.get("sessions", []) if not s.get("skip_pull")]


def _options_path_for_session(session_id: str) -> Path | None:
    p = _REPO / "data" / "options" / "equity_chains" / "normalized" / f"{session_id}.ndjson"
    return p if p.exists() else None


def _session_id_for(symbol: str, date: str) -> str:
    return f"{symbol.lower()}_{date.replace('-', '_')}"


def _compute_decision_features(
    ticks: list,
    options_loader: OptionsChainLoader | None,
    decision_ts_ns: int,
) -> tuple[dict[str, float], dict[str, Any], dict[str, Any]]:
    """Compute features at decision timestamp using the real feature pipeline.
    
    Samples up to 1000 ticks near the decision timestamp for efficiency.
    In production, features would be computed incrementally; here we use
    a representative sample for research experiments.
    
    Returns (equity_features, option_features, raw_snapshot) where raw_snapshot
    contains the full FeatureSnapshot data for debugging.
    """
    from equities_lane.src.types import FeatureToggles, DegradedModeFlags
    
    # Sample ticks near decision timestamp for efficiency
    # Use up to 1000 ticks from the last 10% of the pre-decision period
    sample_size = min(1000, len(ticks))
    if len(ticks) > sample_size:
        # Take ticks from the last portion before decision
        start_idx = max(0, len(ticks) - sample_size)
        sampled_ticks = ticks[start_idx:]
    else:
        sampled_ticks = ticks
    
    # Use default feature toggles (all enabled)
    toggles = FeatureToggles(
        ofi=True,
        vpin=True,
        hawkes=True,
        hmm=True,
        l3_queue=True,
        l3_cancellation=True,
        l3_iceberg=True,
    )
    degraded = DegradedModeFlags(degraded_mode=False, assumptions=[])
    
    # Run the feature pipeline on sampled ticks
    snapshots = compute_features(sampled_ticks, toggles, degraded, options_loader)
    
    if not snapshots:
        # No ticks, return zeros
        return (
            {"ofi_zscore": 0.0, "mlofi_pc1": 0.0, "vpin_value": 0.0, "vpin_percentile": 0.0, "hawkes_score": 0.0, "hmm_markup_prob": 0.0},
            {"iv_atm": 0.0, "gex_net": 0.0, "dex_net": 0.0, "iv_skew_25d": 0.0},
            {}
        )
    
    # Use the last snapshot as the decision-time features
    decision_snap = snapshots[-1]
    
    # Extract equity features
    equity_features = {
        "ofi_zscore": decision_snap.ofi_zscore,
        "mlofi_pc1": decision_snap.mlofi_pc1,
        "vpin_value": decision_snap.vpin_value,
        "vpin_percentile": decision_snap.vpin_percentile,
        "hawkes_score": decision_snap.hawkes_score,
        "hmm_markup_prob": decision_snap.hmm_markup_prob,
    }
    
    # Extract option features from the snapshot
    option_features = {
        "iv_atm": decision_snap.options.get("iv_atm", 0.0),
        "gex_net": decision_snap.options.get("gex_net", 0.0),
        "dex_net": decision_snap.options.get("dex_net", 0.0),
        "iv_skew_25d": decision_snap.options.get("iv_skew_25d", 0.0),
    }
    
    raw_snapshot = decision_snap.to_dict()
    
    return equity_features, option_features, raw_snapshot


def _estimate_stock_ev(
    equity_features: dict[str, float],
    liquidity_score: float,
) -> tuple[float, float, float, float]:
    """Estimate stock EV using feature-based model.
    
    EV is computed as a linear combination of features:
    - OFI z-score: directional pressure (positive = buy pressure)
    - VPIN: toxicity (high = adverse selection risk)
    - Hawkes: cascade probability (high = momentum continuation)
    - HMM markup: regime-based markup probability
    
    Coefficients are calibrated placeholders per PLAN_EQOPT §4.2;
    production wiring will replace with trained model weights.
    
    Returns (ev, spread, slippage, fill_probability).
    """
    ofi = equity_features.get("ofi_zscore", 0.0)
    vpin = equity_features.get("vpin_value", 0.0)
    hawkes = equity_features.get("hawkes_score", 0.0)
    hmm_markup = equity_features.get("hmm_markup_prob", 0.0)
    
    # Base EV from liquidity (market making profit)
    base_ev = 10.0 * liquidity_score
    
    # Feature adjustments
    # OFI: positive pressure increases EV (directional edge)
    ofi_adj = 2.0 * max(0.0, ofi)  # Only positive OFI helps
    # VPIN: high toxicity reduces EV (adverse selection)
    vpin_adj = -5.0 * vpin
    # Hawkes: high cascade probability increases EV (momentum)
    hawkes_adj = 3.0 * min(1.0, hawkes)
    # HMM: high markup probability increases EV
    hmm_adj = 1.5 * hmm_markup
    
    stock_ev = base_ev + ofi_adj + vpin_adj + hawkes_adj + hmm_adj
    stock_ev = max(0.0, stock_ev)  # Floor at 0
    
    # Spread and slippage from liquidity
    spread = 0.05 / max(0.1, liquidity_score)  # Lower liquidity = wider spread
    slippage = 0.05 / max(0.1, liquidity_score)
    
    # Fill probability from liquidity and features
    base_fill = 0.5 + 0.4 * liquidity_score
    # High VPIN reduces fill probability (toxic flow)
    vpin_fill_adj = -0.2 * vpin
    fill_prob = max(0.0, min(1.0, base_fill + vpin_fill_adj))
    
    return stock_ev, spread, slippage, fill_prob


def _tick_spot(tick: Any) -> float:
    bid = float(getattr(tick, "bid_px", 0.0) or 0.0)
    ask = float(getattr(tick, "ask_px", 0.0) or 0.0)
    trade = float(getattr(tick, "trade_px", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    return trade if trade > 0 else 0.0


def _spot_at_decision(ticks: list[Any], fallback: float) -> float:
    for tick in reversed(ticks):
        spot = _tick_spot(tick)
        if spot > 0:
            return spot
    return fallback


def _option_route_eligibility(
    snap,
    *,
    avg_spread: float,
    fill_probability: float,
    spot: float,
) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if snap.real_quote_count <= 0:
        reasons.append("no_real_executable_option_quotes")
    if snap.quote_age_ns > 120 * 1_000_000_000:
        reasons.append("option_quote_stale")
    if not snap.real_nbbo_size_available:
        reasons.append("option_nbbo_size_missing")
    if not snap.contract_listing_metadata_available:
        reasons.append("option_contract_listing_metadata_missing")
    elif snap.valid_contract_count <= 0:
        reasons.append("no_valid_option_contract_at_decision")
    if snap.iv_atm_status != IV_STATUS_SUCCESS:
        reasons.append(f"iv_status_{snap.iv_atm_status.lower()}")
    if snap.iv_confidence not in {CONFIDENCE_HIGH, CONFIDENCE_MEDIUM}:
        reasons.append(f"iv_confidence_{snap.iv_confidence.lower()}")
    if avg_spread <= 0:
        reasons.append("missing_option_spread")
    elif spot > 0 and avg_spread > max(0.25, spot * 0.05):
        reasons.append("option_spread_too_wide")
    if fill_probability < 0.4:
        reasons.append("option_fill_probability_below_40pct")
    return not reasons, tuple(reasons)


def _estimate_option_ev(
    option_features: dict[str, Any],
    spot: float,
    decision_ts_ns: int,
    options_loader: OptionsChainLoader | None,
) -> tuple[float, float, float, float, float, float, float, tuple[str, ...], tuple[str, ...], bool, tuple[str, ...], dict[str, Any]]:
    """Estimate option EV using feature-based model.
    
    EV is computed from option features:
    - IV ATM: implied volatility (higher = more premium)
    - GEX: gamma exposure (positive = dealer long gamma, negative = short)
    - DEX: delta exposure (directional bias)
    - IV skew: 25-delta put IV - call IV (risk reversal)
    
    Coefficients are calibrated placeholders per PLAN_EQOPT §4.2;
    production wiring will replace with trained model weights.
    
    Returns EV/cost fields plus route eligibility and diagnostics. Raw option EV is
    retained even when the option route is ineligible; the comparator gates route
    selection separately.
    """
    if options_loader is None or spot <= 0:
        return 0.0, 0.10, 0.10, 0.0, 0.0, 0.0, 0.0, (), ("iv_atm", "gex_net", "dex_net"), False, ("no_options_loader",), {}

    # Get the option chain snapshot for contract selection
    from datetime import datetime, timezone
    decision_date = datetime.fromtimestamp(decision_ts_ns / 1e9, tz=timezone.utc).date()
    snap = options_loader.to_snapshot(decision_ts_ns, spot, decision_date=decision_date)
    if snap.num_quotes == 0:
        return 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, (), ("iv_atm", "gex_net", "dex_net"), False, ("no_fresh_option_quotes",), snap.to_dict()

    iv_atm = snap.iv_atm
    gex_net = snap.gex_net
    dex_net = snap.dex_net
    iv_skew = snap.iv_skew_25d
    
    # Average spread from the chain
    avg_spread = (sum(q.spread for q in snap.quotes) / len(snap.quotes)) if snap.quotes else 0.0
    
    # Fill probability from coverage
    fill_prob = min(0.9, max(0.0, snap.coverage * 0.9))

    option_route_eligible, option_block_reasons = _option_route_eligibility(
        snap,
        avg_spread=avg_spread,
        fill_probability=fill_prob,
        spot=spot,
    )

    if iv_atm <= 0:
        return (
            0.0,
            avg_spread,
            avg_spread * 0.5,
            fill_prob,
            0.0,
            0.0,
            0.0,
            (),
            ("iv_atm", "gex_net", "dex_net"),
            option_route_eligible,
            option_block_reasons,
            snap.to_dict(),
        )
    
    # Option EV from features
    # Base EV from IV (higher vol = more premium)
    base_ev = iv_atm * spot * 0.1  # 10% of notional vol
    
    # GEX adjustment: positive GEX = dealer long gamma = more stable prices
    gex_adj = 0.0001 * gex_net  # Scale down from $ to EV units
    # DEX adjustment: directional exposure
    dex_adj = 0.0001 * abs(dex_net)
    # Skew adjustment: risk reversal premium
    skew_adj = 2.0 * iv_skew
    
    option_ev = base_ev + gex_adj + dex_adj + skew_adj - avg_spread
    option_ev = max(0.0, option_ev)  # Floor at 0
    
    contracts = tuple(
        f"{snap.underlying} {q.expiry} {q.strike} {q.right}"
        for q in snap.quotes[:10]
    ) if option_route_eligible else ()
    
    features_used = ("iv_atm", "gex_net", "dex_net", "iv_skew_25d")
    
    return (
        option_ev,
        avg_spread,
        avg_spread * 0.5,
        fill_prob,
        gex_net / 1e6 if gex_net else 0.0,
        dex_net / 1e6 if dex_net else 0.0,
        iv_skew,
        contracts,
        features_used,
        option_route_eligible,
        option_block_reasons,
        snap.to_dict(),
    )


def _decision_ts_for_session(date_str: str) -> int:
    """Decision timestamp = 14:30 ET on session date (post-ORB)."""
    from datetime import datetime, timezone
    dt = datetime.fromisoformat(f"{date_str}T14:30:00+00:00")
    return int(dt.timestamp() * 1e9)


def run_session(
    session: dict,
    universe,
) -> dict:
    """Run the full experiment for one decadal session. Returns a session record."""
    sym = session["symbol"]
    date = session["date"]
    norm_path = _REPO / "data" / "equities" / "normalized" / f"{sym}_{date}.ndjson"
    npz_path = _REPO / "data" / "equities" / "npz" / f"{sym}_{date}.npz"
    record: dict[str, Any] = {
        "underlying_symbol": sym,
        "session_date": date,
        "decision_timestamp_ns": _decision_ts_for_session(date),
        "equity_data_source": "Databento XNAS.ITCH mbo" if not norm_path.name.startswith("fixture") else "fixture",
        "option_data_source": "Databento OPRA.PILLAR cbbo-1m",
        "equity_schema_used": "mbo",
        "option_schema_used": "cbbo-1m",
        "equity_features_used": (),
        "option_features_used": (),
        "selected_option_contracts": (),
        "leakage_status": "CLEAN",
        "rejection_reason": None,
        "final_route_decision": ROUTE_NO_TRADE,
        "payoff": None,
        "ontology_claim_ids": [],
        "pdf_citations": [],
        "reason_codes": [],
        "option_route_eligible": False,
        "option_route_block_reasons": [],
        "option_diagnostics": {},
    }
    if not norm_path.exists():
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = f"equity_normalized_missing: {norm_path}"
        return record

    try:
        ctx = EquitySessionContext(
            underlying_symbol=sym,
            session_date=date,
            decision_timestamp_ns=record["decision_timestamp_ns"],
            equity_data_source=record["equity_data_source"],
            equity_schema_used=record["equity_schema_used"],
            equity_npz_path=str(npz_path),
            equity_normalized_path=str(norm_path),
            float_metadata_path=str(_FLOAT_CSV),
            catalog_yaml_path=str(_DECADAL),
            l3_only=True,
        )
        ctx.validate()
    except ValueError as e:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = f"context validation: {e}"
        return record

    meta, ticks = load_session(str(norm_path))
    if meta.degraded.degraded_mode:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = (
            f"L3-only violation: degraded_mode=true assumptions={meta.degraded.assumptions}"
        )
        return record

    # Filter equity ticks to only include those at or before decision timestamp
    # (point-in-time: no future data available at decision time)
    decision_ts = record["decision_timestamp_ns"]
    ticks = [t for t in ticks if t.ts_ns <= decision_ts]

    equity_pit = check_equity_ticks(ticks, decision_ts)
    if not equity_pit.is_pit_clean:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = equity_pit.rejection_reason
        return record

    float_entry = parse_float_pit_csv(str(_FLOAT_CSV), sym, date)
    if float_entry is None:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = f"float metadata missing for {sym} as of {date}"
        return record
    float_pit = check_float_metadata(float_entry.as_of_date, date)
    if not float_pit.is_pit_clean:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = float_pit.rejection_reason
        return record

    options_path = _options_path_for_session(session["id"])
    options_loader: OptionsChainLoader | None = None
    option_quotes_for_pit: list[dict] = []
    option_contracts_for_pit: list[dict] = []
    if options_path is not None:
        try:
            options_loader = OptionsChainLoader(options_path, underlying=sym)
        except Exception as e:
            record["leakage_status"] = "REJECTED"
            record["rejection_reason"] = f"options loader failure: {e}"
            return record
        seen_contracts: dict[str, int] = {}
        # Filter option quotes to only include those at or before decision timestamp
        # (point-in-time: no future data available at decision time)
        for q in options_loader._bars.values():
            for opt in q:
                if opt.ts_ns <= decision_ts:
                    option_quotes_for_pit.append({
                        "quote_ts_ns": opt.ts_ns,
                        "symbol": f"{sym}   {opt.expiry.replace('-', '')[2:]}{opt.right}{int(opt.strike*1000):08d}",
                    })
                    contract_sym = f"{sym}   {opt.expiry.replace('-', '')[2:]}{opt.right}{int(opt.strike*1000):08d}"
                    if contract_sym not in seen_contracts:
                        seen_contracts[contract_sym] = opt.ts_ns
                    else:
                        seen_contracts[contract_sym] = min(seen_contracts[contract_sym], opt.ts_ns)
        for contract_sym, listed_ts in seen_contracts.items():
            option_contracts_for_pit.append({
                "contract_symbol": contract_sym,
                "listed_at_ts_ns": listed_ts,
            })
    option_pit = check_option_quotes(option_quotes_for_pit, record["decision_timestamp_ns"])
    if not option_pit.is_pit_clean:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = option_pit.rejection_reason
        return record
    contract_pit = check_option_contracts(option_contracts_for_pit, record["decision_timestamp_ns"])
    if not contract_pit.is_pit_clean:
        record["leakage_status"] = "REJECTED"
        record["rejection_reason"] = contract_pit.rejection_reason
        return record

    fallback_spot = meta.premarket_open or meta.prior_close or 0.0
    spot = _spot_at_decision(ticks, fallback_spot)
    record["decision_spot"] = spot
    
    # Compute features using the real feature pipeline
    equity_features, option_features, raw_snapshot = _compute_decision_features(
        ticks, options_loader, record["decision_timestamp_ns"]
    )
    record["equity_features_used"] = tuple(equity_features.keys())
    record["option_features_used"] = tuple(option_features.keys())
    
    # Compute liquidity score from file size (placeholder until we have real liquidity metrics)
    liquidity_score = min(1.0, norm_path.stat().st_size / 1e8)
    
    # Estimate EV using feature-based models
    stock_ev, spread_s, slip_s, fill_s = _estimate_stock_ev(equity_features, liquidity_score)
    opt_ev, spread_o, slip_o, fill_o, gamma, delta, conv, contracts, opt_used, opt_eligible, opt_block_reasons, opt_diagnostics = _estimate_option_ev(
        option_features, spot, record["decision_timestamp_ns"], options_loader
    )
    record["selected_option_contracts"] = list(contracts)
    record["option_route_eligible"] = opt_eligible
    record["option_route_block_reasons"] = list(opt_block_reasons)
    record["option_diagnostics"] = opt_diagnostics

    inputs = RouteInputs(
        underlying_symbol=sym,
        session_date=date,
        decision_timestamp_ns=record["decision_timestamp_ns"],
        stock_expected_value=stock_ev,
        option_expected_value=opt_ev,
        expected_slippage_stock=slip_s,
        expected_slippage_option=slip_o,
        spread_cost_stock=spread_s,
        spread_cost_option=spread_o,
        fill_probability_stock=fill_s,
        fill_probability_option=fill_o,
        latency_assumption_stock_us=5000.0,
        latency_assumption_option_us=8000.0,
        max_loss_stock=100.0,
        max_loss_option=50.0,
        convexity_exposure=conv,
        gamma_exposure=gamma,
        delta_exposure=delta,
        theta_decay_window_seconds=3600.0,
        liquidity_score_stock=liquidity_score,
        liquidity_score_option=options_loader.num_bars / 100.0 if options_loader else 0.0,
        borrow_shortability_constraint="long_only",
        selected_option_contracts=contracts,
        equity_features_used=tuple(equity_features.keys()),
        option_features_used=opt_used,
        option_route_eligible=opt_eligible,
        option_route_block_reasons=opt_block_reasons,
    )
    decision = compare_routes(inputs)
    decision.validate()
    record["final_route_decision"] = decision.final_route_decision
    record["payoff"] = decision.payoff.to_dict()
    record["ontology_claim_ids"] = list(decision.ontology_claim_ids)
    record["pdf_citations"] = list(decision.pdf_citations)
    record["reason_codes"] = list(decision.reason_codes)
    return record


def run_all() -> dict:
    """Run the experiment for all decadal sessions and write a session_bundle report."""
    _, universe, _ = load_universe(str(_CONFIG))
    sessions = _load_sessions()
    per_session: list[dict] = []
    for s in sessions:
        try:
            r = run_session(s, universe)
        except Exception as e:
            r = {
                "underlying_symbol": s.get("symbol"),
                "session_date": s.get("date"),
                "leakage_status": "REJECTED",
                "rejection_reason": f"runner exception: {type(e).__name__}: {e}",
                "final_route_decision": ROUTE_NO_TRADE,
            }
        per_session.append(r)

    counts: dict[str, int] = {}
    for r in per_session:
        counts[r["final_route_decision"]] = counts.get(r["final_route_decision"], 0) + 1
    rejected = sum(1 for r in per_session if r.get("leakage_status") == "REJECTED")

    out = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "config": str(_CONFIG),
        "decadal_config": str(_DECADAL),
        "totals": {
            "sessions_total": len(sessions),
            "rejected": rejected,
            "route_distribution": counts,
        },
        "per_session": per_session,
        "notes": [
            "L3-only enforcement throughout; allow_degraded only in CI fixture path.",
            "Point-in-time filtration enforced for equity ticks, option quotes, option contracts, and float metadata.",
            "Route comparison: stock / option / stock+option / no-trade with cost, slippage, spread, fill probability, and risk inputs.",
            "Ontology citations: StockOptionRouteDecision.ontology_claim_ids must be non-empty for clean runs.",
        ],
    }

    _REPORTS_ROOT.mkdir(parents=True, exist_ok=True)
    bundle_path = _REPORTS_ROOT / f"session_bundle_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    bundle_path.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    latest = _REPORTS_ROOT / "session_bundle_latest.json"
    latest.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")

    return out


if __name__ == "__main__":
    out = run_all()
    print(json.dumps(out["totals"], indent=2))
