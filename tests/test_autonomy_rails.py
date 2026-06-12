"""ML5 — safety rails: two-key enable, kill, breaker, gate-chain anti-bypass, audit."""
import importlib
import textwrap

import pytest

cfg = importlib.import_module("autonomy.config")
breaker = importlib.import_module("autonomy.breaker")
gates = importlib.import_module("autonomy.gates")
audit = importlib.import_module("autonomy.audit")


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_AUTONOMY_DIR", str(tmp_path / "autonomy"))
    monkeypatch.setenv("HFT3_AUTONOMY_CONFIG", str(tmp_path / "autonomy.yaml"))
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    return tmp_path, monkeypatch


def _write_cfg(tmp_path, *, enabled, rearm_live=False, actions=None):
    actions = actions or {}
    body = {
        "demote": actions.get("demote", False), "retest": actions.get("retest", False),
        "repromote": actions.get("repromote", False), "rearm": actions.get("rearm", False),
    }
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent(f"""
        enabled: {str(enabled).lower()}
        actions:
          demote: {str(body['demote']).lower()}
          retest: {str(body['retest']).lower()}
          repromote: {str(body['repromote']).lower()}
          rearm: {str(body['rearm']).lower()}
        rearm:
          allow_live: {str(rearm_live).lower()}
        circuit_breaker:
          max_failed_arms_in_window: 2
          window_hours: 72
          max_arm_cycles_per_model: 2
          thrash_window_hours: 24
          max_simultaneous_demotions: 2
          correlation_window_minutes: 30
    """), encoding="utf-8")


def test_default_disabled(sandbox):
    tmp, _ = sandbox  # no config file at all
    assert cfg.master_enabled() is False
    assert cfg.can_arm_live() is False
    for a in cfg.ACTIONS:
        assert cfg.action_enabled(a) is False


def test_two_key_requires_both(sandbox):
    tmp, mp = sandbox
    _write_cfg(tmp, enabled=True, actions={"demote": True})
    # file says enabled, but env key absent -> still disabled
    assert cfg.master_enabled() is False
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")
    assert cfg.master_enabled() is True
    assert cfg.action_enabled("demote") is True
    assert cfg.action_enabled("rearm") is False  # action flag off


def test_rearm_needs_sub_flag(sandbox):
    tmp, mp = sandbox
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")
    _write_cfg(tmp, enabled=True, rearm_live=False, actions={"rearm": True})
    assert cfg.can_arm_live() is False  # allow_live false
    _write_cfg(tmp, enabled=True, rearm_live=True, actions={"rearm": True})
    assert cfg.can_arm_live() is True


def test_kill_engages_disable(sandbox):
    tmp, mp = sandbox
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")
    _write_cfg(tmp, enabled=True, rearm_live=True, actions={"rearm": True, "demote": True})
    assert cfg.master_enabled() is True
    mp.setenv("HFT3_AUTONOMY_KILL", "fired")
    assert cfg.master_enabled() is False
    assert cfg.can_arm_live() is False


def test_frozen_breaker_disables(sandbox):
    tmp, mp = sandbox
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")
    _write_cfg(tmp, enabled=True, actions={"demote": True})
    assert cfg.action_enabled("demote") is True
    breaker.trip("test freeze")
    assert breaker.is_frozen() is True
    assert cfg.kill_engaged() is True
    assert cfg.action_enabled("demote") is False
    breaker.clear(operator="human")
    assert cfg.action_enabled("demote") is True


def test_malformed_config_fails_closed(sandbox):
    tmp, mp = sandbox
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")
    (tmp / "autonomy.yaml").write_text("{ this: is: not valid", encoding="utf-8")
    assert cfg.master_enabled() is False  # parse error => disabled


# --- gate chain anti-bypass -------------------------------------------------
def _full_pass():
    return [gates.GateResult(name=n, passed=True) for n in gates.AUTONOMY_REQUIRED_GATES]


def test_gate_chain_all_pass():
    r = gates.evaluate_gate_chain(_full_pass())
    assert r["allowed"] is True


def test_gate_chain_missing_required_trips_breaker():
    results = [g for g in _full_pass() if g.name != gates.GATE_SHADOW]  # drop one
    r = gates.evaluate_gate_chain(results)
    assert r["allowed"] is False
    assert gates.GATE_SHADOW in r["missing"]
    assert r["trip_breaker"] is True


def test_gate_chain_rejects_extra_gate():
    results = _full_pass() + [gates.GateResult(name="rogue_gate", passed=True)]
    r = gates.evaluate_gate_chain(results)
    assert r["allowed"] is False
    assert "rogue_gate" in r["extra"] and r["trip_breaker"] is True


def test_gate_chain_rejects_nonblocking_required():
    results = [gates.GateResult(name=n, passed=True, blocking=(n != gates.GATE_SHADOW))
               for n in gates.AUTONOMY_REQUIRED_GATES]
    r = gates.evaluate_gate_chain(results)
    assert r["allowed"] is False
    assert gates.GATE_SHADOW in r["non_blocking_required"] and r["trip_breaker"] is True


def test_gate_chain_failing_gate_refuses():
    results = [gates.GateResult(name=n, passed=(n != gates.GATE_DEFECT_LEDGER)) for n in gates.AUTONOMY_REQUIRED_GATES]
    r = gates.evaluate_gate_chain(results)
    assert r["allowed"] is False
    assert gates.GATE_DEFECT_LEDGER in r["failed"]
    assert r["trip_breaker"] is False


# --- breaker trip conditions ------------------------------------------------
def test_breaker_trips_on_failed_arms(sandbox):
    tmp, mp = sandbox
    _write_cfg(tmp, enabled=True)
    conf = cfg.load_config()
    breaker.record_arm_outcome("M1", "fail")
    breaker.record_arm_outcome("M2", "killed")
    reason = breaker.evaluate_and_maybe_trip(conf)
    assert reason and "failed/killed" in reason
    assert breaker.is_frozen() is True


def test_breaker_trips_on_mass_demotion(sandbox):
    tmp, mp = sandbox
    _write_cfg(tmp, enabled=True)
    conf = cfg.load_config()
    breaker.record_demotion("M1")
    breaker.record_demotion("M2")
    reason = breaker.evaluate_and_maybe_trip(conf)
    assert reason and "mass-demotion" in reason


# --- audit chain ------------------------------------------------------------
def test_audit_chain_verifies_and_detects_tamper(sandbox):
    tmp, mp = sandbox
    audit.append(audit.AUTO_ARM_REFUSED, {"reason": "ledger open"}, model_id="M1")
    audit.append(audit.DEGRADATION_DETECTED, {"state": "RED"}, model_id="M2")
    assert audit.verify_chain() is True
    # tamper line 0
    p = audit.paths.audit_path()
    import json
    lines = p.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0]); rec["payload"] = {"reason": "TAMPER"}
    lines[0] = json.dumps(rec)
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert audit.verify_chain() is False
