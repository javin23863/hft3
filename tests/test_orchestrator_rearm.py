"""ML8 — gauntlet reader + autonomous re-arm gate chain (safety-critical)."""
import importlib
import textwrap

import pytest

gr = importlib.import_module("lifecycle_orchestrator.src.gauntlet_reader")


# --- gauntlet reader --------------------------------------------------------
def _universe(passing=True):
    return {
        "corrections": {"CPI_TIGHT": {"holm": {"passed_slugs": ["mes_secondwave"]}}},
        "robustness": {
            "dsr_by_cell": {"mes_secondwave": {"dsr": 0.8 if passing else -0.2}},
            "pbo": {"pbo": 0.2 if passing else 0.7},
            "bootstrap_by_cell": {"mes_secondwave": {"ci_lower": 1.5 if passing else -3.0}},
            "fee_stress_by_cell": {"mes_secondwave": {"fee_x2_pass": passing}},
        },
    }


def test_gauntlet_pass():
    v = gr.read_verdict(_universe(True), "mes_secondwave", event_type="CPI_TIGHT")
    assert v.passed is True and not v.reasons


def test_gauntlet_fail_collects_reasons():
    v = gr.read_verdict(_universe(False), "mes_secondwave", event_type="CPI_TIGHT")
    assert v.passed is False
    assert any("dsr" in r for r in v.reasons) and any("pbo" in r for r in v.reasons)


def test_gauntlet_missing_cell_fails_closed():
    v = gr.read_verdict({"robustness": {}}, "absent_slug")
    assert v.passed is False


# --- re-arm chain -----------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lc"))
    monkeypatch.setenv("HFT3_AUTONOMY_DIR", str(tmp_path / "auto"))
    monkeypatch.setenv("HFT3_AUTONOMY_CONFIG", str(tmp_path / "autonomy.yaml"))
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    rearm = importlib.import_module("lifecycle_orchestrator.src.rearm")
    lc = importlib.import_module("model_metrics.lifecycle")
    audit = importlib.import_module("autonomy.audit")
    return rearm, lc, audit, tmp_path, monkeypatch


def _enable_full_autonomy(tmp_path, mp):
    (tmp_path / "autonomy.yaml").write_text(textwrap.dedent("""
        enabled: true
        actions: {demote: true, retest: true, repromote: true, rearm: true}
        rearm: {allow_live: true}
    """), encoding="utf-8")
    mp.setenv("HFT3_AUTONOMY_ENABLED", "1")


def _walk_to_shadow(lc, mid="MES_X"):
    lc.apply_transition(mid, lc.CANDIDATE, trigger="r", reason="r", actor="t", create=True,
                        initial={"hypothesis_id": 1, "symbol": "MES"})
    for to in (lc.SCREENING, lc.GAUNTLET, lc.CERTIFIED, lc.SHADOW):
        lc.apply_transition(mid, to, trigger="r", reason="r", actor="t")


def _all_pass_ctx(rearm):
    return rearm.GateContext(
        cert_ok_override=True, gauntlet_passed=True, promotion_passed=True,
        defect_ledger_empty_override=True, shadow_passed=True, embargo_clean=True,
        determinism_ok=True, kill_drill_ok=True,
    )


def _write_options_spec(root, status):
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(textwrap.dedent(f"""
        # OPTIONS_LANE.md

        | ID | Component | Description | Status |
        |----|-----------|-------------|--------|
        | o-a | `vol_clock` | placeholder | {status} |
    """), encoding="utf-8")


def test_rearm_refused_when_autonomy_disabled(env):
    rearm, lc, audit, tmp, mp = env
    _walk_to_shadow(lc)
    # autonomy OFF (default) -> master_enable gate fails -> refused, NOT armed
    res = rearm.attempt_rearm("MES_X", _all_pass_ctx(rearm))
    assert res["armed"] is False
    assert "master_enable" in res["failed"]
    assert lc.get_record("MES_X").current_state == lc.SHADOW  # unchanged
    assert audit.verify_chain() is True


def test_rearm_refused_when_defect_ledger_open(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _walk_to_shadow(lc)
    ctx = _all_pass_ctx(rearm)
    ctx.defect_ledger_empty_override = False  # ledger has OPEN items (true today)
    res = rearm.attempt_rearm("MES_X", ctx)
    assert res["armed"] is False
    assert "defect_ledger_empty" in res["failed"]
    assert lc.get_record("MES_X").current_state == lc.SHADOW


def test_rearm_arms_when_all_gates_pass(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _walk_to_shadow(lc)
    res = rearm.attempt_rearm("MES_X", _all_pass_ctx(rearm))
    assert res["armed"] is True
    assert lc.get_record("MES_X").current_state == lc.LIVE
    # audit carries AUTO_GATE_EVAL + AUTO_ARM, chain intact
    types = {r["event_type"] for r in audit.tail(10)}
    assert "AUTO_ARM" in types and "AUTO_GATE_EVAL" in types
    assert audit.verify_chain() is True


def test_rearm_recovers_degraded_when_all_gates_pass(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _walk_to_shadow(lc)
    lc.apply_transition("MES_X", lc.LIVE, trigger="manual_arm", reason="r", actor="t")
    lc.apply_transition("MES_X", lc.DEGRADED, trigger="decay", reason="r", actor="t")

    res = rearm.attempt_rearm("MES_X", _all_pass_ctx(rearm))

    assert res["armed"] is True
    assert lc.get_record("MES_X").current_state == lc.LIVE
    assert any(r["event_type"] == "AUTO_ARM" for r in audit.tail(10))


def test_rearm_does_not_claim_arm_when_lifecycle_state_cannot_transition(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    lc.apply_transition(
        "MES_X",
        lc.CANDIDATE,
        trigger="register",
        reason="new candidate",
        actor="t",
        create=True,
        initial={"hypothesis_id": 1, "symbol": "MES"},
    )

    res = rearm.attempt_rearm("MES_X", _all_pass_ctx(rearm))

    assert res["armed"] is False
    assert "lifecycle_state" in res["failed"]
    assert lc.get_record("MES_X").current_state == lc.CANDIDATE
    assert not any(r["event_type"] == "AUTO_ARM" for r in audit.tail(10))


def test_fopt_rearm_refused_when_options_ledger_open_even_with_generic_override(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _write_options_spec(tmp, "**OPEN** - blocks shadow/live arm.")
    _walk_to_shadow(lc, "FOPT_ES_CALL")
    ctx = _all_pass_ctx(rearm)
    ctx.options_defect_ledger_root = tmp

    res = rearm.attempt_rearm("FOPT_ES_CALL", ctx)

    assert res["armed"] is False
    assert "defect_ledger_empty" in res["failed"]
    assert lc.get_record("FOPT_ES_CALL").current_state == lc.SHADOW


def test_fopt_rearm_refused_when_ledger_empty_but_profile_is_research_only(env):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _write_options_spec(tmp, "**FIXED**")
    _walk_to_shadow(lc, "FOPT_ES_CALL")
    ctx = _all_pass_ctx(rearm)
    ctx.options_defect_ledger_root = tmp

    res = rearm.attempt_rearm("FOPT_ES_CALL", ctx)

    assert res["armed"] is False
    assert "promotion_gate" in res["failed"]
    assert any(
        "research_only" in gate["detail"]
        for gate in res["gates"]
        if gate["name"] == "promotion_gate"
    )
    assert lc.get_record("FOPT_ES_CALL").current_state == lc.SHADOW


@pytest.mark.parametrize("model_id", ["OPTIONS_ES_CALL", "PARITY_ES_CALL"])
def test_legacy_options_rearm_refused_when_options_ledger_open(env, model_id):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _write_options_spec(tmp, "**OPEN** - blocks shadow/live arm.")
    _walk_to_shadow(lc, model_id)
    ctx = _all_pass_ctx(rearm)
    ctx.options_defect_ledger_root = tmp

    res = rearm.attempt_rearm(model_id, ctx)

    assert res["armed"] is False
    assert "defect_ledger_empty" in res["failed"]
    assert lc.get_record(model_id).current_state == lc.SHADOW


@pytest.mark.parametrize("model_id", ["OPTIONS_ES_CALL", "PARITY_ES_CALL"])
def test_legacy_options_rearm_refused_when_ledger_empty_but_canonical_profile_research_only(env, model_id):
    rearm, lc, audit, tmp, mp = env
    _enable_full_autonomy(tmp, mp)
    _write_options_spec(tmp, "**FIXED**")
    _walk_to_shadow(lc, model_id)
    ctx = _all_pass_ctx(rearm)
    ctx.options_defect_ledger_root = tmp

    res = rearm.attempt_rearm(model_id, ctx)

    assert res["armed"] is False
    assert "promotion_gate" in res["failed"]
    assert any(
        "research_only" in gate["detail"]
        for gate in res["gates"]
        if gate["name"] == "promotion_gate"
    )
    assert lc.get_record(model_id).current_state == lc.SHADOW


def test_real_defect_ledger_absent_fails_closed(env):
    rearm, lc, audit, tmp, mp = env
    # no override, point at a nonexistent ledger -> fail-closed (not empty)
    ok, detail = rearm.defect_ledger_empty(tmp / "nope.jsonl")
    assert ok is False and "absent" in detail


def test_defect_ledger_missing_status_is_open(env):
    rearm, lc, audit, tmp, mp = env
    led = tmp / "ledger.jsonl"
    led.write_text('{"id": "x"}\n', encoding="utf-8")  # no status field
    ok, _ = rearm.defect_ledger_empty(led)
    assert ok is False  # missing status -> fail-closed (OPEN)
    led.write_text('{"id": "x", "status": "CLOSED"}\n', encoding="utf-8")
    ok2, _ = rearm.defect_ledger_empty(led)
    assert ok2 is True


def test_defect_ledger_unknown_status_is_open(env):
    rearm, lc, audit, tmp, mp = env
    led = tmp / "ledger.jsonl"
    led.write_text('{"id": "x", "status": "PENDING_REVIEW"}\n', encoding="utf-8")

    ok, detail = rearm.defect_ledger_empty(led)

    assert ok is False
    assert "OPEN/unknown" in detail


def test_defect_ledger_unparseable_row_fails_closed(env):
    rearm, lc, audit, tmp, mp = env
    led = tmp / "ledger.jsonl"
    led.write_text('{"id": "x", "status": "CLOSED"}\nnot json\n', encoding="utf-8")
    ok, _ = rearm.defect_ledger_empty(led)
    assert ok is False


def test_cert_green_not_stale_requires_explicit_fresh_and_eligible(env):
    rearm, lc, audit, tmp, mp = env

    ok, detail = rearm.cert_green_not_stale({"latest_certification_status": "GREEN"})
    assert ok is False
    assert "eligible=None" in detail

    ok2, detail2 = rearm.cert_green_not_stale(
        {"latest_certification_status": "GREEN", "stale": False, "promotion_eligible": True}
    )
    assert ok2 is True
    assert "eligible=True" in detail2
