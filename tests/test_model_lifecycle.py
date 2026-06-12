"""ML1 — lifecycle state machine + hash-chained registry."""
import importlib

import pytest


@pytest.fixture()
def lc(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lifecycle"))
    mod = importlib.import_module("model_metrics.lifecycle")
    importlib.reload(mod)  # re-resolve paths under the patched env
    return mod


def _create(lc, mid="MES_SECOND_WAVE", hyp=1):
    return lc.apply_transition(
        mid, lc.CANDIDATE, trigger="register", reason="new candidate",
        actor="test", create=True, initial={"hypothesis_id": hyp, "symbol": "MES"},
    )


def test_create_and_forward_path(lc):
    _create(lc)
    for to in (lc.SCREENING, lc.GAUNTLET, lc.CERTIFIED, lc.SHADOW, lc.LIVE):
        lc.apply_transition("MES_SECOND_WAVE", to, trigger="t", reason="r", actor="test")
    rec = lc.get_record("MES_SECOND_WAVE")
    assert rec.current_state == lc.LIVE
    assert rec.current_state_since
    assert lc.verify_chain() is True


def test_illegal_transition_raises(lc):
    _create(lc)
    # CANDIDATE -> LIVE is not legal
    with pytest.raises(lc.IllegalTransitionError):
        lc.apply_transition("MES_SECOND_WAVE", lc.LIVE, trigger="t", reason="r", actor="test")


def test_quarantine_from_any_nonterminal(lc):
    _create(lc)
    lc.apply_transition("MES_SECOND_WAVE", lc.SCREENING, trigger="t", reason="r", actor="test")
    # SCREENING -> QUARANTINED is allowed even though not in the explicit table
    lc.apply_transition("MES_SECOND_WAVE", lc.QUARANTINED, trigger="halt", reason="manual", actor="test")
    assert lc.get_record("MES_SECOND_WAVE").current_state == lc.QUARANTINED


def test_retired_is_terminal(lc):
    _create(lc)
    lc.apply_transition("MES_SECOND_WAVE", lc.RETIRED, trigger="t", reason="edge gone", actor="test")
    with pytest.raises(lc.IllegalTransitionError):
        lc.apply_transition("MES_SECOND_WAVE", lc.SCREENING, trigger="t", reason="r", actor="test")


def test_routes_and_demotion_recorded(lc):
    _create(lc)
    for to in (lc.SCREENING, lc.GAUNTLET, lc.CERTIFIED, lc.SHADOW, lc.LIVE):
        lc.apply_transition("MES_SECOND_WAVE", to, trigger="t", reason="r", actor="test")
    lc.apply_transition(
        "MES_SECOND_WAVE", lc.DEGRADED, trigger="decay", reason="slippage drift",
        actor="detector", route=lc.ROUTE_PARAM_TWEAK,
        record_updates={"demotion": {"reason": "slippage_drift", "from_state": "LIVE"}},
    )
    rec = lc.get_record("MES_SECOND_WAVE")
    assert rec.current_state == lc.DEGRADED
    assert rec.reentry_routing["route"] == lc.ROUTE_PARAM_TWEAK
    assert rec.demotion["reason"] == "slippage_drift"


def test_regime_pause_autoream_edge(lc):
    _create(lc)
    for to in (lc.SCREENING, lc.GAUNTLET, lc.CERTIFIED, lc.SHADOW, lc.LIVE, lc.DEGRADED):
        lc.apply_transition("MES_SECOND_WAVE", to, trigger="t", reason="r", actor="test")
    lc.apply_transition("MES_SECOND_WAVE", lc.ARCHIVED_PAUSED, trigger="regime", reason="dormant",
                        actor="detector", route=lc.ROUTE_REGIME_SHIFT)
    # ARCHIVED_PAUSED -> LIVE (auto re-arm) is legal
    lc.apply_transition("MES_SECOND_WAVE", lc.LIVE, trigger="regime_back", reason="regime returned", actor="detector")
    assert lc.get_record("MES_SECOND_WAVE").current_state == lc.LIVE


def test_chain_tamper_detected(lc):
    _create(lc)
    lc.apply_transition("MES_SECOND_WAVE", lc.SCREENING, trigger="t", reason="r", actor="test")
    # Corrupt the first line of the transition log
    p = lc.transitions_path()
    lines = p.read_text(encoding="utf-8").splitlines()
    import json as _j
    rec0 = _j.loads(lines[0])
    rec0["reason"] = "TAMPERED"
    lines[0] = _j.dumps(rec0)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert lc.verify_chain() is False


def test_duplicate_create_rejected(lc):
    _create(lc)
    with pytest.raises(lc.LifecycleError):
        _create(lc)


def test_rebuild_registry_from_log(lc):
    _create(lc)
    for to in (lc.SCREENING, lc.GAUNTLET):
        lc.apply_transition("MES_SECOND_WAVE", to, trigger="t", reason="r", actor="test")
    # simulate a crash that lost the materialized snapshot
    lc.registry_path().unlink()
    assert lc.load_registry() == {}
    rebuilt = lc.rebuild_registry_from_log()
    assert rebuilt["MES_SECOND_WAVE"].current_state == lc.GAUNTLET
    assert rebuilt["MES_SECOND_WAVE"].hypothesis_id == 1   # from logged `initial`
    # snapshot rewritten + consistent with the log
    assert lc.load_registry()["MES_SECOND_WAVE"].current_state == lc.GAUNTLET


def test_envelope_snapshot_roundtrip(lc):
    lc.save_envelope_snapshot("env_abc", {"envelope_id": "env_abc", "active": True})
    assert lc.load_envelope_snapshot("env_abc")["active"] is True
    assert lc.load_envelope_snapshot("missing") is None
