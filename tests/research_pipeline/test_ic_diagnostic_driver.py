"""Tests for scripts/build_ic_diagnostic.py (PR-1 driver).

Proves the gates are code, not convention:
- HOLDOUT seal: 2023+ events are refused with a receipt;
- pre-registration gate: an uncommitted/dirty horizon map refuses to run;
- kill-list schema: only primary-family fields (regression lock);
- multi-adapter single-pass parity: the 27x refactor changed cost, not
  semantics (sig__<m> == build_meta_training_set primary_signal);
- end-to-end smoke on a synthetic tape.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_REPO), str(_REPO / "packages"), str(_REPO / "apps")]


def _load_driver():
    script = _REPO / "scripts" / "build_ic_diagnostic.py"
    spec = importlib.util.spec_from_file_location("build_ic_diagnostic", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _event_contract():
    from features_engine.src.features.npz_feed import EVENT_DTYPE, EVENT_FLAG_CONSTANTS

    return EVENT_DTYPE, EVENT_FLAG_CONSTANTS


def _write_synthetic_tape(path: Path, *, n_rows: int = 240, base_ts: int = 1_000_000_000) -> Path:
    """Alternating bid/ask adds + trades walking the mid — enough rows for
    the driver's floor and non-degenerate signals."""
    from tests.backtest_pipeline.test_hftbacktest_only_pipeline import _write_valid_l3_npz  # noqa: F401

    dtype, constants = _event_contract_via_pipeline()
    rows = []
    px = 5000.0
    order_id = 1000
    for i in range(n_rows):
        drift = 0.25 if (i // 20) % 2 == 0 else -0.25
        px = max(4900.0, px + (drift if i % 4 == 0 else 0.0))
        side_buy = i % 2 == 0
        ev = constants["ADD_ORDER_EVENT"] | (
            constants["BUY_EVENT"] if side_buy else constants["SELL_EVENT"]
        )
        ts = base_ts + i * 100_000_000  # 100ms apart -> 24s tape
        rows.append((ev | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
                     ts, ts + 100, px if side_buy else px + 0.25, 1.0, order_id, 0, 0.0))
        order_id += 1
        if i % 5 == 0:
            rows.append((constants["TRADE_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
                         ts + 50, ts + 150, px + 0.25, 1.0, order_id - 1, 0, 0.0))
    events = np.zeros(len(rows), dtype=dtype)
    for idx, row in enumerate(rows):
        for field, value in zip(("ev", "exch_ts", "local_ts", "px", "qty", "order_id", "ival", "fval"), row):
            events[idx][field] = value
    np.savez_compressed(path, data=events, timestamp_units="nanoseconds")
    return path


def _event_contract_via_pipeline():
    from tests.backtest_pipeline.test_hftbacktest_only_pipeline import _event_contract

    return _event_contract()


# ---------------------------------------------------------------------------
# Gates
# ---------------------------------------------------------------------------

def test_holdout_seal_excludes_2023_events(tmp_path: Path) -> None:
    driver = _load_driver()
    manifest = tmp_path / "m.jsonl"
    rows = [
        {"source_npz": "/x/a.npz", "symbol": "MES.v.0",
         "event_id": "CORE_CPI_2021_05_12_TIGHT"},
        {"source_npz": "/x/b.npz", "symbol": "MES.v.0",
         "event_id": "CORE_CPI_2023_05_10_TIGHT"},   # HOLDOUT — must be refused
        {"source_npz": "/x/c.npz", "symbol": "MES.v.0",
         "event_id": "CORE_CPI_2024_01_11_TIGHT"},   # HOLDOUT
    ]
    manifest.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    units, excluded = driver._load_units(manifest)
    assert [u["event_id"] for u in units] == ["CORE_CPI_2021_05_12_TIGHT"]
    assert excluded == ["CORE_CPI_2023_05_10_TIGHT", "CORE_CPI_2024_01_11_TIGHT"]


def test_unparseable_event_year_hard_fails(tmp_path: Path) -> None:
    driver = _load_driver()
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(json.dumps({"source_npz": "/x/a.npz", "symbol": "MES.v.0",
                                    "event_id": "NO_DATE_EVENT"}) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unparseable_event_year"):
        driver._load_units(manifest)


def test_horizon_map_gate_refuses_untracked(tmp_path: Path) -> None:
    driver = _load_driver()
    # a map inside the repo tree but NOT git-tracked
    rogue = _REPO / "runtime" / "_test_rogue_horizon_map.json"
    rogue.write_text("{}", encoding="utf-8")
    try:
        with pytest.raises(SystemExit, match="horizon_map_not_committed"):
            driver._require_committed_horizon_map(rogue)
    finally:
        rogue.unlink(missing_ok=True)


def test_horizon_map_gate_accepts_committed_map() -> None:
    driver = _load_driver()
    committed = _REPO / "docs" / "hypotheses" / "HORIZON_MAP_PREREGISTERED.json"
    blob = driver._require_committed_horizon_map(committed)
    assert len(blob) == 40  # sha1 blob id


def test_kill_list_schema_regression_lock() -> None:
    driver = _load_driver()
    assert driver.KILL_LIST_ALLOWED_FIELDS == frozenset(
        {
            "verdict", "h_star_ms", "threshold", "edge_ticks",
            "spread_adjusted_edge_ticks", "hurdle_fee_ticks", "pass_line_ticks",
            "p_raw", "bh_pass", "dsr", "n_events", "n_events_censor_excluded",
            "sigma_k_median", "alpha_class",
        }
    )
    # no exploratory-grid vocabulary may enter the schema
    for banned in ("event_type", "vol_regime", "by_h", "exploratory"):
        assert banned not in driver.KILL_LIST_ALLOWED_FIELDS


# ---------------------------------------------------------------------------
# Parity: single-pass multi-adapter == per-model reference implementation
# ---------------------------------------------------------------------------

def test_multi_adapter_single_pass_parity(tmp_path: Path) -> None:
    driver = _load_driver()
    tape = _write_synthetic_tape(tmp_path / "tape.npz")

    unit = {"source_npz": str(tape), "symbol": "MES.v.0",
            "event_id": "CORE_CPI_2021_05_12_TIGHT", "sensor_feature_npz": {}}
    model_ids = ["SECOND_WAVE_CONTINUATION", "ABSORPTION_FADE"]
    frame, tick_size = driver._extract_signal_frame(unit, model_ids)
    assert tick_size == 0.25
    assert len(frame) > 200

    # reference: the per-model pass used by build_meta_training_set
    meta_script = _REPO / "scripts" / "build_meta_training_set.py"
    spec = importlib.util.spec_from_file_location("build_meta_training_set", meta_script)
    meta = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(meta)

    for mid in model_ids:
        ref = meta._signal_and_features(mid, str(tape), 0.25)
        got = frame[f"sig__{mid}"].to_numpy(dtype=np.float64)
        want = ref["primary_signal"].to_numpy(dtype=np.float64)
        assert len(got) == len(want)
        np.testing.assert_allclose(got, want, rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# End-to-end smoke
# ---------------------------------------------------------------------------

def test_driver_end_to_end_smoke(tmp_path: Path) -> None:
    driver = _load_driver()
    tape = _write_synthetic_tape(tmp_path / "tape.npz")
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        json.dumps({"source_npz": str(tape), "symbol": "MES.v.0",
                    "event_id": "CORE_CPI_2021_05_12_TIGHT"}) + "\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    rc = driver.main([
        "--campaign-manifest", str(manifest),
        "--out-dir", str(out),
        "--workers", "1",
    ])
    assert rc == 0
    report = json.loads((out / "ic_report.json").read_text(encoding="utf-8"))
    kill = json.loads((out / "kill_list.json").read_text(encoding="utf-8"))
    assert report["units_processed"] == 1
    assert report["holdout_excluded_events"] == []
    assert kill["schema_version"] == "hft3_ic_kill_list_v1"
    models = kill["models"]
    assert "SECOND_WAVE_CONTINUATION" in models
    # one event << MIN_EVENTS -> never a pass/fail verdict on a smoke run
    for entry in models.values():
        assert entry["verdict"] not in ("pass",)
        assert set(entry) <= driver.KILL_LIST_ALLOWED_FIELDS


def test_sharpe_and_dsr_branch_call_shape() -> None:
    # Greptile P1 (PR #75): deflated_sharpe_ratio takes an observed-Sharpe
    # float + n_obs/n_trials kwargs; the MIN_EVENTS branch previously passed
    # the raw edge list and crashed. Exercise the exact helper with a
    # 45-event edge series (the smallest realistic inference input).
    driver = _load_driver()
    rng = np.random.default_rng(9)
    edges = rng.normal(0.3, 1.0, 45)
    sr, dsr = driver._sharpe_and_dsr(edges, n_trials=12)
    assert np.isfinite(sr) and np.isfinite(dsr)
    assert 0.0 <= dsr <= 1.0  # DSR is a CDF value
    # degenerate inputs stay NaN, never raise
    sr2, dsr2 = driver._sharpe_and_dsr(np.array([0.5]), n_trials=3)
    assert sr2 != sr2 and dsr2 != dsr2  # NaN, no exception
    # constant edges (zero variance) must also degrade to NaN, never raise
    sr3, dsr3 = driver._sharpe_and_dsr(np.full(45, 0.7), n_trials=12)
    assert sr3 != sr3 and dsr3 != dsr3
