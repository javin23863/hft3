import importlib
import textwrap

import pytest

from backtest_pipeline.src.promotion_gate import PromotedCandidate


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("HFT3_AUTONOMY_DIR", str(tmp_path / "auto"))
    monkeypatch.setenv("HFT3_AUTONOMY_CONFIG", str(tmp_path / "autonomy.yaml"))
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    cert = importlib.import_module("model_metrics.certify")
    lc = importlib.import_module("model_metrics.lifecycle")
    return cert, lc, tmp_path, monkeypatch


def _candidate():
    return PromotedCandidate(
        candidate_id="cand1",
        hypothesis_id="29",
        strategy_family="prop",
        asset_class="futures",
        symbol="MGC",
        timeframe="event",
        param_values={},
        vectorbt_run_id="vb",
        vectorbt_results={
            "oos_expectancy": 10.0,
            "win_rate": 0.55,
            "num_trades": 100,
            "max_drawdown_pct": -10.0,
            "slippage_sensitivity": 0.1,
            "wf_consistency": 0.8,
            "param_stability_score": 0.9,
            "turnover_mean_pct": 50.0,
        },
        pass_reason="ok",
        drawdown_metrics={"max_drawdown": -10.0},
    )


def _certify_live(cert, lc, mid="MGC_X"):
    cert.certify_and_snapshot(
        _candidate(),
        lifecycle_id=mid,
        approved_regime_ids=["normal"],
        feature_bounds={"f1": (-1.0, 1.0)},
        latency_envelope={"execution_path_audit": {"status": "pass"}},
    )
    for to in (lc.SHADOW, lc.LIVE):
        lc.apply_transition(mid, to, trigger="t", reason="r", actor="t")


def _degrade(lc, mid="MGC_X"):
    lc.apply_transition(mid, lc.DEGRADED, trigger="decay", reason="slippage drift", actor="test")


def _green_obs(gate_context=None):
    obs = {"model_id": "29", "regime_id": "normal", "feature_values": {"f1": 0.0}}
    if gate_context is not None:
        obs["rearm_gate_context"] = gate_context
    return obs


def _enable_demote_only(tmp_path, monkeypatch):
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: false}
        rearm: {allow_live: false}
    """), encoding="utf-8")
    monkeypatch.setenv("HFT3_AUTONOMY_ENABLED", "1")


def _enable_full_autonomy(tmp_path, monkeypatch):
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: true}
        rearm: {allow_live: true}
    """), encoding="utf-8")
    monkeypatch.setenv("HFT3_AUTONOMY_ENABLED", "1")


def _all_pass_gate_context(tmp_path):
    return {
        "cert_ok_override": True,
        "gauntlet_passed": True,
        "promotion_passed": True,
        "defect_ledger_empty_override": True,
        "shadow_passed": True,
        "embargo_clean": True,
        "determinism_ok": True,
        "kill_drill_ok": True,
        "options_defect_ledger_root": str(tmp_path),
    }


def _write_options_spec(root, status):
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(textwrap.dedent(f"""
        # OPTIONS_LANE.md

        | ID | Component | Description | Status |
        |----|-----------|-------------|--------|
        | o-a | `vol_clock` | placeholder | {status} |
    """), encoding="utf-8")


def test_degraded_green_recovery_refuses_when_rearm_gates_fail(env):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    _degrade(lc)
    _enable_demote_only(tmp, mp)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")

    out = drv.run_eval({"MGC_X": _green_obs()})

    assert out["recovered"] == 0
    assert lc.get_record("MGC_X").current_state == lc.DEGRADED
    action = out["actions"][0]
    assert action["action"] == "recover_refused"
    assert "master_enable" in action["failed"]


def test_degraded_green_recovery_is_counted_only_after_rearm_moves_live(env, monkeypatch):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    _degrade(lc)
    _enable_demote_only(tmp, mp)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    rearm = importlib.import_module("lifecycle_orchestrator.src.rearm")

    def fake_attempt_rearm(model_id, ctx, *, actor="autonomous-orchestrator", ts=None):
        assert isinstance(ctx, rearm.GateContext)
        lc.apply_transition(model_id, lc.LIVE, trigger="auto_arm", reason="gate chain passed", actor=actor, ts=ts)
        return {"armed": True, "allowed": True, "reason": "all required gates passed", "failed": []}

    monkeypatch.setattr(rearm, "attempt_rearm", fake_attempt_rearm)

    out = drv.run_eval({"MGC_X": _green_obs()})

    assert out["recovered"] == 1
    assert lc.get_record("MGC_X").current_state == lc.LIVE
    assert out["actions"][0]["action"] == "recover"


def test_degraded_options_recovery_obeys_lane_profile_gate(env):
    cert, lc, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _write_options_spec(tmp, "**FIXED**")
    _certify_live(cert, lc, mid="FOPT_ES_CALL")
    _degrade(lc, mid="FOPT_ES_CALL")
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")

    out = drv.run_eval({"FOPT_ES_CALL": _green_obs(_all_pass_gate_context(tmp))})

    assert out["recovered"] == 0
    assert lc.get_record("FOPT_ES_CALL").current_state == lc.DEGRADED
    action = out["actions"][0]
    assert action["action"] == "recover_refused"
    assert "promotion_gate" in action["failed"]


def test_recovery_gate_context_rejects_string_false_booleans(env):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    _degrade(lc)
    _enable_full_autonomy(tmp, mp)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    gate_context = _all_pass_gate_context(tmp)
    gate_context["promotion_passed"] = "false"

    out = drv.run_eval({"MGC_X": _green_obs(gate_context)})

    assert out["recovered"] == 0
    assert lc.get_record("MGC_X").current_state == lc.DEGRADED
    action = out["actions"][0]
    assert action["action"] == "recover_refused"
    assert "promotion_gate" in action["failed"]
