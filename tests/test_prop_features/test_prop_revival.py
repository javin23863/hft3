"""
PC3 + PC4 revival tests for the prop-cohort hypothesis family.

Covers:
  - micro_leader_divergence() and prop_cohort_active() helpers
  - HYP 20 MicroContractRetailLag (repointed off institutional_flow_score)
  - HYP 30 CutoffPanicExits (context repoint to PROP_FLATTEN_TOPSTEP / FRIDAY_CLOSE)
  - HYP 32 DailyLossLimitDefense (no longer constant-zero)
  - HYP 38 EconomicEventRestrictionFlattening (_TIGHT gate for NEWS_RESTRICTION)

MarketState is constructed directly from its dataclass fields — no fixture
files or database access required.  PYTHONPATH-tolerant via the repo-root
bootstrap at the bottom of the module guard.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# PYTHONPATH bootstrap — runnable from any working directory or via pytest
# from the repo root without an installed package.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# hft3_bootstrap wires packages/ and apps/ onto sys.path.
try:
    from hft3_bootstrap import setup_repo_paths
    setup_repo_paths()
except ImportError:
    # Fallback: add packages/ manually so imports work in isolated envs.
    _PKG = _REPO_ROOT / "packages"
    if _PKG.is_dir() and str(_PKG) not in sys.path:
        sys.path.insert(0, str(_PKG))

from features_engine.src.hypotheses.modules import (
    MarketState,
    micro_leader_divergence,
    prop_cohort_active,
    MicroContractRetailLag,
    CutoffPanicExits,
    DailyLossLimitDefense,
    EconomicEventRestrictionFlattening,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _state(
    primary: dict | None = None,
    cross_asset: dict | None = None,
    regime: str = "NORMAL",
    context: str = "NORMAL",
    volatility: str = "NORMAL",
    liquidity: str = "NORMAL",
    feature_vector: np.ndarray | None = None,
) -> MarketState:
    """Build a minimal MarketState for testing."""
    return MarketState(
        primary_features=primary or {},
        cross_asset_features=cross_asset or {},
        regime_state=regime,
        event_context=context,
        volatility_state=volatility,
        liquidity_state=liquidity,
        latency_ms=0.0,
        current_inventory=0,
        feature_vector=feature_vector,
    )


def _state_with_fvec(slot_dict: dict[int, float], n_slots: int = 64) -> MarketState:
    """Build a MarketState where .f() reads from feature_vector."""
    vec = np.zeros(n_slots)
    for idx, val in slot_dict.items():
        vec[idx] = val
    return _state(feature_vector=vec)


# ---------------------------------------------------------------------------
# Suite 1: micro_leader_divergence and prop_cohort_active
# ---------------------------------------------------------------------------

class TestMicroLeaderDivergence:
    """Unit tests for the module-level helper functions."""

    def test_zero_when_no_es_leg(self):
        """Without a cross-asset ES entry the divergence must be zero."""
        state = _state(primary={'aggressor_volume_imbalance': 0.5})
        assert micro_leader_divergence(state) == pytest.approx(0.0)

    def test_zero_when_both_legs_equal(self):
        """Micro == leader → divergence zero."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.4},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.4}},
        )
        assert micro_leader_divergence(state) == pytest.approx(0.0)

    def test_positive_when_micro_more_bullish(self):
        """micro > leader → positive divergence."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.7},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.2}},
        )
        div = micro_leader_divergence(state)
        assert div == pytest.approx(0.5)
        assert div > 0.0

    def test_negative_when_micro_more_bearish(self):
        """micro < leader → negative divergence."""
        state = _state(
            primary={'aggressor_volume_imbalance': -0.6},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.1}},
        )
        div = micro_leader_divergence(state)
        assert div == pytest.approx(-0.7)
        assert div < 0.0

    def test_custom_leader_key(self):
        """custom leader key is used when provided."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.5},
            cross_asset={'NQ': {'aggressor_volume_imbalance': 0.3}},
        )
        div = micro_leader_divergence(state, leader='NQ')
        assert div == pytest.approx(0.2)

    def test_prop_cohort_active_false_below_threshold(self):
        """Small divergence (< 0.3 default) → cohort NOT active."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.1},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.0}},
        )
        assert prop_cohort_active(state) is False

    def test_prop_cohort_active_true_above_threshold(self):
        """Large divergence (> 0.3 default) → cohort IS active."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.8},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.2}},
        )
        # divergence = 0.6 > 0.3
        assert prop_cohort_active(state) is True

    def test_prop_cohort_active_false_when_no_es_leg(self):
        """No ES leg → divergence is zero → cohort inactive regardless of micro."""
        state = _state(primary={'aggressor_volume_imbalance': 0.9})
        assert prop_cohort_active(state) is False

    def test_prop_cohort_active_threshold_boundary(self):
        """Exactly at threshold → inactive (> not >=)."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.3},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.0}},
        )
        assert prop_cohort_active(state) is False

    def test_prop_cohort_active_custom_threshold(self):
        """Custom threshold respected."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.5},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.1}},
        )
        # divergence = 0.4; active with threshold=0.3, inactive with threshold=0.5
        assert prop_cohort_active(state, threshold=0.3) is True
        assert prop_cohort_active(state, threshold=0.5) is False


# ---------------------------------------------------------------------------
# Suite 2: HYP 20 — MicroContractRetailLag
# ---------------------------------------------------------------------------

class TestMicroContractRetailLag:
    """HYP 20: institutional_flow_score replaced with ES aggressor flow."""

    def setup_method(self):
        self.hyp = MicroContractRetailLag()

    def test_zero_when_no_es_leg(self):
        """No cross-asset ES → single-symbol replay → return 0."""
        state = _state(primary={'aggressor_volume_imbalance': 0.5})
        assert self.hyp.evaluate(state) == pytest.approx(0.0)

    def test_zero_when_es_imb_zero(self):
        """ES imbalance zero → leader hasn't moved → no lag signal."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.0},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.0}},
        )
        assert self.hyp.evaluate(state) == pytest.approx(0.0)

    def test_positive_in_leader_direction_when_micro_not_yet_diverged(self):
        """
        ES bullish, micro neutral (not yet caught up) → positive signal
        (follow the leader).
        """
        state = _state(
            primary={'aggressor_volume_imbalance': 0.0},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.8}},
        )
        signal = self.hyp.evaluate(state)
        assert signal > 0.0, f"Expected positive signal, got {signal}"
        assert math.isfinite(signal)

    def test_negative_when_leader_bearish_and_micro_not_diverged(self):
        """ES bearish → signal should be negative (follow the leader down)."""
        state = _state(
            primary={'aggressor_volume_imbalance': 0.0},
            cross_asset={'ES': {'aggressor_volume_imbalance': -0.8}},
        )
        signal = self.hyp.evaluate(state)
        assert signal < 0.0, f"Expected negative signal, got {signal}"

    def test_signal_stronger_when_micro_close_to_leader(self):
        """
        Signal structure: tanh(es_imb*2) * (1 - tanh(|div|)).

        The (1 - tanh(|div|)) term is largest when |div| is small — i.e.,
        when micro and leader are close together (micro not yet escaped the
        leader).  When micro is wildly decoupled (|div| large), the term
        shrinks toward 0.  The hypothesis fires strongest in the window where
        the lag exists AND micro is still near the leader (not overshot).

        Concretely: micro at 0.75 (close to es 0.8) → small div → strong signal.
        micro at 0.0 (neutral while es is 0.8) → large div → weaker signal.
        """
        # Micro close to leader: divergence = 0.75 - 0.8 = -0.05 (small)
        state_close = _state(
            primary={'aggressor_volume_imbalance': 0.75},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.8}},
        )
        # Micro far from leader: divergence = 0.0 - 0.8 = -0.8 (large)
        state_far = _state(
            primary={'aggressor_volume_imbalance': 0.0},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.8}},
        )
        s_close = self.hyp.evaluate(state_close)
        s_far = self.hyp.evaluate(state_far)
        # Both signals positive (following bullish leader)
        assert s_close > 0.0 and s_far > 0.0
        # Signal is larger when micro is near the leader (small divergence term)
        assert s_close > s_far, (
            f"Close-divergence signal ({s_close:.4f}) should exceed "
            f"far-divergence signal ({s_far:.4f})"
        )

    def test_does_not_read_institutional_flow_score(self):
        """
        Verify the fix: hypothesis must not crash or misbehave when
        institutional_flow_score key is absent (it has no producer).
        """
        state = _state(
            primary={'aggressor_volume_imbalance': 0.3},
            # ES dict deliberately omits institutional_flow_score
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.1}},
        )
        # Must not raise and must return a finite value
        result = self.hyp.evaluate(state)
        assert math.isfinite(result)

    def test_bounded_output(self):
        """Output stays in (-1, 1) across a grid of inputs."""
        for es_imb in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            for micro_imb in [-1.0, -0.5, 0.0, 0.5, 1.0]:
                state = _state(
                    primary={'aggressor_volume_imbalance': micro_imb},
                    cross_asset={'ES': {'aggressor_volume_imbalance': es_imb}},
                )
                r = self.hyp.evaluate(state)
                assert -1.0 <= r <= 1.0, (
                    f"Out of bounds at es_imb={es_imb}, micro={micro_imb}: {r}"
                )


# ---------------------------------------------------------------------------
# Suite 3: HYP 32 — DailyLossLimitDefense (no longer constant-zero)
# ---------------------------------------------------------------------------

class TestDailyLossLimitDefense:
    """HYP 32: replaced no-op with real loss-limit-defense logic."""

    def setup_method(self):
        self.hyp = DailyLossLimitDefense()

    def _state_active(self, cutoff_pressure: float) -> MarketState:
        """Build a state where prop_cohort_active() returns True."""
        from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX
        n = max(FEATURE_NAME_TO_INDEX.values()) + 1
        vec = np.zeros(n)
        cutoff_idx = FEATURE_NAME_TO_INDEX.get('cutoff_pressure_score')
        if cutoff_idx is not None:
            vec[cutoff_idx] = cutoff_pressure
        state = _state(
            primary={'aggressor_volume_imbalance': 0.8},
            cross_asset={'ES': {'aggressor_volume_imbalance': 0.1}},
            # divergence = 0.7 > 0.3 threshold → prop_cohort_active = True
            feature_vector=vec,
        )
        return state

    def test_zero_when_cohort_inactive(self):
        """prop_cohort_active() == False → return 0.0."""
        # No ES leg → divergence = 0 → cohort inactive
        state = _state(primary={'aggressor_volume_imbalance': 0.5})
        assert self.hyp.evaluate(state) == pytest.approx(0.0)

    def test_nonzero_when_cohort_active_with_positive_cutoff_pressure(self):
        """Cohort active + buy-side cutoff pressure → negative signal (fade the buys)."""
        state = self._state_active(cutoff_pressure=0.7)
        result = self.hyp.evaluate(state)
        assert result != pytest.approx(0.0), (
            "HYP 32 must not be constant-zero when cohort is active"
        )
        # Fade: positive cutoff pressure (forced buys) → negative signal
        assert result < 0.0, f"Expected negative fade signal, got {result}"

    def test_nonzero_when_cohort_active_with_negative_cutoff_pressure(self):
        """Cohort active + sell-side cutoff pressure → positive signal (fade the sells)."""
        state = self._state_active(cutoff_pressure=-0.7)
        result = self.hyp.evaluate(state)
        assert result > 0.0, f"Expected positive fade signal, got {result}"

    def test_two_different_inputs_give_two_different_outputs(self):
        """Core anti-no-op assertion: distinct inputs must produce distinct outputs."""
        state_heavy = self._state_active(cutoff_pressure=0.8)
        state_light = self._state_active(cutoff_pressure=0.1)
        r_heavy = self.hyp.evaluate(state_heavy)
        r_light = self.hyp.evaluate(state_light)
        assert r_heavy != pytest.approx(r_light), (
            f"HYP 32 returned identical output ({r_heavy}) for different inputs"
        )

    def test_signal_sign_opposes_cutoff_direction(self):
        """
        Fade sign: signal must be opposite sign to cutoff_pressure_score.
        Forced buy exits (positive pressure) → expect to fade up → negative signal.
        """
        state_pos = self._state_active(cutoff_pressure=0.5)
        state_neg = self._state_active(cutoff_pressure=-0.5)
        r_pos = self.hyp.evaluate(state_pos)
        r_neg = self.hyp.evaluate(state_neg)
        assert r_pos < 0.0, f"Positive cutoff pressure should yield negative signal, got {r_pos}"
        assert r_neg > 0.0, f"Negative cutoff pressure should yield positive signal, got {r_neg}"

    def test_bounded_output(self):
        """Output in (-1, 1)."""
        for cp in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            state = self._state_active(cutoff_pressure=cp)
            r = self.hyp.evaluate(state)
            assert -1.0 <= r <= 1.0, f"Out of bounds at cutoff={cp}: {r}"


# ---------------------------------------------------------------------------
# Suite 4: HYP 30 — CutoffPanicExits (context repoint)
# ---------------------------------------------------------------------------

class TestCutoffPanicExits:
    """HYP 30: repointed from TPT_FLATTEN/APEX_FLATTEN to real contexts."""

    def setup_method(self):
        self.hyp = CutoffPanicExits()

    def _state_with_cutoff(self, context: str, cutoff_val: float) -> MarketState:
        from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX
        n = max(FEATURE_NAME_TO_INDEX.values()) + 1
        vec = np.zeros(n)
        idx = FEATURE_NAME_TO_INDEX.get('cutoff_pressure_score')
        if idx is not None:
            vec[idx] = cutoff_val
        return _state(context=context, feature_vector=vec)

    def test_zero_outside_prop_flatten_contexts(self):
        """Any context other than PROP_FLATTEN_TOPSTEP / FRIDAY_CLOSE → 0."""
        for ctx in ('NORMAL', 'CPI_TIGHT', 'NFP_TIGHT', 'PROP_REOPEN',
                    'TPT_FLATTEN', 'APEX_FLATTEN', 'CASH_EQUITY_OPEN'):
            state = self._state_with_cutoff(ctx, 0.7)
            assert self.hyp.evaluate(state) == pytest.approx(0.0), (
                f"Expected zero outside prop-flatten context, got non-zero at ctx={ctx}"
            )

    def test_nonzero_inside_prop_flatten_topstep(self):
        """PROP_FLATTEN_TOPSTEP context with cutoff pressure → non-zero."""
        state = self._state_with_cutoff('PROP_FLATTEN_TOPSTEP', 0.7)
        result = self.hyp.evaluate(state)
        assert result != pytest.approx(0.0), (
            "HYP 30 must fire inside PROP_FLATTEN_TOPSTEP"
        )
        assert math.isfinite(result)

    def test_nonzero_inside_friday_close(self):
        """FRIDAY_CLOSE context with cutoff pressure → non-zero."""
        state = self._state_with_cutoff('FRIDAY_CLOSE', 0.6)
        result = self.hyp.evaluate(state)
        assert result != pytest.approx(0.0), (
            "HYP 30 must fire inside FRIDAY_CLOSE"
        )

    def test_zero_inside_context_but_zero_cutoff_pressure(self):
        """Correct context but no cutoff pressure → signal is zero."""
        state = self._state_with_cutoff('PROP_FLATTEN_TOPSTEP', 0.0)
        assert self.hyp.evaluate(state) == pytest.approx(0.0)

    def test_signal_follows_cutoff_direction(self):
        """Signal follows the sign of cutoff_pressure_score (ride the exits)."""
        state_buy = self._state_with_cutoff('FRIDAY_CLOSE', 0.6)
        state_sell = self._state_with_cutoff('FRIDAY_CLOSE', -0.6)
        assert self.hyp.evaluate(state_buy) > 0.0
        assert self.hyp.evaluate(state_sell) < 0.0

    def test_bounded_output(self):
        """Output in (-1, 1) at both valid contexts."""
        for ctx in ('PROP_FLATTEN_TOPSTEP', 'FRIDAY_CLOSE'):
            for cp in [-1.0, -0.5, 0.5, 1.0]:
                state = self._state_with_cutoff(ctx, cp)
                r = self.hyp.evaluate(state)
                assert -1.0 <= r <= 1.0


# ---------------------------------------------------------------------------
# Suite 5: HYP 38 — EconomicEventRestrictionFlattening (_TIGHT gate)
# ---------------------------------------------------------------------------

class TestEconomicEventRestrictionFlattening:
    """HYP 38: NEWS_RESTRICTION context replaced by _TIGHT gate."""

    def setup_method(self):
        self.hyp = EconomicEventRestrictionFlattening()

    def _state_with_flatten(self, context: str, flatten_val: float) -> MarketState:
        from features_engine.src.features.feature_index import FEATURE_NAME_TO_INDEX
        n = max(FEATURE_NAME_TO_INDEX.values()) + 1
        vec = np.zeros(n)
        idx = FEATURE_NAME_TO_INDEX.get('news_restriction_flatten_score')
        if idx is not None:
            vec[idx] = flatten_val
        return _state(context=context, feature_vector=vec)

    def test_zero_outside_tight_contexts(self):
        """Contexts that don't end with _TIGHT → zero."""
        for ctx in ('NORMAL', 'PROP_FLATTEN_TOPSTEP', 'FRIDAY_CLOSE',
                    'PROP_REOPEN', 'CASH_EQUITY_OPEN', 'NEWS_RESTRICTION'):
            state = self._state_with_flatten(ctx, 0.7)
            assert self.hyp.evaluate(state) == pytest.approx(0.0), (
                f"Expected zero outside _TIGHT contexts, got non-zero at ctx={ctx}"
            )

    def test_fires_under_cpi_tight(self):
        """CPI_TIGHT context (ends with _TIGHT) + flatten score → non-zero."""
        state = self._state_with_flatten('CPI_TIGHT', 0.7)
        result = self.hyp.evaluate(state)
        assert result != pytest.approx(0.0), (
            "HYP 38 must fire inside CPI_TIGHT (prop news-ban window)"
        )
        assert math.isfinite(result)

    def test_fires_under_nfp_tight(self):
        """NFP_TIGHT context → non-zero with flatten score present."""
        state = self._state_with_flatten('NFP_TIGHT', 0.5)
        assert self.hyp.evaluate(state) != pytest.approx(0.0)

    def test_fires_under_fomc_statement_tight(self):
        """FOMC_STATEMENT_TIGHT context → non-zero."""
        state = self._state_with_flatten('FOMC_STATEMENT_TIGHT', 0.4)
        assert self.hyp.evaluate(state) != pytest.approx(0.0)

    def test_zero_when_no_flatten_score_in_tight_context(self):
        """Correct context but flatten score is zero → signal zero."""
        state = self._state_with_flatten('CPI_TIGHT', 0.0)
        assert self.hyp.evaluate(state) == pytest.approx(0.0)

    def test_signal_direction_follows_flatten_score(self):
        """Signal follows the sign of news_restriction_flatten_score."""
        state_pos = self._state_with_flatten('NFP_TIGHT', 0.6)
        state_neg = self._state_with_flatten('NFP_TIGHT', -0.6)
        assert self.hyp.evaluate(state_pos) > 0.0
        assert self.hyp.evaluate(state_neg) < 0.0

    def test_bounded_output(self):
        """Output in (-1, 1) across tight contexts and flatten values."""
        for ctx in ('CPI_TIGHT', 'NFP_TIGHT', 'FOMC_STATEMENT_TIGHT'):
            for fv in [-1.0, -0.5, 0.5, 1.0]:
                state = self._state_with_flatten(ctx, fv)
                r = self.hyp.evaluate(state)
                assert -1.0 <= r <= 1.0
