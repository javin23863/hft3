"""All seven crypto candidates complete smoke without error."""
from __future__ import annotations

from crypto_lane.src.ml.walk_forward_runner import run_all_smokes


def test_all_candidate_smokes_complete():
    reports = run_all_smokes()
    assert len(reports) == 7
    for report in reports:
        assert report["candidate_id"]
        assert report["target"]
        assert report.get("smoke_mode") is True
        assert "with_btc_node" in report["runs"] or "without_btc_node" in report["runs"]
        primary = report["runs"].get("with_btc_node") or report["runs"].get("without_btc_node")
        assert primary is not None
        min_folds = 2 if report.get("hypothesis_id") == "CRYPTO_H7" else 3
        assert primary.get("n_folds", 0) >= min_folds
        assert primary.get("min_folds_met") is True
        assert report.get("purged_cv_implemented") is True
        assert report["pass_fail"] == "pass"
        assert report.get("rejection_reason") is None
        assert report["holdout_gate"]["status"] == "PASS"
        nc = report["negative_controls"]
        assert nc.get("controls_skipped_low_signal") is False
        assert nc.get("shuffled_degraded") is True
        assert nc.get("shifted_degraded") is True
        if report.get("hypothesis_id") == "CRYPTO_H7":
            assert nc.get("randomized_degraded") is True
        assert abs(primary.get("oos_ic_baseline_mean", 0.0)) < 0.995
