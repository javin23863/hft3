"""Validation-honesty helpers in the ML walk-forward runner."""
from __future__ import annotations

import pytest

from crypto_lane.src.ml.walk_forward_runner import (
    CRYPTO_EXECUTION_ACK_STATUS,
    benjamini_hochberg,
    deflated_sharpe_cdf,
    deflated_sharpe_ratio,  # backward-compat alias
    run_all_smokes,
)


def test_deflated_sharpe_cdf_is_one_for_huge_observed():
    assert deflated_sharpe_cdf(10.0, 5, 1000) > 0.99


def test_deflated_sharpe_cdf_is_zero_for_zero_observed_with_many_trials():
    assert deflated_sharpe_cdf(0.0, 100, 1000) < 0.01


def test_deflated_sharpe_cdf_handles_single_trial():
    assert deflated_sharpe_cdf(2.0, 1, 100) > 0.9


def test_deflated_sharpe_cdf_is_in_unit_interval():
    for sharpe, trials, n in [(-1.0, 1, 50), (0.0, 1, 50), (1.0, 10, 200), (3.0, 100, 1000)]:
        v = deflated_sharpe_cdf(sharpe, trials, n)
        assert 0.0 <= v <= 1.0, f"out of [0,1] for sharpe={sharpe}, trials={trials}, n={n}: {v}"


def test_deflated_sharpe_cdf_zero_trials_returns_zero():
    # Degenerate input must not crash and must not return a fake-positive 0.5.
    assert deflated_sharpe_cdf(5.0, 0, 200) == 0.0


def test_deflated_sharpe_ratio_alias_points_to_cdf():
    # Backward-compat alias.
    assert deflated_sharpe_ratio is deflated_sharpe_cdf


def test_benjamini_hochberg_no_false_positives():
    assert benjamini_hochberg([0.5, 0.6, 0.7, 0.8]) == [False, False, False, False]


def test_benjamini_hochberg_detects_clear_signal():
    assert benjamini_hochberg([0.001, 0.002, 0.5, 0.6]) == [True, True, False, False]


def test_benjamini_hochberg_empty():
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_all_below_threshold():
    # n=3, alpha=0.05: threshold at k=3 is 0.05; all 0.04 -> all rejected.
    assert benjamini_hochberg([0.04, 0.04, 0.04]) == [True, True, True]


def test_benjamini_hochberg_all_above_threshold():
    # n=3, alpha=0.05: threshold at k=3 is 0.05; all 0.06 -> none rejected.
    assert benjamini_hochberg([0.06, 0.06, 0.06]) == [False, False, False]


def test_crypto_execution_ack_status_constant_mentions_gap():
    assert "INSUFFICIENT" in CRYPTO_EXECUTION_ACK_STATUS
    assert "crypto venue" in CRYPTO_EXECUTION_ACK_STATUS
    assert "Bitcoin node" in CRYPTO_EXECUTION_ACK_STATUS


def test_smoke_report_includes_n_trials_and_crypto_execution_status():
    reports = run_all_smokes()
    assert reports, "expected at least one candidate report"
    for report in reports:
        assert "n_trials" in report
        assert isinstance(report["n_trials"], int)
        assert report["n_trials"] >= 1
        assert "deflated_sharpe_ratio" in report
        assert isinstance(report["deflated_sharpe_ratio"], float)
        assert 0.0 <= report["deflated_sharpe_ratio"] <= 1.0
        assert "execution_ack_status" in report
        assert report["execution_ack_status"] == CRYPTO_EXECUTION_ACK_STATUS
        assert report["execution_ack_scope"] == "crypto_venue_submit_ack"
        assert report["execution_ack_measured"] is False
        proxy = report["research_pnl_proxy"]
        assert proxy["promotion_gate"] is False
        assert proxy["scope"] == "purged_walk_forward_oos_diagnostic"
        assert proxy["leakage_controls"]["train_rows_only_for_fit"] is True
        assert proxy["leakage_controls"]["train_predictions_only_for_threshold"] is True
        assert proxy["leakage_controls"]["test_rows_only_for_reported_pnl"] is True
        assert proxy["summary"]["return_proxy_label"] == "event_window_return"
        assert proxy["summary"]["min_trades_for_sharpe"] == 20
        assert proxy["summary"]["sharpe_status"] in {"OBSERVED", "INSUFFICIENT_TRADES"}
        assert "chi404_order_ack_status" not in report
        assert "bh_rejected" in report
        assert isinstance(report["bh_rejected"], bool)
