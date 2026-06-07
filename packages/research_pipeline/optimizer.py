"""Adaptive candidate optimization for the autoresearch pipeline."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Iterable, List, Tuple

from workbench.src.core.params import DEFAULT_STRATEGY_PARAMS, param_hash_from_dict

from research_pipeline.types import CandidateModel, EvaluationResult, ParsedHypothesis


_DEFAULT_RANGES = {
    "signal_threshold": (0.05, 0.50),
    "holding_period_bars": (5.0, 60.0),
    "stop_loss_pct": (0.25, 2.0),
    "take_profit_pct": (0.25, 2.5),
}

_FAILED_SCORE = -1_000_000_000.0


@dataclass(frozen=True)
class OptimizerTrace:
    backend: str
    iteration: int
    requested_candidates: int
    emitted_candidates: int
    best_prior_candidate_id: str | None
    best_prior_score: float | None
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "iteration": self.iteration,
            "requested_candidates": self.requested_candidates,
            "emitted_candidates": self.emitted_candidates,
            "best_prior_candidate_id": self.best_prior_candidate_id,
            "best_prior_score": self.best_prior_score,
            "fallback_reason": self.fallback_reason,
        }


def score_result(result: EvaluationResult) -> float:
    """Scalar objective for optimizer ranking.

    Expectancy is the primary objective. Gate-quality metrics are additive
    signals, while drawdown, latency, and hard failures are penalized.
    """
    if result.error:
        return _FAILED_SCORE
    expectancy = _finite_float(result.expectancy)
    net_pnl = _finite_float(result.net_pnl)
    win_rate = _finite_float(result.win_rate)
    if expectancy is None or net_pnl is None or win_rate is None:
        return _FAILED_SCORE
    score = expectancy
    score += 0.001 * net_pnl
    score += 0.10 * win_rate
    sharpe = _finite_float(result.sharpe)
    if sharpe is not None:
        score += 0.25 * sharpe
    drawdown_bps = _finite_float(result.drawdown_bps)
    if drawdown_bps is not None:
        score -= 0.001 * abs(drawdown_bps)
    avg_latency_us = _finite_float(result.avg_latency_us)
    if avg_latency_us is not None:
        score -= 0.0001 * max(0.0, avg_latency_us)
    if result.passes_all_gates():
        score += 1_000.0
    return score if math.isfinite(score) else _FAILED_SCORE


def propose_optimized_candidates(
    parsed: ParsedHypothesis,
    history: Iterable[EvaluationResult],
    *,
    max_candidates: int,
    iteration: int,
    backend: str = "heuristic",
    random_seed: int | None = None,
    top_k: int = 3,
    seed_metadata: dict[str, Any] | None = None,
) -> Tuple[List[CandidateModel], OptimizerTrace]:
    """Propose the next candidate batch from prior evaluation outcomes."""
    history_list = list(history)
    ranked = sorted(history_list, key=score_result, reverse=True)
    best = ranked[0] if ranked else None
    requested = max(1, int(max_candidates))
    seed = None if random_seed is None else int(random_seed) + max(0, int(iteration))
    rng = random.Random(seed)

    if backend == "optuna":
        candidates, fallback = _propose_optuna(
            parsed,
            ranked,
            max_candidates=requested,
            iteration=iteration,
            rng=rng,
            random_seed=random_seed,
            top_k=top_k,
            seed_metadata=seed_metadata,
        )
        actual_backend = "optuna" if fallback is None else "heuristic"
    elif backend == "heuristic":
        candidates = _propose_heuristic(
            parsed,
            ranked,
            max_candidates=requested,
            iteration=iteration,
            rng=rng,
            top_k=top_k,
            seed_metadata=seed_metadata,
        )
        fallback = None
        actual_backend = "heuristic"
    else:
        raise ValueError(f"unsupported optimizer backend: {backend}")

    trace = OptimizerTrace(
        backend=actual_backend,
        iteration=iteration,
        requested_candidates=requested,
        emitted_candidates=len(candidates),
        best_prior_candidate_id=best.candidate.candidate_id if best else None,
        best_prior_score=score_result(best) if best else None,
        fallback_reason=fallback,
    )
    return candidates, trace


def _propose_heuristic(
    parsed: ParsedHypothesis,
    ranked: list[EvaluationResult],
    *,
    max_candidates: int,
    iteration: int,
    rng: random.Random,
    top_k: int,
    seed_metadata: dict[str, Any] | None = None,
) -> List[CandidateModel]:
    anchors = ranked[: max(1, int(top_k))]
    candidates: List[CandidateModel] = []
    seen: set[str] = {r.candidate.candidate_id for r in ranked}
    attempts = 0
    while len(candidates) < max_candidates and attempts < max_candidates * 20:
        attempts += 1
        anchor = anchors[(attempts - 1) % len(anchors)] if anchors else None
        params = _sample_params(parsed, anchor, iteration=iteration, rng=rng)
        candidate = _candidate_from_params(
            parsed,
            params,
            iteration=iteration,
            backend="heuristic",
            anchor=anchor,
            seed_metadata=seed_metadata,
        )
        if candidate.candidate_id in seen:
            continue
        seen.add(candidate.candidate_id)
        candidates.append(candidate)
    return candidates


def _propose_optuna(
    parsed: ParsedHypothesis,
    ranked: list[EvaluationResult],
    *,
    max_candidates: int,
    iteration: int,
    rng: random.Random,
    random_seed: int | None,
    top_k: int,
    seed_metadata: dict[str, Any] | None = None,
) -> tuple[List[CandidateModel], str | None]:
    try:
        import optuna  # type: ignore
        from optuna.distributions import FloatDistribution, IntDistribution  # type: ignore
        from optuna.trial import create_trial  # type: ignore
    except Exception as exc:
        return (
            _propose_heuristic(
                parsed,
                ranked,
                max_candidates=max_candidates,
                iteration=iteration,
                rng=rng,
                top_k=top_k,
                seed_metadata=seed_metadata,
            ),
            f"optuna_unavailable: {exc}",
        )

    ranges = _param_ranges(parsed)
    distributions = {
        "signal_threshold": FloatDistribution(*ranges["signal_threshold"]),
        "holding_period_bars": IntDistribution(
            int(ranges["holding_period_bars"][0]),
            int(ranges["holding_period_bars"][1]),
        ),
        "stop_loss_pct": FloatDistribution(*ranges["stop_loss_pct"]),
        "take_profit_pct": FloatDistribution(*ranges["take_profit_pct"]),
    }
    sampler = optuna.samplers.TPESampler(seed=random_seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)

    for result in ranked:
        params = result.candidate.strategy_params
        if not all(name in params and params[name] is not None for name in distributions):
            continue
        trial_params = {
            "signal_threshold": float(params["signal_threshold"]),
            "holding_period_bars": int(params["holding_period_bars"]),
            "stop_loss_pct": float(params["stop_loss_pct"]),
            "take_profit_pct": float(params["take_profit_pct"]),
        }
        try:
            study.add_trial(
                create_trial(
                    params=trial_params,
                    distributions=distributions,
                    value=score_result(result),
                )
            )
        except Exception:
            continue

    candidates: List[CandidateModel] = []
    seen: set[str] = {r.candidate.candidate_id for r in ranked}
    attempts = 0
    while len(candidates) < max_candidates and attempts < max_candidates * 20:
        attempts += 1
        trial = study.ask(distributions)
        params = {
            "signal_threshold": round(float(trial.params["signal_threshold"]), 4),
            "holding_period_bars": int(trial.params["holding_period_bars"]),
            "stop_loss_pct": round(float(trial.params["stop_loss_pct"]), 4),
            "take_profit_pct": round(float(trial.params["take_profit_pct"]), 4),
        }
        candidate = _candidate_from_params(
            parsed,
            params,
            iteration=iteration,
            backend="optuna",
            anchor=ranked[0] if ranked else None,
            seed_metadata=seed_metadata,
        )
        if candidate.candidate_id in seen:
            continue
        seen.add(candidate.candidate_id)
        candidates.append(candidate)
    return candidates, None


def _sample_params(
    parsed: ParsedHypothesis,
    anchor: EvaluationResult | None,
    *,
    iteration: int,
    rng: random.Random,
) -> dict[str, Any]:
    ranges = _param_ranges(parsed)
    shrink = max(0.08, 0.40 / max(1, int(iteration)))
    params = dict(DEFAULT_STRATEGY_PARAMS)
    for name, (lo, hi) in ranges.items():
        center = _anchor_value(anchor, name)
        if center is None:
            value = rng.uniform(lo, hi)
        else:
            sigma = (hi - lo) * shrink
            value = min(hi, max(lo, rng.gauss(float(center), sigma)))
        if name == "holding_period_bars":
            params[name] = int(round(value))
        else:
            params[name] = round(float(value), 4)
    return params


def _candidate_from_params(
    parsed: ParsedHypothesis,
    params: dict[str, Any],
    *,
    iteration: int,
    backend: str,
    anchor: EvaluationResult | None = None,
    seed_metadata: dict[str, Any] | None = None,
) -> CandidateModel:
    model_id = parsed.primary_model_id
    cid = param_hash_from_dict(model_id, params)
    inherited = {
        **_lineage_metadata(seed_metadata or {}),
        **_inherited_metadata(anchor),
    }
    return CandidateModel(
        candidate_id=cid,
        model_id=model_id,
        strategy_params=params,
        thesis=parsed.thesis,
        metadata={
            **inherited,
            "source_model": parsed.primary_model_id,
            "strategy_family": model_id,
            "optimizer_backend": backend,
            "optimizer_iteration": iteration,
            "optimized": True,
        },
    )


def _inherited_metadata(anchor: EvaluationResult | None) -> dict[str, Any]:
    if anchor is None:
        return {}
    inherited = _lineage_metadata(anchor.candidate.metadata)
    if anchor.candidate.candidate_id:
        inherited["optimizer_anchor_candidate_id"] = anchor.candidate.candidate_id
    return inherited


def _lineage_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    inherited: dict[str, Any] = {}
    for key in ("idea_id", "idea_status", "idea_lane_code", "idea_queue_index"):
        if key in metadata:
            inherited[key] = metadata[key]
    return inherited


def _finite_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _param_ranges(parsed: ParsedHypothesis) -> dict[str, tuple[float, float]]:
    ranges: dict[str, tuple[float, float]] = {}
    for name, default in _DEFAULT_RANGES.items():
        raw = parsed.param_ranges.get(name)
        if raw and len(raw) >= 2:
            lo, hi = float(raw[0]), float(raw[1])
        else:
            lo, hi = default
        if hi < lo:
            lo, hi = hi, lo
        if hi == lo:
            hi = lo + 1e-9
        ranges[name] = (lo, hi)
    return ranges


def _anchor_value(anchor: EvaluationResult | None, name: str) -> Any:
    if anchor is None:
        return None
    value = anchor.candidate.strategy_params.get(name)
    if value is None and name in DEFAULT_STRATEGY_PARAMS:
        value = DEFAULT_STRATEGY_PARAMS[name]
    return value
