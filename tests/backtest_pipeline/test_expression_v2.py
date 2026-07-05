"""Execution expression v2 (PR-3): vol-scaled tick barriers + entry hurdle gate.

Covers, against the deterministic exit-leg fake engine:
- PIT correctness: a planted vol change AFTER the entry fill never moves the
  frozen barriers;
- warmup skip counter;
- clamp floor and cap;
- barrier_units_conflict fail-closed receipt;
- legacy byte-parity golden (receipt hash identical to the pre-change code);
- hurdle skip counter;
- strategy surface version bump.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline
from test_hftbacktest_only_pipeline import _config, _install_exit_leg_fake_hftbacktest

TICK = 0.25


def _run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, params: dict, mids: list[float]):
    _install_exit_leg_fake_hftbacktest(monkeypatch, mids=mids)
    config = _config(
        tmp_path,
        tmp_path / "dummy_data.npz",
        tmp_path / "dummy_snapshot.npz",
        strategy_params=params,
    )
    return pipeline._run_minimal_strategy(config)


def _expected_barrier(deltas_ticks: list[float], holding_steps: int, mult: float,
                      floor: float = 2.0, cap: float = 40.0) -> tuple[float, float]:
    """Replicates the PIT EWMA (lambda=0.97) + clamp used by the pipeline."""
    var = 0.0
    lam = pipeline._VOL_EWMA_LAMBDA
    for d in deltas_ticks:
        var = lam * var + (1.0 - lam) * d * d
    sigma_step = math.sqrt(var)
    sigma_h = sigma_step * math.sqrt(float(holding_steps))
    return sigma_step, min(max(mult * sigma_h, floor), cap)


def _vol_params(**overrides) -> dict:
    params = {
        "side": "BUY",
        "quantity": 1.0,
        "max_steps": 10,
        "holding_period_bars": 4,
        "pt_vol_mult": 1.0,
        "sl_vol_mult": 1.0,
        "vol_warmup_steps": 2,
    }
    params.update(overrides)
    return params


def test_vol_barriers_pit_frozen_at_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-entry tape identical (8-tick swings); post-entry tapes diverge
    # violently. Barriers are frozen at the entry fill (step 2), so both runs
    # must carry the SAME barrier receipt fields.
    pre = [5000.0, 5002.0, 5000.0]
    calm_post = [5000.0] * 7
    wild_post = [5010.0, 4990.0, 5010.0, 4990.0, 5010.0, 4990.0, 5010.0]
    replay_calm, _ = _run(monkeypatch, tmp_path / "a", _vol_params(), pre + calm_post)
    replay_wild, _ = _run(monkeypatch, tmp_path / "b", _vol_params(), pre + wild_post)

    # Entry submitted at step 2 (after 2 warmup skips) and filled there.
    sigma_step, expected_barrier = _expected_barrier([8.0, -8.0], holding_steps=4, mult=1.0)
    assert 2.0 < expected_barrier < 40.0  # unclamped: the test is about sigma, not the clamp
    for replay in (replay_calm, replay_wild):
        assert replay["sigma_step_at_entry"] == pytest.approx(sigma_step)
        assert replay["barrier_pt_ticks"] == pytest.approx(expected_barrier)
        assert replay["barrier_sl_ticks"] == pytest.approx(expected_barrier)
    # The planted post-entry vol explosion changed the exit path (barrier
    # touch vs holding expiry) but NOT the frozen barriers.
    assert replay_calm["exit_reason"] == "max_holding"
    assert replay_wild["exit_reason"] == "take_profit"
    assert replay_wild["barrier_pt_ticks"] == replay_calm["barrier_pt_ticks"]
    assert replay_wild["sigma_step_at_entry"] == replay_calm["sigma_step_at_entry"]


def test_vol_warmup_skips_entries_and_counts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        _vol_params(vol_warmup_steps=3),
        [5000.0, 5000.25, 5000.0, 5000.25] + [5000.0] * 8,
    )
    assert reasons == []
    # Steps 0..2 have < 3 EWMA observations: three skipped entries, the
    # order goes out on step 3.
    assert replay["vol_warmup_skipped_entries"] == 3
    entry_events = [o for o in replay["orders"] if o["event_type"] == "ORDER_SUBMITTED"]
    assert entry_events[0]["step"] == 3
    assert replay["vol_warmup_steps"] == 3


def test_vol_barrier_clamp_floor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Perfectly flat pre-entry tape: sigma_step == 0 -> both barriers sit on
    # the min_barrier_ticks floor.
    replay, reasons = _run(monkeypatch, tmp_path, _vol_params(), [5000.0] * 10)
    assert reasons == []
    assert replay["sigma_step_at_entry"] == 0.0
    assert replay["barrier_pt_ticks"] == 2.0
    assert replay["barrier_sl_ticks"] == 2.0


def test_vol_barrier_clamp_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 8-tick swings with a 10x multiplier blow far past the configured cap.
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        _vol_params(pt_vol_mult=10.0, sl_vol_mult=10.0, max_barrier_ticks=5.0),
        [5000.0, 5002.0, 5000.0] + [5000.0] * 7,
    )
    assert reasons == []
    sigma_step, _ = _expected_barrier([8.0, -8.0], holding_steps=4, mult=10.0)
    assert 10.0 * sigma_step * math.sqrt(4.0) > 5.0
    assert replay["barrier_pt_ticks"] == 5.0
    assert replay["barrier_sl_ticks"] == 5.0


def test_barrier_units_conflict_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Percent barriers AND vol barriers on the same run: fail closed before
    # any order can queue, mirroring the existing blocker-receipt style.
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        _vol_params(stop_loss_pct=0.1),
        [5000.0] * 6,
    )
    assert reasons == ["barrier_units_conflict"]
    assert replay["official_hftbacktest_replay_status"] == "not_run"
    assert replay["fail_closed_reasons"] == ["barrier_units_conflict"]
    assert replay["orders"] == []
    assert replay["orders_submitted"] == 0


# sha256 of json.dumps(replay, sort_keys=True) captured on the PRE-change
# legacy path (origin/main, commit f86c5fe2) with the same fake engine and
# params. The legacy surface must stay byte-identical.
_LEGACY_GOLDEN = {
    "legacy_stop_loss": (
        {
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "stop_loss_pct": 0.1,
            "take_profit_pct": 0.3,
            "holding_period_bars": 50,
        },
        [5000.0, 5000.0, 4985.0, 4985.0, 4985.0, 4985.0],
        "3a8caeffef6392e8bfa8f3502bc109effbf91530fb707012093e5661594af71f",
    ),
    "legacy_hold_only": (
        {"side": "BUY", "quantity": 1.0, "max_steps": 2},
        [5000.0] * 6,
        "a0c390953040d223d27a8ff3c4082ad847ab33e02056e6a557588807d397c18e",
    ),
}


@pytest.mark.parametrize("case", sorted(_LEGACY_GOLDEN))
def test_legacy_path_byte_identical_golden(
    case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    params, mids, expected_sha = _LEGACY_GOLDEN[case]
    replay, _ = _run(monkeypatch, tmp_path, dict(params), list(mids))
    assert "barrier_pt_ticks" not in replay
    assert "hurdle_skipped_entries" not in replay
    blob = json.dumps(replay, sort_keys=True)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == expected_sha


def test_hurdle_gate_skips_cross_spread_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Fake book is always 1 tick wide (bid = mid-0.125, ask = mid+0.125).
    # Crossing costs 1 tick >= hurdle of 1 tick -> every entry opportunity
    # is skipped and counted; no order is ever queued.
    mids = [5000.0] * 4
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        {
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 10,
            "price_mode": "cross_spread",
            "entry_hurdle_ticks": 1.0,
        },
        mids,
    )
    assert replay["hurdle_skipped_entries"] == len(mids)
    assert replay["orders_submitted"] == 0
    assert "pipeline_blocker:no_hbt_order_submitted" in reasons


def test_hurdle_gate_passes_when_hurdle_clears_spread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Configured hurdle strictly above the 1-tick live spread cost: entry
    # proceeds and the observed spread is receipted.
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        {
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "price_mode": "cross_spread",
            "entry_hurdle_ticks": 1.5,
        },
        [5000.0] * 6,
    )
    assert reasons == []
    assert replay["hurdle_skipped_entries"] == 0
    assert replay["entry_hurdle_ticks"] == 1.5
    assert replay["entry_spread_ticks"] == pytest.approx(1.0)
    assert replay["orders_submitted"] == 1


def test_hurdle_gate_is_noop_for_passive_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Passive entries pay no spread at entry: spread cost is 0.0 and the
    # gate never trips, by documented design.
    replay, reasons = _run(
        monkeypatch,
        tmp_path,
        {"side": "BUY", "quantity": 1.0, "max_steps": 2, "entry_hurdle_ticks": 0.5},
        [5000.0] * 6,
    )
    assert reasons == []
    assert replay["hurdle_skipped_entries"] == 0
    assert replay["entry_spread_ticks"] == 0.0
    assert replay["orders_submitted"] == 1


def test_surface_version_bumps_for_expression_params() -> None:
    # New surface: stale resume receipts written by v2/v3/v4 must invalidate.
    assert (
        pipeline._strategy_surface_version("hypothesis_limit_order", {"pt_vol_mult": 1.5})
        == "hypothesis_limit_order_event_scan_v5_expression"
    )
    assert (
        pipeline._strategy_surface_version("hypothesis_limit_order", {"entry_hurdle_ticks": 2.0})
        == "hypothesis_limit_order_event_scan_v5_expression"
    )
    assert (
        pipeline._strategy_surface_version("smoke_limit_order", {"sl_vol_mult": 2.0})
        == "smoke_limit_order_event_scan_v5_expression"
    )
    # Legacy param sets keep their existing surface strings.
    assert (
        pipeline._strategy_surface_version("hypothesis_limit_order", {"stop_loss_pct": 0.1})
        == "hypothesis_limit_order_event_scan_v3_exit_leg"
    )
    assert (
        pipeline._strategy_surface_version("hypothesis_limit_order", {})
        == "hypothesis_limit_order_event_scan_v2"
    )
    assert (
        pipeline._strategy_surface_version("smoke_limit_order", {"take_profit_pct": 0.2})
        == "smoke_limit_order_exit_leg_v2"
    )
