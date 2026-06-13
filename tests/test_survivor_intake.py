"""WS-B3 — survivor intake mints CERTIFIED models from a universe sweep.

Covers: gauntlet-pass cell -> CERTIFIED record w/ frozen envelope + cell metadata;
gauntlet-fail cell skipped; idempotent re-run; cell metadata feeds the re-screen
route; the gauntlet_reader cell_slug ({slug}_{event_type}) robustness lookup.
"""
import importlib
import json

import pytest

_SLUG = "hyp_1_band_6.255764"
_CELL_SLUG = f"{_SLUG}_CPI"
_BAD_SLUG = "hyp_2_band_6.255764"
_BAD_CELL_SLUG = f"{_BAD_SLUG}_NFP"


def _universe() -> dict:
    """One CPI survivor that passes the full gauntlet, one NFP survivor that fails
    it (negative DSR). Robustness blocks are keyed by cell_slug, Holm by bare slug —
    the real run_event_universe schema."""
    return {
        "schema": "universe_result_v1",
        "corrections": {
            "CPI": {"holm": {"passed_slugs": [_SLUG], "total_tested": 4}},
            "NFP": {"holm": {"passed_slugs": [_BAD_SLUG], "total_tested": 4}},
        },
        # real run_event_universe / robustness_producers field names:
        # dsr {sharpe,dsr_cdf,dsr_pass}, bootstrap {mean,ci_lo_95,ci_hi_95}, fee {fee_x2_pass}
        "robustness": {
            "dsr_by_cell": {_CELL_SLUG: {"sharpe": 1.4, "dsr_cdf": 0.98, "dsr_pass": True},
                            _BAD_CELL_SLUG: {"sharpe": -1.2, "dsr_cdf": 0.0, "dsr_pass": False}},
            "bootstrap_by_cell": {_CELL_SLUG: {"mean": 14.0, "ci_lo_95": 2.0, "ci_hi_95": 26.0},
                                  _BAD_CELL_SLUG: {"mean": -3.0, "ci_lo_95": -5.0, "ci_hi_95": -1.0}},
            "fee_stress_by_cell": {_CELL_SLUG: {"fee_x2_pass": True}, _BAD_CELL_SLUG: {"fee_x2_pass": False}},
            "pbo": {"pbo": 0.12},
        },
        "aggregated": {
            "1": {"CPI": {"6.255764": {
                "hypothesis_id": 1, "hypothesis_name": "OFI_PRESSURE",
                "mean_expectancy_usd": 14.0, "mean_win_rate": 0.57, "total_trades": 180,
                "n_events": 22, "per_event_expectancies": [12.0, 16.0, 14.0, 13.5, 14.5],
            }}},
            "2": {"NFP": {"6.255764": {
                "hypothesis_id": 2, "hypothesis_name": "BAD_ONE",
                "mean_expectancy_usd": -3.0, "mean_win_rate": 0.40, "total_trades": 90,
                "n_events": 18, "per_event_expectancies": [-2.0, -4.0, -3.0],
            }}},
        },
    }


@pytest.fixture()
def mod(tmp_path, monkeypatch):
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(tmp_path / "lifecycle"))
    si = importlib.import_module("lifecycle_orchestrator.src.survivor_intake")
    lc = importlib.import_module("model_metrics.lifecycle")
    importlib.reload(lc)
    up = tmp_path / "universe_result.json"
    up.write_text(json.dumps(_universe()), encoding="utf-8")
    return si, lc, up


def test_gauntlet_reader_uses_cell_slug(mod):
    gr = importlib.import_module("lifecycle_orchestrator.src.gauntlet_reader")
    uni = _universe()
    good = gr.read_verdict(uni, _SLUG, event_type="CPI")
    assert good.passed is True, good.reasons
    assert good.dsr == 0.98 and good.ci_lower == 2.0 and good.fee_x2_pass is True
    bad = gr.read_verdict(uni, _BAD_SLUG, event_type="NFP")
    assert bad.passed is False


def test_intake_mints_passing_survivor_only(mod):
    si, lc, up = mod
    summary = si.intake_survivors(up)
    assert summary["n_minted"] == 1, summary
    minted = summary["minted"][0]
    assert minted["event_type"] == "CPI"
    assert minted["state"] == lc.CERTIFIED
    # the NFP cell failed the gauntlet -> skipped, not minted
    assert any(s["event_type"] == "NFP" and "gauntlet fail" in s["reason"] for s in summary["skipped"])
    # registry now holds exactly the CPI model, CERTIFIED, with cell metadata
    reg = lc.load_registry()
    lid = minted["lifecycle_id"]
    assert lid in reg and reg[lid].current_state == lc.CERTIFIED
    cell = reg[lid].research_card_links.get("cell")
    assert cell and cell["hyp_id"] == 1 and cell["event_type"] == "CPI"
    assert reg[lid].current_envelope_id  # frozen envelope linked
    assert lc.verify_chain() is True


def test_intake_is_idempotent(mod):
    si, lc, up = mod
    first = si.intake_survivors(up)
    assert first["n_minted"] == 1
    second = si.intake_survivors(up)
    assert second["n_minted"] == 0
    assert any(s.get("reason") == "already registered" for s in second["skipped"])
    assert len(lc.load_registry()) == 1


def test_cell_metadata_feeds_rescreen_route(mod):
    si, lc, up = mod
    si.intake_survivors(up)
    routes = importlib.import_module("lifecycle_orchestrator.src.routes")
    lid = next(iter(lc.load_registry()))
    rec = lc.get_record(lid)
    cmd = routes._materialize_rescreen(rec, "param_rescreen")
    # cell present -> a real materialized command, never the <bridge_stub> placeholder
    assert "<bridge_stub>" not in cmd["args"]
    assert cmd["entry"].endswith("run_event_universe.py")
