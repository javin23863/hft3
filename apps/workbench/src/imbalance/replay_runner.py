"""Real imbalance ablation via repeated replay with family toggles."""

from __future__ import annotations

import random
from typing import Any, Dict, List, Tuple

from backtest_pipeline.src.replay_matrix import run_hypothesis_replay
from features_engine.src.hypotheses.modules import BaseHypothesis
from features_engine.src.imbalance.ablation import (
    AblationRunResult,
    ImbalanceAblationMode,
    all_ablation_modes,
    best_ablation_verdict,
    decide_promotion,
)
from features_engine.src.imbalance.apply import wrap_hypothesis_for_ablation
from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS
from workbench.src.run.run_context import RunContext
from workbench.src.registry.unified_registry import get_model_by_id


def _modes_for_sweep(fast_sweep: bool, *, ablation_full: bool = False) -> List[ImbalanceAblationMode]:
    all_modes = all_ablation_modes()
    if ablation_full or not fast_sweep:
        return all_modes
    ids = {"baseline", "book_only", "auction_only", "all_three"}
    return [m for m in all_modes if m.mode_id in ids]


def run_imbalance_ablation_replays(
    ctx: RunContext,
    *,
    fast_sweep: bool = True,
    ablation_full: bool = False,
    latency_ok: bool = True,
    robustness_ok: bool = True,
    wfc_ok: bool = True,
    walk_forward_correlation: float = 0.0,
) -> Tuple[List[AblationRunResult], List[dict], Dict[str, Any]]:
    model = get_model_by_id(ctx.model_id)
    from workbench.src.adapters.hypothesis_adapter import HypothesisAdapter

    if not isinstance(model, HypothesisAdapter):
        modes = _modes_for_sweep(fast_sweep, ablation_full=ablation_full)
        baseline = 0.0
        results = [
            AblationRunResult(
                mode_id=m.mode_id,
                baseline_metric=baseline,
                treatment_metric=baseline,
                incremental_contribution=0.0,
                decision="quarantine",
                labeling={"note": "non-hypothesis model; ablation skipped"},
            )
            for m in modes
        ]
        return results, [], {"verdict": "quarantine", "best_mode_id": None, "skipped": True}

    from features_engine.src.imbalance.auction_events import load_auction_events

    hypothesis = model._effective_hypothesis(ctx)
    npz_path = str(ctx.metadata.get("npz_path") or ctx.npz_path)
    event_id = ctx.event_id
    symbol = str(ctx.metadata.get("symbol") or "MES")
    auction_events = load_auction_events(ctx.repo_root, event_id, symbol)

    params = ctx.metadata.get("strategy_params") or {}
    threshold = float(params.get("signal_threshold", DEFAULT_STRATEGY_PARAMS["signal_threshold"]))
    latency_ms = ctx.latency_policy.total_ms_for_backtest(random.Random(ctx.seed))

    modes = _modes_for_sweep(fast_sweep, ablation_full=ablation_full)
    baseline_pnl = 0.0
    results: List[AblationRunResult] = []
    treatment_by_mode: Dict[str, float] = {}
    samples_by_mode: Dict[str, List[dict]] = {}
    all_samples: List[dict] = []

    for mode in modes:
        meta: Dict[str, Any] = {}
        wrapped = wrap_hypothesis_for_ablation(hypothesis, mode)
        replay_result = run_hypothesis_replay(
            wrapped,
            npz_path,
            latency_ms=latency_ms,
            signal_threshold=threshold,
            imbalance_ablation_mode_id=mode.mode_id,
            auction_events=auction_events,
            event_window_id=event_id,
            meta_out=meta,
        )
        pnl = float(replay_result.net_pnl)
        treatment_by_mode[mode.mode_id] = pnl
        mode_samples = list(meta.get("imbalance_samples") or [])
        samples_by_mode[mode.mode_id] = mode_samples
        if mode.mode_id == "baseline":
            baseline_pnl = pnl
        if mode.mode_id == "all_three":
            all_samples = mode_samples

    if not all_samples and samples_by_mode:
        all_samples = samples_by_mode.get("all_three") or next(iter(samples_by_mode.values()), [])

    for mode in modes:
        pnl = treatment_by_mode[mode.mode_id]
        inc = pnl - baseline_pnl
        results.append(
            AblationRunResult(
                mode_id=mode.mode_id,
                baseline_metric=baseline_pnl,
                treatment_metric=pnl,
                incremental_contribution=inc,
                decision=decide_promotion(
                    inc,
                    latency_ok=latency_ok,
                    robustness_ok=robustness_ok,
                    wfc_ok=wfc_ok,
                ),
                labeling={"active_families": [f.value for f in mode.active_families]},
                robustness_passed=robustness_ok,
                walk_forward_passed=wfc_ok,
                walk_forward_correlation=walk_forward_correlation,
            )
        )

    verdict, best_mode = best_ablation_verdict(results)
    meta_out = {
        "verdict": verdict,
        "best_mode_id": best_mode,
        "treatment_by_mode": treatment_by_mode,
        "modes_run": [m.mode_id for m in modes],
        "samples_by_mode": {k: len(v) for k, v in samples_by_mode.items()},
        "auction_events_loaded": len(auction_events),
    }
    return results, all_samples, meta_out
