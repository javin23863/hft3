from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))
if str(_REPO / "packages") not in sys.path:
    sys.path.insert(0, str(_REPO / "packages"))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from research_pipeline.cost_model import CostModel, apply_costs
from research_pipeline.cost_model import bid_ask_spread_cost, commission_cost, market_impact_cost, slippage_cost
from research_pipeline.cross_validation import (
    combinatorial_symmetric_cross_validation,
    combinatorially_symmetric_cv,
    expanding_windows,
    rolling_window_validation,
    rolling_windows,
)
from research_pipeline.power_analysis import (
    achieved_power,
    compute_effect_size,
    detectable_effect_size,
    minimum_sample_size,
    required_sample_size,
)
from research_pipeline.regime import RegimeThresholds, group_performance, label_regimes
from research_pipeline.statistics import (
    adjusted_p_value,
    deflated_sharpe_ratio,
    minimum_track_record_length,
    p_value_correction,
    probabilistic_sharpe_ratio,
)


def test_psr_dsr_and_min_track_record_behave_monotonically() -> None:
    weak = probabilistic_sharpe_ratio(0.5, 0.0, 30)
    strong = probabilistic_sharpe_ratio(1.0, 0.0, 30)
    assert strong > weak

    single_trial = deflated_sharpe_ratio(1.0, 30, 1)
    many_trials = deflated_sharpe_ratio(1.0, 30, 100)
    assert many_trials < single_trial

    assert minimum_track_record_length(1.0, benchmark_sharpe=0.0) < minimum_track_record_length(
        0.5,
        benchmark_sharpe=0.0,
    )
    assert minimum_track_record_length(1.0, 0.0, 0.05, 0.0, 3.0, 0.8) > minimum_track_record_length(
        1.0,
        benchmark_sharpe=0.0,
    )
    assert minimum_track_record_length(0.0, benchmark_sharpe=0.1) is None


def test_p_value_corrections_match_known_small_vector() -> None:
    pvals = [0.01, 0.04, 0.03]
    assert p_value_correction(pvals, "bonferroni") == pytest.approx([0.03, 0.12, 0.09])
    assert p_value_correction(pvals, "holm") == pytest.approx([0.03, 0.06, 0.06])
    assert p_value_correction(pvals, "bh") == pytest.approx([0.03, 0.04, 0.04])
    assert adjusted_p_value(0.01, 3, method="holm") == pytest.approx(0.03)


def test_power_sample_size_effect_size_and_power_are_consistent() -> None:
    n = required_sample_size(0.5, alpha=0.05, power=0.8)
    assert n == 32
    assert minimum_sample_size(0.5, 0.05, 0.8) == n
    assert compute_effect_size(1.2, 0.2) == pytest.approx(1.0)
    assert detectable_effect_size(n, alpha=0.05, power=0.8) <= 0.5
    assert achieved_power(0.5, n, alpha=0.05) >= 0.8


def test_cost_model_breaks_out_components_and_apply_costs() -> None:
    model = CostModel(spread_bps=2.0, commission_per_unit=1.0, slippage_bps=1.0, impact_bps=1.0)
    cost = model.estimate(quantity=10, price=100.0)
    assert cost.spread == pytest.approx(0.10)
    assert cost.commission == pytest.approx(10.0)
    assert cost.slippage == pytest.approx(0.10)
    assert cost.impact == pytest.approx(0.10)
    assert cost.total == pytest.approx(10.30)
    assert bid_ask_spread_cost(99.0, 101.0, quantity=2.0) == pytest.approx(2.0)
    assert commission_cost(10.0, 1.25) == pytest.approx(12.5)
    assert slippage_cost(100.0, quantity=2.0, slippage_bps=5.0) == pytest.approx(0.1)
    assert market_impact_cost(100.0, quantity=2.0, participation_rate=0.25, coefficient=0.01) == pytest.approx(1.0)
    assert apply_costs([20.0], quantities=10, prices=100.0, model=model) == pytest.approx([9.70])

    net, breakdown = apply_costs(
        [20.0],
        config={"quantity": 10, "price": 100.0, "spread_bps": 2.0, "commission_per_unit": 1.0},
        market_data={},
    )
    assert net == pytest.approx([9.90])
    assert breakdown["total"] == pytest.approx(10.10)


def test_cscv_reports_low_pbo_for_dominant_config_and_loss_for_bad_winner() -> None:
    dominant = [
        [10.0, 0.0, -1.0],
        [10.0, 0.0, -1.0],
        [10.0, 0.0, -1.0],
        [10.0, 0.0, -1.0],
    ]
    result = combinatorially_symmetric_cv(dominant)
    alias_result = combinatorial_symmetric_cross_validation(dominant)
    assert result["pbo"] == pytest.approx(0.0)
    assert alias_result["pbo"] == pytest.approx(0.0)
    assert result["logits"]
    assert result["probability_of_loss"] == pytest.approx(0.0)

    overfit = [
        [10.0, 1.0],
        [10.0, 1.0],
        [-10.0, 1.0],
        [-10.0, 1.0],
    ]
    overfit_result = combinatorially_symmetric_cv(overfit)
    assert overfit_result["pbo"] > 0.0
    assert overfit_result["performance_degradation"] > 0.0


def test_rolling_and_expanding_windows_are_chronological_with_embargo() -> None:
    assert rolling_windows(10, train_size=4, test_size=2, embargo=1) == [
        (0, 4, 5, 7),
        (2, 6, 7, 9),
    ]
    assert rolling_window_validation(10, train_size=4, test_size=2, embargo=1) == [
        (0, 4, 5, 7),
        (2, 6, 7, 9),
    ]
    assert expanding_windows(10, initial_train_size=4, test_size=2, embargo=1) == [
        (0, 4, 5, 7),
        (0, 6, 7, 9),
    ]


def test_regime_labeling_and_group_performance() -> None:
    regimes = label_regimes([1.0, 2.0, 3.0], RegimeThresholds(low=1.5, high=2.5))
    assert regimes == ["low", "normal", "high"]
    grouped = group_performance([1.0, -2.0, 3.0], regimes)
    assert grouped["low"]["mean"] == pytest.approx(1.0)
    assert grouped["normal"]["loss_rate"] == pytest.approx(1.0)
    assert grouped["high"]["win_rate"] == pytest.approx(1.0)
