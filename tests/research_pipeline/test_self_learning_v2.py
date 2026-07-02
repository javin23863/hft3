from __future__ import annotations

from research_pipeline.generation_summary import _composite_score, _hbt_gate_aware_score
from research_pipeline.parameter_search import hbt_parameter_set_from_candidate
from research_pipeline.types import CandidateModel


def _candidate(params: dict) -> CandidateModel:
    return CandidateModel(
        candidate_id="cand_1",
        model_id="SECOND_WAVE_CONTINUATION",
        strategy_params=params,
        thesis="test",
        metadata={},
    )


def test_envelope_defaults_exit_at_holding_on() -> None:
    spec = hbt_parameter_set_from_candidate(
        _candidate({"signal_threshold": 0.2, "holding_period_bars": 15})
    )
    assert spec["strategy_params"]["exit_at_holding"] is True
    # Proposal dims untouched.
    assert spec["strategy_params"]["signal_threshold"] == 0.2


def test_envelope_respects_explicit_exit_at_holding_off() -> None:
    spec = hbt_parameter_set_from_candidate(
        _candidate({"signal_threshold": 0.2, "exit_at_holding": False})
    )
    assert spec["strategy_params"]["exit_at_holding"] is False


def test_gate_aware_score_absent_without_hbt_results() -> None:
    row = {"metrics": {"oos_expectancy": 1.5}}
    assert _hbt_gate_aware_score(row) is None
    # Legacy composite still applies.
    assert _composite_score(row) == 1.5


def test_gate_aware_score_uses_realized_pnl_with_psr_dsr_multipliers() -> None:
    row = {
        "metrics": {"oos_expectancy": 99.0},  # must be outranked by HBT evidence
        "hbt_results": {
            "realized_closed_trade_pnl_mean": 10.0,
            "gate3_status": "pass",
            "gate4_status": "pass",
            "psr": 0.96,
            "dsr": 0.92,
        },
    }
    score = _composite_score(row)
    assert abs(score - 10.0 * 0.96 * 0.92) < 1e-9


def test_gate_fail_zeroes_score() -> None:
    row = {
        "metrics": {"oos_expectancy": 99.0},
        "hbt_results": {
            "realized_closed_trade_pnl_mean": 10.0,
            "gate3_status": "fail",
            "gate4_status": "pass",
        },
    }
    assert _composite_score(row) == 0.0


def test_negative_realized_pnl_not_rescued_by_multipliers() -> None:
    row = {
        "hbt_results": {
            "realized_closed_trade_pnl_mean": -4.0,
            "gate3_status": "pass",
            "gate4_status": "pass",
            "psr": 0.5,
        },
    }
    # Multipliers only shrink positive scores; a loss stays a full loss.
    assert _composite_score(row) == -4.0
