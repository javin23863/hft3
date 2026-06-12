"""ML2 — certify hook: freeze a model's edge-envelope at promotion.

When a candidate PASSES the promotion gate, this mints the immutable behavior
envelope it will be monitored against for the rest of its life, and advances it
to CERTIFIED in the lifecycle registry. Envelope population REUSES
``envelope.generate_behavior_envelope`` verbatim — this module only builds the
``ModelScorecard`` + ``inputs`` it expects from a ``PromotedCandidate`` plus the
Stage A/B robustness + regime evidence the orchestrator passes in.

``approved_regime_ids`` (regimes where Stage A/B expectancy was positive) vs
``blocked_regime_ids`` (negative) are what later let the decay detector separate
"regime-dormant → pause" from "edge-gone → retire".
"""
from __future__ import annotations

from typing import Any, Optional, Sequence

from model_metrics.envelope import generate_behavior_envelope
from model_metrics.schemas import (
    CALCULATION_VERSION,
    MetricValue,
    ModelBehaviorEnvelope,
    ModelScorecard,
)

from . import lifecycle


class CertifyError(Exception):
    """Raised when a candidate is not eligible for certification."""


# scorecard metric names that generate_behavior_envelope reads (envelope.py::_metric)
_ENVELOPE_METRICS = (
    "max_drawdown", "drawdown_velocity", "latency_order_to_ack", "alpha_half_life",
    "hit_rate", "average_win_loss_ratio", "expectancy_per_trade", "fill_rate",
)


def _metric(name: str, value: Optional[float], n: int) -> MetricValue:
    return MetricValue(
        metric_name=name,
        metric_value=value,
        metric_unit="native",
        metric_window="oos",
        sample_size=n,
        calculation_version=CALCULATION_VERSION,
    )


def scorecard_from_candidate(candidate: Any, *, grade: str = "A", metrics: Optional[dict] = None) -> ModelScorecard:
    """Build a ModelScorecard carrying the metrics the envelope generator reads."""
    vb = getattr(candidate, "vectorbt_results", {}) or {}
    dd = getattr(candidate, "drawdown_metrics", {}) or {}
    n = int(vb.get("num_trades", 0) or 0)
    src = {
        "max_drawdown": dd.get("max_drawdown", vb.get("max_drawdown_pct")),
        "drawdown_velocity": dd.get("drawdown_velocity"),
        "latency_order_to_ack": vb.get("latency_order_to_ack"),
        "alpha_half_life": vb.get("alpha_half_life"),
        "hit_rate": vb.get("win_rate", vb.get("hit_rate")),
        "average_win_loss_ratio": vb.get("payoff_ratio", vb.get("average_win_loss_ratio")),
        "expectancy_per_trade": vb.get("oos_expectancy", vb.get("expectancy")),
        "fill_rate": vb.get("fill_rate"),
    }
    if metrics:
        src.update(metrics)
    metric_tuple = tuple(_metric(name, src.get(name), n) for name in _ENVELOPE_METRICS)
    return ModelScorecard(
        scorecard_id=f"sc_{candidate.candidate_id}",
        model_id=str(candidate.hypothesis_id),
        model_version=getattr(candidate, "git_commit", "") or "0",
        run_id=getattr(candidate, "vectorbt_run_id", ""),
        campaign_id="",
        asset_class=getattr(candidate, "asset_class", ""),
        symbol=getattr(candidate, "symbol", ""),
        robustness_run_id=getattr(candidate, "vectorbt_run_id", ""),
        calculation_version=CALCULATION_VERSION,
        created_at=getattr(candidate, "timestamp_utc", "") or "1970-01-01T00:00:00+00:00",
        grade=grade,
        weighted_score=1.0,
        category_scores=(),
        metrics=metric_tuple,
    )


def build_inputs(
    candidate: Any,
    *,
    approved_regime_ids: Sequence[str] = (),
    blocked_regime_ids: Sequence[str] = (),
    feature_bounds: Optional[dict] = None,
    returns: Optional[list] = None,
    trades: Optional[list] = None,
    latency_envelope: Optional[dict] = None,
) -> dict:
    return {
        "context": {
            "asset_class": getattr(candidate, "asset_class", ""),
            "symbol": getattr(candidate, "symbol", ""),
            "strategy_family": getattr(candidate, "strategy_family", ""),
        },
        "returns": returns or [],
        "trades": trades or [],
        "feature_training_domain_bounds": feature_bounds or {},
        "approved_regime_ids": tuple(approved_regime_ids),
        "blocked_regime_ids": tuple(blocked_regime_ids),
        "latency_operating_envelope": latency_envelope or {},
    }


def _light_snapshot(env: ModelBehaviorEnvelope) -> dict:
    return {
        "envelope_id": env.envelope_id,
        "expected_expectancy_range": env.expected_expectancy_range,
        "expected_win_rate_range": env.expected_win_rate_range,
        "warning_drawdown_threshold": env.warning_drawdown_threshold,
        "kill_drawdown_threshold": env.kill_drawdown_threshold,
        "max_allowed_slippage": env.max_allowed_slippage,
        "approved_regime_ids": list(env.approved_regime_ids),
        "blocked_regime_ids": list(env.blocked_regime_ids),
        "alpha_half_life_bounds": env.alpha_half_life_bounds,
    }


def _ensure_in_gauntlet(lifecycle_id: str, candidate: Any, actor: str) -> None:
    """Fast-forward a model to GAUNTLET via legal edges (no-op if already there)."""
    rec = lifecycle.get_record(lifecycle_id)
    if rec is None:
        lifecycle.apply_transition(
            lifecycle_id, lifecycle.CANDIDATE, trigger="register", reason="certify auto-register",
            actor=actor, create=True,
            initial={"hypothesis_id": _int_or_none(getattr(candidate, "hypothesis_id", None)),
                     "symbol": getattr(candidate, "symbol", ""),
                     "strategy_family": getattr(candidate, "strategy_family", "")},
        )
        rec = lifecycle.get_record(lifecycle_id)
    path = {lifecycle.CANDIDATE: lifecycle.SCREENING, lifecycle.SCREENING: lifecycle.GAUNTLET}
    while rec.current_state in path:
        nxt = path[rec.current_state]
        lifecycle.apply_transition(lifecycle_id, nxt, trigger="advance", reason="certify path", actor=actor)
        rec = lifecycle.get_record(lifecycle_id)


def _int_or_none(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def certify_and_snapshot(
    candidate: Any,
    *,
    lifecycle_id: Optional[str] = None,
    approved_regime_ids: Sequence[str] = (),
    blocked_regime_ids: Sequence[str] = (),
    feature_bounds: Optional[dict] = None,
    returns: Optional[list] = None,
    trades: Optional[list] = None,
    latency_envelope: Optional[dict] = None,
    scorecard_metrics: Optional[dict] = None,
    links: Optional[dict] = None,
    actor: str = "certifier",
    validate_gate: bool = True,
    cell: Optional[dict] = None,
) -> tuple[ModelBehaviorEnvelope, "lifecycle.LifecycleRecord"]:
    """Mint + freeze the envelope and transition the model to CERTIFIED.

    Raises CertifyError if the candidate does not pass PromotionGate.evaluate
    (set validate_gate=False only for explicitly pre-validated callers/tests).
    """
    lid = lifecycle_id or str(getattr(candidate, "candidate_id", "") or candidate.hypothesis_id)

    # Do not trust the caller: a model is only certifiable if it actually passes
    # the promotion gate. Mint nothing for a failing candidate.
    if validate_gate:
        try:
            from backtest_pipeline.src.promotion_gate import PromotionGate

            if not PromotionGate().evaluate(candidate):
                raise CertifyError(f"candidate {lid} does not pass PromotionGate.evaluate")
        except CertifyError:
            raise
        except Exception as exc:  # gate import/shape error => fail-closed
            raise CertifyError(f"cannot validate promotion gate for {lid}: {exc}")

    scorecard = scorecard_from_candidate(candidate, metrics=scorecard_metrics)
    inputs = build_inputs(
        candidate,
        approved_regime_ids=approved_regime_ids,
        blocked_regime_ids=blocked_regime_ids,
        feature_bounds=feature_bounds,
        returns=returns,
        trades=trades,
        latency_envelope=latency_envelope,
    )
    env = generate_behavior_envelope(inputs, scorecard)
    snapshot = env.to_dict()
    lifecycle.save_envelope_snapshot(env.envelope_id, snapshot)

    _ensure_in_gauntlet(lid, candidate, actor)
    updates = {"certified_edge_envelope_snapshot": _light_snapshot(env)}
    if cell:
        # cell metadata = the certified (hyp_id, event_type, band) that the
        # param/edge routes need to build a runnable re-screen command.
        updates["cell"] = cell
    rec = lifecycle.apply_transition(
        lid, lifecycle.CERTIFIED,
        trigger="promotion_pass", reason=getattr(candidate, "pass_reason", "promoted"),
        actor=actor, envelope_id=env.envelope_id,
        record_updates=updates,
        links=links or {"candidate_id": getattr(candidate, "candidate_id", None)},
    )
    return env, rec
