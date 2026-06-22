"""End-to-end lifecycle workstation simulation (no live adapters, no Vast)."""
from __future__ import annotations

import importlib
import json
import textwrap

import pytest

from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.aggregate import model_detail as model_detail_agg
from backtest_pipeline.src.promotion_gate import PromotedCandidate


@pytest.fixture()
def env(tmp_path, monkeypatch):
    lc_dir = tmp_path / "lifecycle"
    lc_dir.mkdir(parents=True)
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(lc_dir))
    monkeypatch.setenv("HFT3_AUTONOMY_DIR", str(tmp_path / "auto"))
    monkeypatch.setenv("HFT3_AUTONOMY_CONFIG", str(tmp_path / "autonomy.yaml"))
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: false}
        rearm: {allow_live: false}
    """), encoding="utf-8")
    monkeypatch.setenv("HFT3_AUTONOMY_ENABLED", "1")

    from apps.cockpit.backend import paths

    reg = lc_dir / "model_lifecycle.json"
    tx = lc_dir / "transitions.jsonl"
    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", reg)
    monkeypatch.setattr(paths, "LIFECYCLE_TRANSITIONS", tx)

    cert = importlib.import_module("model_metrics.certify")
    lc = importlib.import_module("model_metrics.lifecycle")
    obs_mod = importlib.import_module("lifecycle_orchestrator.src.observations")
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    routes = importlib.import_module("lifecycle_orchestrator.src.routes")
    return cert, lc, obs_mod, drv, routes, lc_dir, tmp_path, monkeypatch


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
            "slippage_sensitivity": 2.5,
            "wf_consistency": 0.8,
            "param_stability_score": 0.9,
            "turnover_mean_pct": 50.0,
        },
        pass_reason="ok",
        drawdown_metrics={"max_drawdown": -10.0},
    )


def test_lifecycle_workstation_e2e_degrade_route_cockpit(env):
    cert, lc, obs_mod, drv, routes, lc_dir, repo, _ = env
    mid = "MGC_X"

    cert.certify_and_snapshot(
        _candidate(),
        lifecycle_id=mid,
        approved_regime_ids=["normal"],
        feature_bounds={"f1": (-1.0, 1.0)},
        latency_envelope={"execution_path_audit": {"status": "pass"}},
        validate_gate=False,
    )
    for to in (lc.SHADOW, lc.LIVE):
        lc.apply_transition(mid, to, trigger="t", reason="r", actor="t")

    art = lc_dir / "reval.json"
    art.write_text(json.dumps({
        "generated_at": "2026-06-22T12:00:00+00:00",
        "vectorbt_results": {
            "oos_expectancy": 1.0,
            "win_rate": 0.5,
            "max_drawdown_pct": -20.0,
            "slippage_sensitivity": 3.0,
            "num_trades": 80,
        },
    }), encoding="utf-8")
    rec = lc.get_record(mid)
    rec.research_card_links = {"vectorbt": str(art)}
    lc.annotate(mid, {"research_card_links": rec.research_card_links}, reason="link", actor="t")

    observations = obs_mod.build_observations(repo_root=repo)
    assert mid in observations
    assert observations[mid]["_evidence"]["status"] == "ok"

    out = drv.run_eval(observations, actor="decay_driver", ts="2026-06-22T12:01:00+00:00")
    assert out["evaluated"] == 1
    assert out["flagged"] >= 1

    rec = lc.get_record(mid)
    assert rec.current_state == lc.DEGRADED
    assert (rec.last_revalidation or {}).get("model_state") != "GREEN"

    from model_metrics.submit_gate import model_submit_decision

    allowed, size, _ = model_submit_decision(mid)
    assert allowed is False or size < 1.0

    degraded = lc.get_record(mid)
    plan = routes.handle_route(degraded, actor="e2e", reason="slippage drift")
    assert plan.get("manifest") is not None

    z = ZONES["lifecycle"]()
    row = next(r for r in z["rows"] if r["id"] == mid)
    assert row["state"] == "DEGRADED"
    assert row["route"] is not None
    assert row["next_required_gate"] == "rearm G0-G8"
    assert row["submit_allowed"] is False

    detail = model_detail_agg.build(29)
    lc_block = detail["lifecycle"]
    assert lc_block["tracked"] is True
    assert lc_block["state"] == "DEGRADED"

    rearm_obs = dict(observations[mid])
    rearm_obs.update({
        "regime_id": "normal",
        "feature_values": {"f1": 0.0},
        "slippage_bps": 0.0,
        "drawdown": -1.0,
        "rearm_gate_context": {"gauntlet_passed": False},
    })
    recover = drv.run_eval({mid: rearm_obs}, actor="decay_driver")
    assert recover["recovered"] == 0
    assert any(a.get("action") == "recover_refused" for a in recover.get("actions", []))
