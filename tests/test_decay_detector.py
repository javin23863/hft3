"""ML3 — decay detector routing + enforced submit gate."""
import importlib

import pytest

dd = importlib.import_module("model_metrics.decay_detector")
lc = importlib.import_module("model_metrics.lifecycle")
from model_metrics.schemas import ModelBehaviorEnvelope


def _envelope(**over):
    """A permissive, ACTIVE envelope; tighten via kwargs per test."""
    base = dict(
        envelope_id="env_test", model_id="M", model_version="1",
        robustness_run_id="r", calculation_version="v", created_at="t",
        approved_by_system_rule=True, active=True,
        expected_daily_pnl_range=(-100.0, 100.0), expected_weekly_pnl_range=(-500.0, 500.0),
        expected_intraday_drawdown_range=(-50.0, 0.0),
        warning_drawdown_threshold=-40.0, kill_drawdown_threshold=-80.0,
        expected_drawdown_velocity=(-10.0, 10.0), max_allowed_drawdown_velocity=50.0,
        expected_trade_count_per_session=(1.0, 100.0),
        min_trade_count_per_session=0.0, max_trade_count_per_session=1000.0,
        expected_holding_period_min=1.0, expected_holding_period_median=5.0, expected_holding_period_max=60.0,
        expected_win_rate_range=(0.3, 0.7), expected_payoff_ratio_range=(0.8, 2.0),
        expected_expectancy_range=(-5.0, 20.0),
        expected_slippage_range=(0.0, 2.0), max_allowed_slippage=5.0,
        expected_spread_range=(0.0, 3.0), max_allowed_spread=10.0,
        expected_fill_rate_range=(0.5, 1.0), min_allowed_fill_rate=0.3,
        approved_regime_ids=("normal", "event_shock"),
        blocked_regime_ids=("liquidity_vacuum",),
        feature_training_domain_bounds={"f1": (-1.0, 1.0)},
        low_latency_execution_path_status={"status": "pass"},
    )
    base.update(over)
    return ModelBehaviorEnvelope(**base).to_dict()


# --- pure routing -----------------------------------------------------------
def test_route_regime_only():
    assert dd.route_demotion([{"name": "unapproved_regime"}]) == lc.ROUTE_REGIME_SHIFT
    assert dd.route_demotion([{"name": "blocked_regime"}]) == lc.ROUTE_REGIME_SHIFT


def test_route_edge_gone():
    assert dd.route_demotion([{"name": "feature_training_domain"}]) == lc.ROUTE_EDGE_GONE
    assert dd.route_demotion([{"name": "drawdown_kill_threshold"}]) == lc.ROUTE_EDGE_GONE


def test_route_param_tweak():
    assert dd.route_demotion([{"name": "slippage_drift"}]) == lc.ROUTE_PARAM_TWEAK
    assert dd.route_demotion([{"name": "latency_drift"}]) == lc.ROUTE_PARAM_TWEAK


def test_route_hypothesis_tweak_default():
    assert dd.route_demotion([{"name": "expectancy_decay"}]) == lc.ROUTE_HYPOTHESIS_TWEAK


def test_route_mixed_is_not_regime():
    # regime + execution -> not regime-only -> param
    assert dd.route_demotion([{"name": "unapproved_regime"}, {"name": "slippage_drift"}]) == lc.ROUTE_PARAM_TWEAK


def test_route_none_when_empty():
    assert dd.route_demotion([]) is None


def test_route_infra_only_is_ops_not_param():
    # infra/data faults must NOT route to param re-fit
    assert dd.route_demotion([{"name": "data_freshness"}]) is None
    assert dd.route_demotion([{"name": "async_ack_stale_state"}]) is None


def test_targets():
    assert dd.target_state_for_route(lc.ROUTE_REGIME_SHIFT) == lc.ARCHIVED_PAUSED
    assert dd.target_state_for_route(lc.ROUTE_PARAM_TWEAK) == lc.GAUNTLET
    assert dd.target_state_for_route(lc.ROUTE_HYPOTHESIS_TWEAK) == lc.SCREENING
    assert dd.target_state_for_route(lc.ROUTE_EDGE_GONE) == lc.RETIRED


# --- enforced submit gate ---------------------------------------------------
def test_enforcement_graduated():
    assert dd.enforcement_for_state("GREEN")["size_factor"] == 1.0
    assert dd.enforcement_for_state("YELLOW")["size_factor"] == 0.5
    assert dd.enforcement_for_state("RED")["size_factor"] == 0.0


@pytest.mark.parametrize("state,model,expected", [
    (lc.LIVE, "GREEN", (True, 1.0)),
    (lc.LIVE, "YELLOW", (True, 0.5)),
    (lc.LIVE, "RED", (False, 0.0)),
    (lc.DEGRADED, "YELLOW", (True, 0.5)),
    (lc.QUARANTINED, "GREEN", (False, 0.0)),
    (lc.ARCHIVED_PAUSED, "GREEN", (False, 0.0)),
    (lc.RETIRED, "GREEN", (False, 0.0)),
])
def test_submit_gate(state, model, expected):
    assert dd.submit_allowed(state, model) == expected


# --- evaluate against a real envelope ---------------------------------------
def test_evaluate_green():
    env = _envelope()
    obs = {"model_id": "M", "regime_id": "normal", "slippage_bps": 1.0, "fill_rate": 0.9}
    r = dd.evaluate(env, obs)
    assert r.model_state == "GREEN" and r.demote is False and r.route is None


def test_evaluate_param_via_slippage_drift():
    env = _envelope()
    obs = {"model_id": "M", "regime_id": "normal", "slippage_bps": 3.0}  # >range hi 2, <max 5
    r = dd.evaluate(env, obs)
    assert r.demote is True and r.route == lc.ROUTE_PARAM_TWEAK and r.target_state == lc.GAUNTLET
    assert r.enforcement["size_factor"] == 0.5  # YELLOW


def test_evaluate_edge_gone_via_feature_domain():
    env = _envelope()
    obs = {"model_id": "M", "regime_id": "normal", "feature_values": {"f1": 9.0}}  # outside (-1,1)
    r = dd.evaluate(env, obs)
    assert r.route == lc.ROUTE_EDGE_GONE and r.target_state == lc.RETIRED
    assert r.enforcement["size_factor"] == 0.0  # RED


def test_evaluate_regime_pause():
    env = _envelope()
    obs = {"model_id": "M", "regime_id": "some_unlisted_regime"}  # not approved, not blocked
    r = dd.evaluate(env, obs)
    assert r.route == lc.ROUTE_REGIME_SHIFT and r.target_state == lc.ARCHIVED_PAUSED
