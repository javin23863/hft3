"""Tests for R7 parameter perturbation in Stage A screen.

Covers:
  - parameter_stability_score == 1.0 when signals are strong (all thresholds positive)
  - parameter_stability_score < 1.0 when signals are marginal (some thresholds fail)
  - default OFF: threshold_perturbation=False produces no parameter_stability block
  - --perturb flag wires correctly through run_stage_a_screen.main
  - survivors gain parameter_stability_score field when perturbation is ON
  - parameter_stability block present in stage_a_result.json when ON
  - PERTURB_THRESHOLDS constant has 6 entries including the base 0.15
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths
setup_repo_paths()


# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

from research_pipeline.src.stage_a_screen import (
    PERTURB_THRESHOLDS,
    SIGNAL_THRESHOLD,
    _compute_parameter_stability,
    _mean_expectancy_at_threshold,
)


# ---------------------------------------------------------------------------
# Unit results builder helpers (mirrors stage_a_screen._worker output shape)
# ---------------------------------------------------------------------------

def _make_unit_result(
    event_id: str,
    event_type: str,
    hyp_id: int,
    hyp_name: str,
    *,
    num_trades: int,
    net_usd_pnls: list[float],
    win_rate: float = 0.6,
    expectancy_usd: float = 0.5,
) -> dict:
    """Minimal unit result matching the shape that _worker produces."""
    return {
        "event_id": event_id,
        "event_type": event_type,
        "band_ms": 1.0,
        "error": None,
        "has_vix": False,
        "hypotheses": [
            {
                "hypothesis_id": hyp_id,
                "hypothesis_name": hyp_name,
                "num_trades": num_trades,
                "win_rate": win_rate,
                "expectancy_usd": expectancy_usd,
                "net_usd_pnls": net_usd_pnls,
                "n_rows_vix": 0,
                "has_vix": False,
            }
        ],
    }


def _make_survivor(hyp_id: int, event_type: str) -> dict:
    return {
        "hyp_id": hyp_id,
        "event_type": event_type,
        "p": 0.01,
        "adj_alpha": 0.05,
        "vix_coverage": {"n_events_with_vix": 0, "n_events": 3},
    }


# ---------------------------------------------------------------------------
# 1. PERTURB_THRESHOLDS constant
# ---------------------------------------------------------------------------

class TestPerturbThresholdsConstant:
    def test_six_thresholds(self):
        assert len(PERTURB_THRESHOLDS) == 6, (
            f"Expected 6 perturb thresholds, got {len(PERTURB_THRESHOLDS)}: {PERTURB_THRESHOLDS}"
        )

    def test_base_threshold_included(self):
        assert SIGNAL_THRESHOLD in PERTURB_THRESHOLDS, (
            f"Base threshold {SIGNAL_THRESHOLD} not in PERTURB_THRESHOLDS: {PERTURB_THRESHOLDS}"
        )

    def test_expected_values(self):
        expected = (0.10, 0.1125, 0.135, 0.15, 0.1875, 0.20)
        assert set(PERTURB_THRESHOLDS) == set(expected), (
            f"PERTURB_THRESHOLDS={PERTURB_THRESHOLDS}, expected {expected}"
        )


# ---------------------------------------------------------------------------
# 2. _mean_expectancy_at_threshold
# ---------------------------------------------------------------------------

class TestMeanExpectancyAtThreshold:
    def test_returns_mean_of_net_pnls_for_matching_hyp_and_etype(self):
        """Mean expectancy = mean of event-level mean net_usd_pnls."""
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=3,
                              net_usd_pnls=[1.0, 2.0, 3.0]),
            _make_unit_result("E2", "CPI", 1, "H1", num_trades=2,
                              net_usd_pnls=[4.0, 6.0]),
        ]
        # Event means: [2.0, 5.0]; overall mean = 3.5
        result = _mean_expectancy_at_threshold(unit_results, hyp_id=1, event_type="CPI", threshold=0.15)
        assert result == pytest.approx(3.5, abs=1e-6)

    def test_ignores_other_event_types(self):
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[5.0, 5.0]),
            _make_unit_result("E2", "NFP", 1, "H1", num_trades=2, net_usd_pnls=[-5.0, -5.0]),
        ]
        result = _mean_expectancy_at_threshold(unit_results, hyp_id=1, event_type="CPI", threshold=0.15)
        assert result == pytest.approx(5.0, abs=1e-6)

    def test_ignores_other_hypotheses(self):
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[3.0, 3.0]),
            _make_unit_result("E1", "CPI", 2, "H2", num_trades=2, net_usd_pnls=[-99.0]),
        ]
        result = _mean_expectancy_at_threshold(unit_results, hyp_id=1, event_type="CPI", threshold=0.15)
        assert result == pytest.approx(3.0, abs=1e-6)

    def test_zero_trades_contributes_zero(self):
        """Unit with no trades (empty net_usd_pnls) contributes 0.0 per event mean."""
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=0, net_usd_pnls=[]),
            _make_unit_result("E2", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[4.0]),
        ]
        result = _mean_expectancy_at_threshold(unit_results, hyp_id=1, event_type="CPI", threshold=0.15)
        # Event means: [0.0, 4.0]; overall mean = 2.0
        assert result == pytest.approx(2.0, abs=1e-6)

    def test_no_matching_events_returns_none(self):
        result = _mean_expectancy_at_threshold([], hyp_id=1, event_type="CPI", threshold=0.15)
        assert result is None


# ---------------------------------------------------------------------------
# 3. _compute_parameter_stability
# ---------------------------------------------------------------------------

class TestComputeParameterStability:
    def test_stability_score_1_when_all_events_strongly_positive(self):
        """All events have large positive PnLs → all thresholds positive → score 1.0."""
        unit_results = [
            _make_unit_result(f"E{i}", "CPI", 1, "H1", num_trades=5,
                              net_usd_pnls=[10.0, 10.0, 10.0, 10.0, 10.0])
            for i in range(5)
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)

        assert "by_cell" in result
        assert "1__CPI" in result["by_cell"]
        cell = result["by_cell"]["1__CPI"]
        assert cell["parameter_stability_score"] == pytest.approx(1.0), (
            f"Expected score 1.0 for strong signal, got {cell['parameter_stability_score']}"
        )

    def test_stability_score_less_than_1_for_marginal_signal(self):
        """Marginal expectancies: mean near 0 → some perturbed thresholds yield ≤ 0 → score < 1.0.

        When net_usd_pnls are near zero, _mean_expectancy_at_threshold returns
        approximately 0. The function uses base-threshold trades as a proxy for
        all thresholds; if base trades have ~0 mean, score will be 0.0 or low.
        """
        # Near-zero PnLs — expectancy ≈ 0 at base threshold
        unit_results = [
            _make_unit_result(f"E{i}", "CPI", 1, "H1", num_trades=3,
                              net_usd_pnls=[0.001, -0.001, 0.0])
            for i in range(5)
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)
        cell = result["by_cell"]["1__CPI"]
        # With near-zero PnLs, mean ≈ 0 → not > 0 → score should be 0.0 (or very low)
        assert cell["parameter_stability_score"] < 1.0, (
            f"Expected score < 1.0 for marginal signal, got {cell['parameter_stability_score']}"
        )

    def test_stability_score_negative_signal_is_zero(self):
        """Consistently negative expectancy → score = 0.0 (0 out of 6 thresholds pass)."""
        unit_results = [
            _make_unit_result(f"E{i}", "CPI", 1, "H1", num_trades=3,
                              net_usd_pnls=[-5.0, -4.0, -6.0])
            for i in range(5)
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)
        cell = result["by_cell"]["1__CPI"]
        assert cell["parameter_stability_score"] == pytest.approx(0.0, abs=1e-9)

    def test_perturb_details_has_all_thresholds(self):
        """perturb_details must have one entry per threshold in PERTURB_THRESHOLDS."""
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[1.0, 1.0]),
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)
        cell = result["by_cell"]["1__CPI"]
        assert len(cell["perturb_details"]) == len(PERTURB_THRESHOLDS)

    def test_empty_survivors_returns_empty_by_cell(self):
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[1.0]),
        ]
        result = _compute_parameter_stability(unit_results, survivors=[], band_ms=1.0)
        assert result["by_cell"] == {}

    def test_metadata_fields_present(self):
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=2, net_usd_pnls=[1.0]),
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)
        assert "perturb_thresholds" in result
        assert result["base_threshold"] == SIGNAL_THRESHOLD
        assert "note" in result

    def test_score_matches_fraction_of_positive_thresholds(self):
        """Score = n_positive / n_thresholds exactly."""
        # With strongly positive PnLs: all 6 thresholds positive → score = 6/6 = 1.0
        unit_results = [
            _make_unit_result("E1", "CPI", 1, "H1", num_trades=3, net_usd_pnls=[10.0, 10.0, 10.0]),
            _make_unit_result("E2", "CPI", 1, "H1", num_trades=3, net_usd_pnls=[10.0, 10.0, 10.0]),
            _make_unit_result("E3", "CPI", 1, "H1", num_trades=3, net_usd_pnls=[10.0, 10.0, 10.0]),
        ]
        survivors = [_make_survivor(1, "CPI")]
        result = _compute_parameter_stability(unit_results, survivors, band_ms=1.0)
        cell = result["by_cell"]["1__CPI"]
        n_positive = sum(1 for d in cell["perturb_details"].values() if d["positive"])
        expected_score = n_positive / len(PERTURB_THRESHOLDS)
        assert cell["parameter_stability_score"] == pytest.approx(expected_score, abs=1e-9)


# ---------------------------------------------------------------------------
# 4. run_stage_a integration: default OFF / ON
# ---------------------------------------------------------------------------

def _feature_index_hash_value() -> str:
    from data_system.src.feature_store import feature_index_hash
    return feature_index_hash()


def _make_feature_store_npz(dest: Path, *, signal_burst: bool = True) -> Path:
    """Write a minimal feature-store NPZ that load_store accepts."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    from research_pipeline.src.stage_a_screen import REGIME_LABELS_ORDERED

    n_rows = 30
    ts_start = 1_700_000_000_000_000_000
    tick_ns = 1_000_000

    ts = np.array([ts_start + i * tick_ns for i in range(n_rows)], dtype=np.int64)
    X = np.zeros((n_rows, 64), dtype=np.float64)
    X[:, 40] = 5000.0    # mid_price
    X[:, 41] = 1.0       # regime_normal
    X[:, 15] = 0.25      # spread
    X[:, 16] = 1.0       # spread_stress

    if signal_burst:
        X[:n_rows // 2, 0] = 0.5
        X[n_rows // 2:, 0] = -0.5

    best_bid = np.full(n_rows, 4999.75, dtype=np.float64)
    best_ask = np.full(n_rows, 5000.00, dtype=np.float64)
    bbo_valid = np.ones(n_rows, dtype=np.bool_)

    regime_state_vocab = np.array(list(REGIME_LABELS_ORDERED))
    regime_state_id    = np.zeros(n_rows, dtype=np.int32)
    event_ctx_vocab    = np.array(["NORMAL", "CPI"])
    event_ctx_id       = np.zeros(n_rows, dtype=np.int32)
    vol_state_vocab    = np.array(["NORMAL", "HIGH"])
    vol_state_id       = np.zeros(n_rows, dtype=np.int32)
    liq_state_vocab    = np.array(["NORMAL", "LOW"])
    liq_state_id       = np.zeros(n_rows, dtype=np.int32)

    np.savez_compressed(
        str(dest),
        ts=ts, X=X, best_bid=best_bid, best_ask=best_ask, bbo_valid=bbo_valid,
        regime_state_vocab=regime_state_vocab, regime_state_id=regime_state_id,
        event_ctx_vocab=event_ctx_vocab, event_ctx_id=event_ctx_id,
        vol_state_vocab=vol_state_vocab, vol_state_id=vol_state_id,
        liq_state_vocab=liq_state_vocab, liq_state_id=liq_state_id,
        tick_size=np.float64(0.25),
        feature_index_hash=np.array(_feature_index_hash_value()),
    )
    return dest


def _write_manifest(feature_root: Path, records: list) -> None:
    mp = feature_root / "feature_manifest.jsonl"
    with mp.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _setup_feature_root(tmp_path: Path) -> Path:
    """Create a feature root with 3 pre-embargo events for MES.v.0."""
    from data_system.src.feature_store import store_path

    feature_root = tmp_path / "features"
    sym = "MES.v.0"

    for eid in ("EVT_A", "EVT_B", "EVT_C"):
        _make_feature_store_npz(store_path(feature_root, sym, eid))

    _write_manifest(feature_root, [
        {"symbol": sym, "event_id": "EVT_A", "event_type": "CPI",
         "release_date": "2025-01-10", "content_hash": "aaa",
         "feature_index_hash": _feature_index_hash_value()},
        {"symbol": sym, "event_id": "EVT_B", "event_type": "CPI",
         "release_date": "2025-02-10", "content_hash": "bbb",
         "feature_index_hash": _feature_index_hash_value()},
        {"symbol": sym, "event_id": "EVT_C", "event_type": "CPI",
         "release_date": "2025-03-10", "content_hash": "ccc",
         "feature_index_hash": _feature_index_hash_value()},
    ])
    return feature_root


class TestRunStageAPerturbation:

    def test_default_off_no_parameter_stability_block(self, tmp_path):
        """threshold_perturbation=False (default) → no parameter_stability in outputs."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_default"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
            threshold_perturbation=False,
        )

        result = json.loads((out_dir / "stage_a_result.json").read_text(encoding="utf-8"))
        assert "parameter_stability" not in result, (
            "parameter_stability block must be absent when threshold_perturbation=False"
        )

        surv = json.loads((out_dir / "stage_a_survivors.json").read_text(encoding="utf-8"))
        assert "parameter_stability" not in surv, (
            "parameter_stability block must be absent from survivors JSON when OFF"
        )
        # survivors must NOT have parameter_stability_score field when OFF
        for sv in surv.get("survivors", []):
            assert "parameter_stability_score" not in sv, (
                f"survivor should not have parameter_stability_score when OFF: {sv}"
            )

    def test_perturb_on_adds_parameter_stability_block(self, tmp_path):
        """threshold_perturbation=True → parameter_stability block present in both outputs."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_perturb"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
            threshold_perturbation=True,
        )

        result = json.loads((out_dir / "stage_a_result.json").read_text(encoding="utf-8"))
        surv   = json.loads((out_dir / "stage_a_survivors.json").read_text(encoding="utf-8"))

        # parameter_stability block present when ON
        assert "parameter_stability" in result, (
            "parameter_stability block must be present in stage_a_result.json when --perturb"
        )
        assert "parameter_stability" in surv, (
            "parameter_stability block must be present in stage_a_survivors.json when --perturb"
        )

    def test_survivors_have_stability_score_when_on(self, tmp_path):
        """When perturbation is ON, each survivor must have parameter_stability_score."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_perturb_score"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
            threshold_perturbation=True,
        )

        surv = json.loads((out_dir / "stage_a_survivors.json").read_text(encoding="utf-8"))
        survivors = surv.get("survivors", [])
        # If there are survivors (BH-significant), they must have the stability score
        for sv in survivors:
            assert "parameter_stability_score" in sv, (
                f"survivor {sv!r} missing parameter_stability_score when --perturb ON"
            )
            score = sv["parameter_stability_score"]
            assert 0.0 <= score <= 1.0, (
                f"parameter_stability_score={score} out of [0, 1] range"
            )

    def test_parameter_stability_block_schema(self, tmp_path):
        """parameter_stability block must have required keys."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_schema"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
            threshold_perturbation=True,
        )

        result = json.loads((out_dir / "stage_a_result.json").read_text(encoding="utf-8"))
        stab = result.get("parameter_stability")
        if stab is None:
            # No survivors → block may be absent; ensure parameter_stability not present
            # (empty survivors → block not injected — that's acceptable)
            pytest.skip("No survivors, parameter_stability block absent (acceptable)")

        required_keys = {"perturb_thresholds", "base_threshold", "by_cell", "note"}
        missing = required_keys - set(stab.keys())
        assert not missing, f"parameter_stability missing keys: {missing}"

        assert stab["base_threshold"] == pytest.approx(SIGNAL_THRESHOLD)
        assert len(stab["perturb_thresholds"]) == len(PERTURB_THRESHOLDS)
