"""Tests for research_pipeline.ic_stats (EVENT_ALPHA_REBUILD_PLAN PR-1).

These tests are the calibration receipts for the IC diagnostic:
1. a planted signal-return relationship must be RECOVERED,
2. a null (independent) signal must NOT be promoted — including under
   cross-event correlation (the failure mode naive clustering misses),
3. per-row inflation must be killed (effective N = events),
4. BH and hurdle arithmetic must be exact.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_REPO), str(_REPO / "packages")]

from research_pipeline.ic_stats import (  # noqa: E402
    bh_reject,
    censoring_rate_per_event,
    clustered_t_two_way,
    hurdle_ticks,
    per_event_conditional_stats,
    spearman_ic_per_event,
)


def _synth_frame(
    rng: np.ndarray | np.random.Generator,
    *,
    n_events: int = 300,
    rows_per_event: int = 200,
    ic: float = 0.0,
    event_common_vol: float = 0.0,
) -> pd.DataFrame:
    """Synthetic per-row (signal, forward return) panel.

    ``ic`` plants a linear signal->return relationship. ``event_common_vol``
    adds a shared per-event return shock so events are internally correlated
    (each event contributes ONE effective observation, not rows_per_event).
    """
    rng = rng if isinstance(rng, np.random.Generator) else np.random.default_rng(rng)
    rows = []
    for e in range(n_events):
        year = 2021 + (e % 2)
        month = 1 + (e % 12)
        event_id = f"CORE_CPI_{year}_{month:02d}_{(e % 28) + 1:02d}_TIGHT"
        common = rng.normal(0.0, event_common_vol) if event_common_vol > 0 else 0.0
        sig = rng.normal(0.0, 1.0, rows_per_event)
        noise = rng.normal(0.0, 1.0, rows_per_event)
        ret = ic * sig + np.sqrt(max(0.0, 1.0 - ic * ic)) * noise + common
        rows.append(
            pd.DataFrame(
                {
                    "event_id": event_id,
                    "signal": sig,
                    "y_return_5000ms": ret,
                    "spread_ticks": 1.0,
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------
# 1. Planted-IC recovery
# ---------------------------------------------------------------------------

def test_planted_ic_recovered() -> None:
    frame = _synth_frame(np.random.default_rng(7), ic=0.3)
    ic_table = spearman_ic_per_event(
        frame, signal_col="signal", ret_col="y_return_5000ms"
    )
    mean_ic = float(ic_table["ic"].mean())
    # Spearman of a 0.3-correlated Gaussian pair ~= (6/pi) * asin(0.3/2) ~= 0.287
    assert abs(mean_ic - 0.287) < 0.05

    t_stat, p, dof = clustered_t_two_way(
        ic_table["ic"], ic_table["event_id"], ic_table["month"]
    )
    assert t_stat > 3.0
    assert p < 1e-3
    assert dof >= 1


def test_planted_conditional_edge_recovered() -> None:
    frame = _synth_frame(np.random.default_rng(11), ic=0.3)
    stats = per_event_conditional_stats(
        frame,
        signal_col="signal",
        ret_cols=["y_return_5000ms"],
        threshold=1.0,
        spread_col="spread_ticks",
    )
    fired = stats.table[stats.table["fired_floor_met"]]
    # E[sign(sig)*ret | |sig|>1] = ic * E[|sig| | |sig|>1] ~= 0.3 * 1.525 ~= 0.46
    mean_edge = float(fired["mean_signed__y_return_5000ms"].mean())
    assert abs(mean_edge - 0.46) < 0.08
    # spread column carried through
    assert float(fired["mean_spread_ticks"].mean()) == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# 2. Null control — including cross-event correlation
# ---------------------------------------------------------------------------

def test_zero_ic_null_no_bh_promotion() -> None:
    rng = np.random.default_rng(23)
    pvals = []
    for _ in range(27):  # 27 fake models
        frame = _synth_frame(rng, n_events=120, rows_per_event=80, ic=0.0,
                             event_common_vol=0.5)
        stats = per_event_conditional_stats(
            frame, signal_col="signal", ret_cols=["y_return_5000ms"], threshold=1.0
        )
        fired = stats.table[stats.table["fired_floor_met"]]
        _, p, _ = clustered_t_two_way(
            fired["mean_signed__y_return_5000ms"], fired["event_id"], fired["month"]
        )
        pvals.append(p)
    assert sum(bh_reject(pvals, q=0.10)) == 0


def test_cross_event_correlation_does_not_inflate_t() -> None:
    # Same signed return replicated across all rows of an event: per-row t
    # would explode; per-event clustered t must stay honest.
    rng = np.random.default_rng(5)
    n_events = 60
    rows = []
    for e in range(n_events):
        event_id = f"NFP_{2021 + e % 2}_{1 + e % 12:02d}_{(e % 28) + 1:02d}_TIGHT"
        common_ret = rng.normal(0.0, 1.0)  # one draw per event
        sig = np.abs(rng.normal(0.0, 1.0, 100)) + 1.5  # always fires, positive
        rows.append(pd.DataFrame({
            "event_id": event_id,
            "signal": sig,
            "y_return_5000ms": np.full(100, common_ret),
        }))
    frame = pd.concat(rows, ignore_index=True)
    stats = per_event_conditional_stats(
        frame, signal_col="signal", ret_cols=["y_return_5000ms"], threshold=1.0
    )
    fired = stats.table[stats.table["fired_floor_met"]]
    t_stat, p, dof = clustered_t_two_way(
        fired["mean_signed__y_return_5000ms"], fired["event_id"], fired["month"]
    )
    # 60 independent event draws of N(0,1): |t| should look like a t-stat on
    # ~60 obs of a zero-mean variable — far from the per-row t (~sqrt(6000)x).
    assert abs(t_stat) < 3.0
    assert dof <= 24  # months bound the dof, not rows


# ---------------------------------------------------------------------------
# 3. Mechanics: BH, hurdle, censoring, month parsing
# ---------------------------------------------------------------------------

def test_bh_arithmetic_hand_example() -> None:
    # p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205], n=8.
    # BH-adjusted (right-cummin of n/j*p(j)):
    #   [0.008, 0.032, 0.0672, 0.0672, 0.0672, 0.08, 0.08457, 0.205]
    # q=0.05 -> reject first 2; q=0.07 -> reject first 5 (0.0672<=0.07<0.08).
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
    assert bh_reject(ps, q=0.05) == [True, True, False, False, False, False, False, False]
    assert bh_reject(ps, q=0.07) == [True, True, True, True, True, False, False, False]


def test_bh_nan_never_rejected() -> None:
    mask = bh_reject([0.0001, float("nan"), 0.0002], q=0.10)
    assert mask == [True, False, True]


def test_bh_nan_preserves_family_size() -> None:
    # A no-verdict (NaN) model must still count in the BH denominator.
    # p=0.04 alone at q=0.05 rejects (adjusted 0.04); in a family of 2 the
    # adjusted value is min(1.0, 0.04*2/1) = 0.08 > 0.05 -> no rejection.
    # Dropping the NaN would shrink the family and falsely promote it.
    assert bh_reject([0.04], q=0.05) == [True]
    assert bh_reject([0.04, float("nan")], q=0.05) == [False, False]


def test_hurdle_arithmetic_exact() -> None:
    mes = hurdle_ticks(fee_per_side=0.52, contract_multiplier=5.0, tick_size=0.25)
    assert mes["fee_points"] == pytest.approx(0.208)
    assert mes["fee_ticks"] == pytest.approx(0.832)
    assert mes["hurdle_ticks"] == pytest.approx(1.832)

    es = hurdle_ticks(fee_per_side=1.52, contract_multiplier=50.0, tick_size=0.25)
    assert es["fee_points"] == pytest.approx(0.0608)
    assert es["hurdle_ticks"] == pytest.approx(1.2432)

    with pytest.raises(ValueError):
        hurdle_ticks(fee_per_side=0.52, contract_multiplier=0.0, tick_size=0.25)


def test_censoring_rate_per_event() -> None:
    frame = pd.DataFrame(
        {
            "event_id": ["FOMC_2021_03_17_TIGHT"] * 4 + ["FOMC_2021_06_16_TIGHT"] * 4,
            "signal": [2.0, 2.0, 2.0, 0.1, 2.0, 2.0, 2.0, 2.0],
            "y_return_5000ms": [1.0, np.nan, np.nan, np.nan, 1.0, 1.0, 1.0, 1.0],
        }
    )
    out = censoring_rate_per_event(
        frame, ret_col="y_return_5000ms", signal_col="signal", threshold=1.0
    )
    first = out[out["event_id"] == "FOMC_2021_03_17_TIGHT"].iloc[0]
    # 3 fired rows, 2 censored (the 0.1-signal NaN row does not count)
    assert first["n_fired"] == 3
    assert first["n_censored"] == 2
    assert first["censoring_rate"] == pytest.approx(2 / 3)
    second = out[out["event_id"] == "FOMC_2021_06_16_TIGHT"].iloc[0]
    assert second["censoring_rate"] == pytest.approx(0.0)


def test_unparseable_event_id_fails_closed() -> None:
    frame = pd.DataFrame(
        {"event_id": ["NO_DATE_HERE"] * 12, "signal": [2.0] * 12,
         "y_return_5000ms": [0.5] * 12}
    )
    with pytest.raises(ValueError, match="unparseable_event_id_for_month"):
        per_event_conditional_stats(
            frame, signal_col="signal", ret_cols=["y_return_5000ms"], threshold=1.0
        )


def test_fired_floor_visible_not_silent() -> None:
    frame = _synth_frame(np.random.default_rng(3), n_events=10, rows_per_event=30, ic=0.0)
    stats = per_event_conditional_stats(
        frame, signal_col="signal", ret_cols=["y_return_5000ms"],
        threshold=10.0,  # nothing fires
    )
    assert stats.n_events_total == 10
    assert stats.n_events_fired == 0
    assert (~stats.table["fired_floor_met"]).all()
    # events remain in the table — coverage visible, no silent drop
    assert len(stats.table) == 10
