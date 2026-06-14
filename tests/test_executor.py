"""ML-FIX — the executor: decay driver, submit gate, job worker, route materialize."""
import importlib
import sys
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
    monkeypatch.delenv("HFT3_ORCH_EXEC", raising=False)
    cert = importlib.import_module("model_metrics.certify")
    lc = importlib.import_module("model_metrics.lifecycle")
    return cert, lc, tmp_path, monkeypatch


def _candidate():
    return PromotedCandidate(
        candidate_id="cand1", hypothesis_id="29", strategy_family="prop", asset_class="futures",
        symbol="MGC", timeframe="event", param_values={}, vectorbt_run_id="vb",
        vectorbt_results={"oos_expectancy": 10.0, "win_rate": 0.55, "num_trades": 100,
                          "max_drawdown_pct": -10.0, "slippage_sensitivity": 0.1,
                          "wf_consistency": 0.8, "param_stability_score": 0.9, "turnover_mean_pct": 50.0},
        pass_reason="ok", drawdown_metrics={"max_drawdown": -10.0},
    )


def _certify_live(cert, lc, mid="MGC_X", cell=None):
    cert.certify_and_snapshot(
        _candidate(), lifecycle_id=mid, approved_regime_ids=["normal"],
        feature_bounds={"f1": (-1.0, 1.0)},
        latency_envelope={"execution_path_audit": {"status": "pass"}}, cell=cell,
    )
    for to in (lc.SHADOW, lc.LIVE):
        lc.apply_transition(mid, to, trigger="t", reason="r", actor="t")


def _enable_demote(tmp, mp):
    (tmp / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: false}
        rearm: {allow_live: false}
    """), encoding="utf-8")
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")


# --- decay driver -----------------------------------------------------------
def test_decay_driver_detection_only_when_autonomy_off(env):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    obs = {"MGC_X": {"model_id": "29", "regime_id": "normal", "feature_values": {"f1": 9.0}}}  # RED
    out = drv.run_eval(obs)
    assert out["flagged"] == 1 and out["demoted"] == 0  # autonomy off -> no state move
    rec = lc.get_record("MGC_X")
    assert rec.current_state == lc.LIVE                  # still LIVE
    assert rec.last_revalidation["model_state"] == "RED"  # but annotated RED


def test_decay_driver_demotes_when_enabled(env):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    _enable_demote(tmp, mp)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    obs = {"MGC_X": {"model_id": "29", "regime_id": "normal", "feature_values": {"f1": 9.0}}}
    out = drv.run_eval(obs)
    assert out["demoted"] == 1
    assert lc.get_record("MGC_X").current_state == lc.DEGRADED


# --- submit gate (enforcement call site) ------------------------------------
def test_submit_gate_blocks_red_tracked_model(env):
    cert, lc, tmp, mp = env
    _certify_live(cert, lc)
    drv = importlib.import_module("lifecycle_orchestrator.src.run_lifecycle_eval")
    drv.run_eval({"MGC_X": {"model_id": "29", "regime_id": "normal", "feature_values": {"f1": 9.0}}})
    sg = importlib.import_module("model_metrics.submit_gate")
    sg._cache["mtime"] = None  # bust cache
    allowed, size, _ = sg.model_submit_decision("MGC_X")
    assert allowed is False and size == 0.0          # RED -> flat
    allowed2, size2, reason2 = sg.model_submit_decision("UNKNOWN_MODEL")
    assert allowed2 is True and size2 == 1.0 and reason2 == "untracked"


# --- job worker -------------------------------------------------------------
def test_worker_records_only_when_exec_disabled(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    worker = importlib.import_module("lifecycle_orchestrator.src.worker")
    importlib.reload(worker)
    jr.enqueue("M", "param_tweak", {"entry": "scripts/run_event_universe.py", "args": ["--from-stage-a", "/tmp/x.json"]})
    res = worker.run_once(do_exec=False)
    assert res["processed"] == 1
    failed = jr.list_jobs("failed")
    assert failed and failed[0]["artifacts"]["executed"] is False
    assert "HFT3_ORCH_EXEC" in failed[0]["error"]


def test_job_runner_singleton_rejects_active_duplicate(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    jid = jr.enqueue_singleton("M", "cockpit", {"entry": "x.py", "args": []})

    with pytest.raises(jr.DuplicateActiveJob) as excinfo:
        jr.enqueue_singleton("M", "cockpit", {"entry": "x.py", "args": []})

    assert excinfo.value.job["job_id"] == jid


def test_worker_skips_unmaterialized_command(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    worker = importlib.import_module("lifecycle_orchestrator.src.worker")
    importlib.reload(worker)
    jr.enqueue("M", "param_tweak", {"entry": "x.py", "args": ["--from-stage-a", "<bridge_stub>"]})
    res = worker.run_once(do_exec=True)  # exec on, but command has placeholder -> skipped
    assert res["jobs"][0]["state"] == "failed"
    assert jr.list_jobs("failed")[0]["artifacts"]["executed"] is False


def test_worker_fails_chi404_job_without_chi404_entitlement(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    worker = importlib.import_module("lifecycle_orchestrator.src.worker")
    importlib.reload(worker)
    jr.enqueue("M", "roll_now", {"entry": "systemctl", "args": ["restart", "hft3-capture"]}, host="chi404")
    res = worker.run_once(do_exec=True)
    assert res["jobs"][0]["state"] == "failed"
    failed = jr.list_jobs("failed")
    assert failed and failed[0]["artifacts"]["executed"] is False
    assert "CHI404" in failed[0]["error"]


def test_worker_fails_nonzero_returncode(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    worker = importlib.import_module("lifecycle_orchestrator.src.worker")
    importlib.reload(worker)
    jr.enqueue("M", "param_tweak", {"entry": sys.executable, "args": ["-c", "import sys; sys.exit(2)"]})
    res = worker.run_once(do_exec=True)
    assert res["jobs"][0]["state"] == "failed"
    failed = jr.list_jobs("failed")
    assert failed[0]["artifacts"]["returncode"] == 2
    assert "returncode 2" in failed[0]["error"]


# --- route materialization --------------------------------------------------
def test_route_materializes_real_command_with_cell(env):
    cert, lc, tmp, mp = env
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    routes = importlib.import_module("lifecycle_orchestrator.src.routes")
    importlib.reload(routes)
    _certify_live(cert, lc, mid="MGC_Y", cell={"hyp_id": 29, "event_type": "FRIDAY_CLOSE", "band_ms": 6.25, "slug": "mgc_y"})
    for to in (lc.DEGRADED,):
        lc.apply_transition("MGC_Y", to, trigger="t", reason="r", actor="t", route=lc.ROUTE_PARAM_TWEAK)
    plan = routes.handle_route(lc.get_record("MGC_Y"))
    assert plan["action"] == "param_rescreen" and plan["materialized"] is True
    job = jr.list_jobs("pending")[0]
    assert "<bridge_stub>" not in job["command"]["args"]  # real stub path substituted
