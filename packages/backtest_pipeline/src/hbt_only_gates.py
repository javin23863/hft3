"""Post-HBT evaluation gates for the HftBacktest-only lane.

Implements the plan's Gate 3 (microstructure realism sensitivity battery)
and Gate 4 (multi-event robustness) so ``write_promotion_decision`` can stop
hardcoding ``promotion_allowed: False`` and instead grant promotion only when
all four gates pass honestly.

Gate 3 re-runs the same registry strategy across fill-model / latency / fee
scenarios and fails closed unless every scenario is mechanically valid and
the closed-trade realized PnL survives the most conservative assumptions.

Gate 4 consumes per-event run stats (plus an optional parameter-surface
performance matrix) and applies the repo's existing statistical rigor
producers: PSR/DSR from ``research_pipeline.statistics`` and CSCV/PBO from
``research_pipeline.cross_validation``. With fewer than ``min_events`` events
or no surface matrix it fails closed with reasons that name exactly what
evidence is missing — that receipt is the data-coverage shopping list.
"""

from __future__ import annotations

import dataclasses
import math
import statistics as _stdlib_stats
from pathlib import Path
from typing import Any, Mapping, Sequence

from backtest_pipeline.src.fee_model import FeeModel

GATE3_SCHEMA = "hft3_hbt_only_gate3_sensitivity_v1"
GATE4_SCHEMA = "hft3_hbt_only_gate4_robustness_v1"

DEFAULT_MIN_EVENTS = 4
DEFAULT_MIN_PSR = 0.95
DEFAULT_MIN_DSR = 0.90
DEFAULT_MAX_PBO = 0.20
LATENCY_MULTIPLIERS = (0.5, 1.0, 2.0)


def default_sensitivity_scenarios(config: Any) -> list[dict[str, Any]]:
    """Cross fill-model x latency x fee scenarios for Gate 3.

    The base configuration itself (declared fill model, 1.0x latency,
    declared fees) is excluded — its stats come from the base run.
    """
    product = str(config.symbol).split(".")[0].upper()
    conservative_fee_bump = FeeModel(product=product).get_fee_per_contract()
    scenarios: list[dict[str, Any]] = []
    for fill_model in ("NoPartialFillExchange", "PartialFillExchange"):
        for latency_mult in LATENCY_MULTIPLIERS:
            for fee_label, fee_bump in (("declared", 0.0), ("conservative", conservative_fee_bump)):
                if (
                    fill_model == config.exchange_fill_model
                    and latency_mult == 1.0
                    and fee_label == "declared"
                ):
                    continue
                scenarios.append(
                    {
                        "scenario_id": f"{fill_model}__lat{latency_mult:g}x__fee_{fee_label}",
                        "exchange_fill_model": fill_model,
                        "latency_multiplier": latency_mult,
                        "fee_label": fee_label,
                        "maker_fee": float(config.maker_fee) + fee_bump,
                        "taker_fee": float(config.taker_fee) + fee_bump,
                    }
                )
    return scenarios


def run_sensitivity_battery(
    config: Any,
    *,
    out_dir: Path,
    base_stats: Mapping[str, Any],
    scenarios: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Gate 3: re-run the strategy across realism scenarios; fail closed.

    Pass criteria: every scenario mechanically valid AND the minimum
    closed-trade realized PnL across base + scenarios stays positive.
    """
    from backtest_pipeline.src.hftbacktest_only_pipeline import (
        _run_minimal_strategy,
        _stats_summary,
        _write_json,
        _write_recorder_result,
    )

    out_dir = Path(out_dir)
    scenario_list = list(scenarios) if scenarios is not None else default_sensitivity_scenarios(config)
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []

    base_realized = base_stats.get("realized_closed_trade_pnl")
    if base_stats.get("mechanical_validity_status") != "pass":
        reasons.append("base_run_not_mechanically_valid")
    if not isinstance(base_realized, (int, float)):
        reasons.append("base_run_missing_realized_closed_trade_pnl")
    rows.append(
        {
            "scenario_id": "base",
            "exchange_fill_model": config.exchange_fill_model,
            "latency_multiplier": 1.0,
            "fee_label": "declared",
            "mechanical_validity_status": base_stats.get("mechanical_validity_status"),
            "realized_closed_trade_pnl": base_realized,
            "fills_count": base_stats.get("fills_count"),
            "fill_rate": base_stats.get("fill_rate"),
            "fail_closed_reasons": list(base_stats.get("fail_closed_reasons") or []),
        }
    )

    for scenario in scenario_list:
        scenario_id = str(scenario["scenario_id"])
        scenario_cfg = dataclasses.replace(
            config,
            run_id=f"{config.run_id}__{scenario_id}",
            exchange_fill_model=str(scenario["exchange_fill_model"]),
            entry_latency_ns=int(config.entry_latency_ns * float(scenario["latency_multiplier"])),
            response_latency_ns=int(config.response_latency_ns * float(scenario["latency_multiplier"])),
            maker_fee=float(scenario["maker_fee"]),
            taker_fee=float(scenario["taker_fee"]),
        )
        scenario_dir = out_dir / "gate3_scenarios" / scenario_id
        scenario_dir.mkdir(parents=True, exist_ok=True)
        replay, replay_reasons = _run_minimal_strategy(scenario_cfg)
        _write_json(scenario_dir / "official_replay.json", replay)
        recorder_path = _write_recorder_result(scenario_dir / "recorder_result.npz", replay)
        stats = _stats_summary(scenario_cfg, replay, recorder_path)
        _write_json(scenario_dir / "stats_summary.json", stats)
        row = {
            "scenario_id": scenario_id,
            "exchange_fill_model": scenario["exchange_fill_model"],
            "latency_multiplier": scenario["latency_multiplier"],
            "fee_label": scenario["fee_label"],
            "maker_fee": scenario["maker_fee"],
            "taker_fee": scenario["taker_fee"],
            "mechanical_validity_status": stats.get("mechanical_validity_status"),
            "realized_closed_trade_pnl": stats.get("realized_closed_trade_pnl"),
            "fills_count": stats.get("fills_count"),
            "fill_rate": stats.get("fill_rate"),
            "fail_closed_reasons": list(replay_reasons),
        }
        rows.append(row)
        if replay_reasons:
            reasons.append(f"scenario_failed:{scenario_id}:{replay_reasons[0]}")
        elif stats.get("mechanical_validity_status") != "pass":
            reasons.append(f"scenario_not_mechanically_valid:{scenario_id}")

    realized_values = [
        row["realized_closed_trade_pnl"]
        for row in rows
        if isinstance(row.get("realized_closed_trade_pnl"), (int, float))
    ]
    if len(realized_values) != len(rows):
        reasons.append("scenario_realized_pnl_missing")
    min_realized = min(realized_values) if realized_values else None
    if isinstance(min_realized, (int, float)) and min_realized <= 0.0 and not any(
        r.startswith("scenario_failed") or r.startswith("base_run") for r in reasons
    ):
        reasons.append(f"realized_pnl_not_robust_to_realism:min={min_realized}")

    reasons = list(dict.fromkeys(reasons))
    report = {
        "schema_version": GATE3_SCHEMA,
        "run_id": config.run_id,
        "status": "pass" if not reasons else "fail",
        "scenario_count": len(rows),
        "min_realized_closed_trade_pnl": min_realized,
        "scenarios": rows,
        "fail_closed_reasons": reasons,
    }
    _write_json(out_dir / "gate3_sensitivity.json", report)
    return report


def run_robustness_gate(
    event_stats: Sequence[Mapping[str, Any]],
    *,
    out_dir: Path,
    min_events: int = DEFAULT_MIN_EVENTS,
    min_psr: float = DEFAULT_MIN_PSR,
    min_dsr: float = DEFAULT_MIN_DSR,
    max_pbo: float = DEFAULT_MAX_PBO,
    n_trials: int = 1,
    performance_matrix: Sequence[Sequence[float]] | None = None,
) -> dict[str, Any]:
    """Gate 4: multi-event robustness via PSR/DSR and CSCV/PBO; fail closed.

    ``event_stats`` rows must carry event_id + realized_closed_trade_pnl from
    per-event HBT runs, ordered chronologically. ``performance_matrix`` is the
    parameter-surface matrix (rows = chronological events, columns = parameter
    variants) required for CSCV/PBO — without it the gate fails closed with
    ``cscv_matrix_missing`` rather than skipping the overfitting check.
    """
    from backtest_pipeline.src.hftbacktest_only_pipeline import _write_json
    from research_pipeline.cross_validation import combinatorially_symmetric_cv
    from research_pipeline.statistics import deflated_sharpe_ratio, probabilistic_sharpe_ratio

    out_dir = Path(out_dir)
    reasons: list[str] = []
    rows = [dict(row) for row in event_stats]
    realized = [
        float(row["realized_closed_trade_pnl"])
        for row in rows
        if isinstance(row.get("realized_closed_trade_pnl"), (int, float))
    ]
    if len(realized) != len(rows):
        reasons.append("event_realized_pnl_missing")
    if len(rows) < min_events:
        reasons.append(f"insufficient_events:{len(rows)}<{min_events}")

    sharpe = psr = dsr = None
    if len(realized) >= 2:
        mean = _stdlib_stats.fmean(realized)
        stdev = _stdlib_stats.stdev(realized)
        if stdev > 0.0 and math.isfinite(stdev):
            sharpe = mean / stdev
            psr = probabilistic_sharpe_ratio(sharpe, 0.0, len(realized))
            dsr = deflated_sharpe_ratio(
                sharpe, benchmark_sharpe=0.0, n_obs=len(realized), n_trials=max(1, int(n_trials))
            )
        else:
            reasons.append("event_pnl_dispersion_degenerate")
    if psr is not None and psr < min_psr:
        reasons.append(f"psr_below_min:{psr:.4f}<{min_psr}")
    if dsr is not None and dsr < min_dsr:
        reasons.append(f"dsr_below_min:{dsr:.4f}<{min_dsr}")

    cscv: dict[str, Any] = {}
    if performance_matrix is None:
        reasons.append("cscv_matrix_missing")
    else:
        cscv = dict(combinatorially_symmetric_cv(performance_matrix))
        pbo = cscv.get("pbo")
        if not isinstance(pbo, (int, float)):
            reasons.append(f"pbo_not_computed:{cscv.get('reason')}")
        elif pbo > max_pbo:
            reasons.append(f"pbo_above_max:{pbo:.4f}>{max_pbo}")

    reasons = list(dict.fromkeys(reasons))
    report = {
        "schema_version": GATE4_SCHEMA,
        "status": "pass" if not reasons else "fail",
        "event_count": len(rows),
        "min_events": min_events,
        "event_ids": [row.get("event_id") for row in rows],
        "per_event_realized_closed_trade_pnl": realized,
        "sharpe": sharpe,
        "psr": psr,
        "min_psr": min_psr,
        "dsr": dsr,
        "min_dsr": min_dsr,
        "n_trials": n_trials,
        "cscv": cscv,
        "max_pbo": max_pbo,
        "fail_closed_reasons": reasons,
    }
    _write_json(out_dir / "robustness_report.json", report)
    return report
