import importlib
import json
from datetime import datetime, timedelta, timezone

import pytest

from backtest_pipeline.src.promotion_gate import PromotedCandidate
from model_metrics import decay_detector, lifecycle


@pytest.fixture()
def env(tmp_path, monkeypatch):
    lc_dir = tmp_path / "lifecycle"
    lc_dir.mkdir(parents=True)
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(lc_dir))
    cert = importlib.import_module("model_metrics.certify")
    obs = importlib.import_module("lifecycle_orchestrator.src.observations")
    return cert, obs, lc_dir, tmp_path, monkeypatch


def _candidate(**vbt_overrides):
    vbt = {
        "oos_expectancy": 10.0,
        "win_rate": 0.55,
        "num_trades": 100,
        "max_drawdown_pct": -10.0,
        "slippage_sensitivity": 0.1,
        "wf_consistency": 0.8,
        "param_stability_score": 0.9,
        "turnover_mean_pct": 50.0,
    }
    vbt.update(vbt_overrides)
    return PromotedCandidate(
        candidate_id="cand1",
        hypothesis_id="29",
        strategy_family="prop",
        asset_class="futures",
        symbol="MGC",
        timeframe="event",
        param_values={},
        vectorbt_run_id="vb",
        vectorbt_results=vbt,
        pass_reason="ok",
        drawdown_metrics={"max_drawdown": -10.0},
    )


def _live_with_link(cert, mid, artifact_path):
    cert.certify_and_snapshot(
        _candidate(),
        lifecycle_id=mid,
        approved_regime_ids=["normal"],
        feature_bounds={"f1": (-1.0, 1.0)},
        latency_envelope={"execution_path_audit": {"status": "pass"}},
    )
    for to in (lifecycle.SHADOW, lifecycle.LIVE):
        lifecycle.apply_transition(mid, to, trigger="t", reason="r", actor="t")
    lifecycle.annotate(mid, {"research_card_links": {"vectorbt": str(artifact_path)}}, reason="link", actor="t")
    return lifecycle.get_record(mid)


def test_missing_vectorbt_evidence_blocks_green(env):
    cert, obs_mod, lc_dir, repo, _ = env
    mid = "MGC_X"
    _live_with_link(cert, mid, lc_dir / "missing.json")
    lifecycle.annotate(mid, {"research_card_links": {}}, reason="clr", actor="t")
    rec = lifecycle.get_record(mid)
    out = obs_mod.build_observation_for_model(mid, rec, repo_root=repo)
    assert out["_evidence"]["blocked"] is True
    assert out["_evidence"]["status"] == "missing"
    env_snap = lifecycle.load_envelope_snapshot(rec.current_envelope_id)
    result = decay_detector.evaluate(env_snap, out)
    assert result.model_state != "GREEN"


def test_stale_evidence_blocks(env):
    cert, obs_mod, lc_dir, repo, _ = env
    art = lc_dir / "stale.json"
    old = (datetime.now(timezone.utc) - timedelta(days=120)).isoformat()
    art.write_text(json.dumps({
        "generated_at": old,
        "vectorbt_results": {"oos_expectancy": 1.0, "max_drawdown_pct": -5.0, "num_trades": 10},
    }), encoding="utf-8")
    mid = "MGC_Y"
    rec = _live_with_link(cert, mid, art)
    out = obs_mod.build_observation_for_model(mid, rec, repo_root=repo, max_evidence_age_days=30)
    assert out["_evidence"]["status"] == "stale"
    env_snap = lifecycle.load_envelope_snapshot(rec.current_envelope_id)
    result = decay_detector.evaluate(env_snap, out)
    assert result.model_state != "GREEN"


def test_valid_degraded_observation_maps_triggers(env):
    cert, obs_mod, lc_dir, repo, _ = env
    art = lc_dir / "slip.json"
    art.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vectorbt_results": {
            "oos_expectancy": 1.0,
            "win_rate": 0.55,
            "max_drawdown_pct": -5.0,
            "slippage_sensitivity": 2.5,
            "num_trades": 40,
        },
    }), encoding="utf-8")
    mid = "MGC_Z"
    rec = _live_with_link(cert, mid, art)
    out = obs_mod.build_observation_for_model(mid, rec, repo_root=repo)
    env_snap = lifecycle.load_envelope_snapshot(rec.current_envelope_id)
    result = decay_detector.evaluate(env_snap, out)
    names = {t.get("name") for t in result.triggers}
    assert "slippage" in names or "slippage_drift" in names
    assert out["_evidence"]["source_path"]


def test_rearm_gate_context_included(env):
    cert, obs_mod, lc_dir, repo, _ = env
    art = lc_dir / "ok.json"
    art.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vectorbt_results": {"oos_expectancy": 1.0, "max_drawdown_pct": -5.0, "num_trades": 10},
    }), encoding="utf-8")
    mid = "MGC_R"
    rec = _live_with_link(cert, mid, art)
    ctx = {"gauntlet_passed": True, "shadow_passed": True}
    out = obs_mod.build_observation_for_model(mid, rec, repo_root=repo, rearm_gate_context=ctx)
    assert out["rearm_gate_context"] == ctx
