"""ML2 — certify hook freezes the envelope + transitions to CERTIFIED."""
import importlib

import pytest

from backtest_pipeline.src.promotion_gate import PromotedCandidate


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lifecycle"))
    cert = importlib.import_module("model_metrics.certify")
    lc = importlib.import_module("model_metrics.lifecycle")
    return cert, lc


def _candidate():
    return PromotedCandidate(
        candidate_id="cand_MES_1",
        hypothesis_id="1",
        strategy_family="second_wave",
        asset_class="futures",
        symbol="MES",
        timeframe="event",
        param_values={"entry_threshold": 0.15},
        vectorbt_run_id="vb_1",
        vectorbt_results={"oos_expectancy": 12.0, "win_rate": 0.55, "num_trades": 120,
                           "max_drawdown_pct": -12.0, "slippage_sensitivity": 0.1,
                           "wf_consistency": 0.8, "param_stability_score": 0.9, "turnover_mean_pct": 50.0},
        pass_reason="passed all gates",
        drawdown_metrics={"max_drawdown": -12.0},
    )


def test_certify_mints_envelope_and_certifies(env):
    cert, lc = env
    envelope, rec = cert.certify_and_snapshot(
        _candidate(),
        lifecycle_id="MES_SECOND_WAVE",
        approved_regime_ids=["normal", "event_shock"],
        blocked_regime_ids=["liquidity_vacuum"],
        feature_bounds={"f1": (-1.0, 1.0)},
    )
    # envelope carries the regime sets that separate pause vs retire later
    assert envelope.approved_regime_ids == ("normal", "event_shock")
    assert envelope.blocked_regime_ids == ("liquidity_vacuum",)
    assert envelope.active is True  # grade A
    # frozen snapshot persisted + retrievable
    snap = lc.load_envelope_snapshot(envelope.envelope_id)
    assert snap is not None and snap["envelope_id"] == envelope.envelope_id
    # lifecycle advanced to CERTIFIED with the envelope linked
    assert rec.current_state == lc.CERTIFIED
    assert rec.current_envelope_id == envelope.envelope_id
    assert rec.certified_edge_envelope_snapshot["approved_regime_ids"] == ["normal", "event_shock"]
    assert lc.verify_chain() is True


def test_certify_refuses_failing_candidate(env):
    cert, lc = env
    bad = _candidate()
    bad.vectorbt_results = {"oos_expectancy": -1.0, "num_trades": 3}  # fails the gate
    with pytest.raises(cert.CertifyError):
        cert.certify_and_snapshot(bad, lifecycle_id="MES_BAD")
    # nothing minted, no lifecycle record created
    assert lc.get_record("MES_BAD") is None


def test_certify_then_decay_uses_frozen_envelope(env):
    cert, lc = env
    envelope, _ = cert.certify_and_snapshot(
        _candidate(), lifecycle_id="MES_SECOND_WAVE", approved_regime_ids=["normal"],
        latency_envelope={"execution_path_audit": {"status": "pass"}},
    )
    dd = importlib.import_module("model_metrics.decay_detector")
    snap = lc.load_envelope_snapshot(envelope.envelope_id)
    # an observation in an unlisted regime -> regime-pause route, against the FROZEN envelope
    r = dd.evaluate(snap, {"model_id": "1", "regime_id": "some_other_regime"})
    assert r.route == lc.ROUTE_REGIME_SHIFT
