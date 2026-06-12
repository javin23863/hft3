"""ML6/ML7 — orchestrator scan gating, routes, param proposer, bridge, job queue."""
import importlib
import textwrap

import pytest

pp = importlib.import_module("lifecycle_orchestrator.src.param_proposer")
qb = importlib.import_module("lifecycle_orchestrator.src.quarantine_bridge")


# --- param proposer (pure) --------------------------------------------------
def test_param_proposer_grid_and_clamp():
    fam = {"sweep": {"entry_threshold": {"grid": [0.10, 0.15, 0.20]},
                     "horizon_s": {"grid": [1, 5]}}}
    out = pp.propose_params(fam)
    assert len(out) == 6  # 3 x 2
    clamped = pp.propose_params(fam, envelope_bounds={"entry_threshold": (0.12, 0.18)})
    assert all(0.12 <= c["entry_threshold"] <= 0.18 for c in clamped)
    assert len(clamped) == 2  # only 0.15 passes x 2 horizons


def test_param_proposer_deterministic():
    fam = {"sweep": {"a": {"grid": [2, 1]}, "b": {"grid": [9, 8]}}}
    assert pp.propose_params(fam) == pp.propose_params(fam)


# --- bridge -----------------------------------------------------------------
def test_bridge_materialize_param():
    stub = qb.materialize_param_candidate(hyp_id=29, event_type="FRIDAY_CLOSE", band_ms=6.25,
                                          params={"entry_threshold": 0.15}, slug="mgc_prop")
    assert stub["pass_through"] == [29]
    assert stub["tested_cells"][0]["params"]["entry_threshold"] == 0.15


def test_bridge_scratch_id_above_900():
    assert qb.assign_scratch_id([1, 2, 50]) == 900
    assert qb.assign_scratch_id([900, 901]) == 902
    assert qb.scratch_registry_enabled() is False  # default off => production registry clean


# --- job runner -------------------------------------------------------------
@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("HFT3_AUTONOMY_DIR", str(tmp_path / "auto"))
    monkeypatch.setenv("HFT3_AUTONOMY_CONFIG", str(tmp_path / "autonomy.yaml"))
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    return tmp_path, monkeypatch


def test_job_queue_lifecycle(sandbox):
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    jid = jr.enqueue("MES_X", "param_tweak", {"step": "rescreen"})
    assert len(jr.list_jobs("pending")) == 1
    jr.claim(jid)
    assert len(jr.list_jobs("running")) == 1
    jr.complete(jid, {"universe_result": "path"})
    assert len(jr.list_jobs("done")) == 1
    assert jr.list_jobs("done")[0]["artifacts"]["universe_result"] == "path"


def test_capture_guard_fails_closed_when_absent(sandbox, monkeypatch):
    tmp, _ = sandbox
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    importlib.reload(jr)
    lc = importlib.import_module("model_metrics.lifecycle")
    monkeypatch.setattr(lc, "_repo_root", lambda: tmp)  # absent baseline -> fail-closed
    assert jr.capture_healthy() is False


# --- orchestrator scan ------------------------------------------------------
def _enable(tmp_path, mp):
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: false}
        rearm: {allow_live: false}
    """), encoding="utf-8")
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")


def _make_degraded(lc, mid="MES_X", route="param_tweak"):
    lc.apply_transition(mid, lc.CANDIDATE, trigger="r", reason="r", actor="t", create=True,
                        initial={"hypothesis_id": 29, "symbol": "MES"})
    for to in (lc.SCREENING, lc.GAUNTLET, lc.CERTIFIED, lc.SHADOW, lc.LIVE, lc.DEGRADED):
        lc.apply_transition(mid, to, trigger="r", reason="r", actor="t",
                            route=route if to == lc.DEGRADED else None)


def test_scan_disabled_by_default(sandbox):
    tmp, mp = sandbox
    orch = importlib.import_module("lifecycle_orchestrator.src.orchestrator")
    audit = importlib.import_module("autonomy.audit")
    res = orch.scan()
    assert res["disabled"] is True and res["scanned"] == 0
    assert any(r["event_type"] == "MASTER_DISABLED" for r in audit.tail(5))


def test_scan_dispatches_degraded_param_model(sandbox):
    tmp, mp = sandbox
    _enable(tmp, mp)
    lc = importlib.import_module("model_metrics.lifecycle")
    orch = importlib.import_module("lifecycle_orchestrator.src.orchestrator")
    jr = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    audit = importlib.import_module("autonomy.audit")
    _make_degraded(lc)
    res = orch.scan()
    assert res["disabled"] is False and res["scanned"] == 1
    assert res["dispatched"][0]["action"] == "param_rescreen"
    assert len(jr.list_jobs("pending")) == 1
    assert any(r["event_type"] == "RETEST_START" for r in audit.tail(5))
