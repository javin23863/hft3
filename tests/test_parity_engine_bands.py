"""
Band-based no-arb detector tests (WS-2 detector upgrade).

Golden values are derived inline where they appear; see comments.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_lane.src.backtest.options_fee_model import OptionsFeeModel
from options_lane.src.models import LegQuote, ParityGroup, QuoteSnapshot, RateSpec
from options_lane.src.parity_engine import (
    check_calendar_monotonicity,
    compute_band_violation,
    compute_violation,
    discount_factor,
    is_actionable,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _group_with_style(option_style: str = "american", rate: float = 0.05, t: float = 0.25) -> ParityGroup:
    """
    Return a ParityGroup-like object whose option_style is visible to _band_for_group.
    Since ParityGroup is frozen we subclass it (additive, no mutation).
    """

    class StyledGroup(ParityGroup):
        # Extra field not in frozen parent — attach via __init_subclass__ trick
        pass

    g = StyledGroup.from_dict(
        {
            "id": "band_test",
            "type": "futures_options",
            "rate": {"source": "constant", "value": rate},
            "legs": [
                {"role": "future", "symbol": "UL.c.0"},
                {"role": "call", "symbol": "UL.c.0", "strike": 5500, "right": "C"},
                {"role": "put", "symbol": "UL.c.0", "strike": 5500, "right": "P"},
            ],
            "threshold_ticks": 1.0,
            "tick_size": 0.25,
            "time_to_expiry_years": t,
        }
    )
    # Inject option_style via object.__setattr__ (frozen dataclass allows this trick)
    object.__setattr__(g, "option_style", option_style)
    return g


def _snap(group: ParityGroup, call_mid: float, put_mid: float, future_mid: float,
          call_bid: float | None = None, call_ask: float | None = None,
          put_bid: float | None = None, put_ask: float | None = None,
          fut_bid: float | None = None, fut_ask: float | None = None,
          ts: int = 1_000_000_000) -> QuoteSnapshot:
    """Build a QuoteSnapshot from mid values, with optional bid/ask."""
    cb = call_bid if call_bid is not None else call_mid
    ca = call_ask if call_ask is not None else call_mid
    pb = put_bid if put_bid is not None else put_mid
    pa = put_ask if put_ask is not None else put_mid
    fb = fut_bid if fut_bid is not None else future_mid
    fa = fut_ask if fut_ask is not None else future_mid
    quotes = {
        "future": LegQuote("future", "UL.c.0", fb, fa, ts),
        "call": LegQuote("call", "UL.c.0", cb, ca, ts, strike=5500.0, right="C"),
        "put": LegQuote("put", "UL.c.0", pb, pa, ts, strike=5500.0, right="P"),
    }
    return QuoteSnapshot(group.id, ts, quotes)


# ---------------------------------------------------------------------------
# 1. European exact parity: zero-width band
# ---------------------------------------------------------------------------

class TestEuropeanExactParity:
    def test_zero_width_band_at_parity(self):
        """
        European: band_lo == band_hi == df*(F-K).
        F=5500, K=5500, r=0.05, T=0.25 => df=e^{-0.0125} ≈ 0.98757.
        theo = df*(5500-5500) = 0.0.
        """
        g = _group_with_style("european")
        df = math.exp(-0.05 * 0.25)
        # exact parity: C-P = df*(F-K) = 0
        snap = _snap(g, call_mid=10.0, put_mid=10.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap)
        assert bv is not None
        assert abs(bv.band_lo - bv.band_hi) < 1e-9
        assert abs(bv.excess) < 1e-9
        assert not bv.actionable

    def test_european_violation_arithmetic(self):
        """
        European: C-P = 5.0, theo = 0.0 => excess = 5.0.
        With fee_per_leg=1.0, 3 legs => fee_total=3.0 => actionable (5 > 3).
        """
        g = _group_with_style("european")
        # C-P=5, theo=0 => excess=5
        snap = _snap(g, call_mid=15.0, put_mid=10.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=1.0)
        assert bv is not None
        assert abs(bv.excess - 5.0) < 1e-6
        assert bv.fee_total == 3.0
        assert bv.actionable

    def test_european_below_parity_violation(self):
        """C-P=-5 => excess=-5 (below the zero-width band)."""
        g = _group_with_style("european")
        snap = _snap(g, call_mid=5.0, put_mid=10.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert bv.excess < -4.9


# ---------------------------------------------------------------------------
# 2. American band: inside => no violation
# ---------------------------------------------------------------------------

class TestAmericanBand:
    def _band(self, f: float = 5500.0, k: float = 5500.0, r: float = 0.05, t: float = 0.25):
        df = math.exp(-r * t)
        lo = df * f - k
        hi = f - df * k
        return lo, hi, df

    def test_inside_band_no_violation(self):
        """
        F=5500, K=5500, r=0.05, T=0.25:
          df ≈ 0.98757
          lo = 0.98757*5500 - 5500 ≈ -68.34
          hi = 5500 - 0.98757*5500 ≈ +68.34
        C-P=0 is inside => excess=0 => not actionable.
        This is the false-positive fix for American quarterlies.
        """
        lo, hi, df = self._band()
        g = _group_with_style("american")
        # Spread 0 is inside band
        snap = _snap(g, call_mid=20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert bv.excess == 0.0
        assert not bv.actionable

    def test_inside_band_nonzero_spread_no_violation(self):
        """
        C-P = 50 is inside band [lo≈-68, hi≈+68] => excess=0 => no violation.
        This covers the mid-price false-positive scenario from JIRA WS-2.
        """
        lo, hi, df = self._band()
        g = _group_with_style("american")
        mid_spread = (lo + hi) / 2.0 + 10.0  # somewhere in the middle of valid band
        # Ensure mid_spread is inside
        assert lo < mid_spread < hi
        snap = _snap(g, call_mid=mid_spread + 20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap)
        assert bv is not None
        assert bv.excess == 0.0
        assert not bv.actionable

    def test_outside_band_hi_violation(self):
        """
        C-P = hi + 10 => excess = +10.
        """
        lo, hi, df = self._band()
        g = _group_with_style("american")
        target_spread = hi + 10.0
        snap = _snap(g, call_mid=target_spread + 20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert abs(bv.excess - 10.0) < 1e-4
        assert bv.executable
        assert bv.actionable

    def test_outside_band_lo_violation(self):
        """
        C-P = lo - 10 => excess ≈ -10.
        """
        lo, hi, df = self._band()
        g = _group_with_style("american")
        target_spread = lo - 10.0
        snap = _snap(g, call_mid=target_spread + 20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert bv.excess < -9.9
        assert bv.executable
        assert bv.actionable

    def test_american_correct_excess_magnitude(self):
        """Excess magnitude equals distance from violated boundary."""
        lo, hi, df = self._band()
        g = _group_with_style("american")
        overshoot = 5.0
        target_spread = hi + overshoot
        snap = _snap(g, call_mid=target_spread + 20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert abs(bv.excess - overshoot) < 1e-4


# ---------------------------------------------------------------------------
# 3. Executable vs mid: mid violates but executable does not => not actionable
# ---------------------------------------------------------------------------

class TestExecutableVsMid:
    def test_mid_violation_executable_inside_band_not_actionable(self):
        """
        Scenario: mid C-P is outside band_hi, but ask(call) - bid(put) is inside.
        => BandViolation.executable=False, actionable=False.

        Band hi ≈ 68.34 (F=K=5500, r=0.05, T=0.25).
        Set call_mid=90, put_mid=10 => mid spread=80 > hi => mid excess=~11.6.
        But call_ask=78, put_bid=10 => exec spread=68 <= hi => no executable violation.
        """
        g = _group_with_style("american")
        df = math.exp(-0.05 * 0.25)
        hi = 5500.0 - df * 5500.0  # ≈ 68.34

        # Mid spread = 80 (violates hi)
        # Exec spread: sell call at bid, buy put at ask
        # direction = short_call_long_put => exec_c=call.bid, exec_p=put.ask
        # Set call.bid=68, call.ask=112, call.mid=90; put.bid=9.9, put.ask=10.1, put.mid=10
        # exec_spread = 68 - 10.1 = 57.9 < hi => inside band => not executable
        snap = _snap(
            g,
            call_mid=90.0, put_mid=10.0, future_mid=5500.0,
            call_bid=68.0, call_ask=112.0,
            put_bid=9.9, put_ask=10.1,
        )
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        # Mid spread should show violation
        assert bv.excess > 0.0
        # But executable spread inside band => not executable => not actionable
        assert not bv.executable
        assert not bv.actionable

    def test_mid_and_executable_both_violate_actionable(self):
        """Both mid and executable spreads exceed band_hi => actionable."""
        g = _group_with_style("american")
        df = math.exp(-0.05 * 0.25)
        hi = 5500.0 - df * 5500.0

        # exec spread: call.bid - put.ask = (hi + 20) => clearly outside
        call_bid = hi + 30.0
        snap = _snap(
            g,
            call_mid=hi + 40.0, put_mid=10.0, future_mid=5500.0,
            call_bid=call_bid, call_ask=hi + 50.0,
            put_bid=9.9, put_ask=10.1,
        )
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert bv.executable
        assert bv.actionable


# ---------------------------------------------------------------------------
# 4. Degraded flag when bid/ask missing (bid==ask==mid)
# ---------------------------------------------------------------------------

class TestDegradedFlag:
    def test_degraded_when_bid_equals_ask(self):
        """When call.bid==call.ask and put.bid==put.ask => degraded=True."""
        g = _group_with_style("american")
        # Default _snap sets bid=ask=mid for both options
        snap = _snap(g, call_mid=20.0, put_mid=20.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap)
        assert bv is not None
        assert bv.degraded

    def test_not_degraded_when_spread_present(self):
        """When bid != ask on at least one option => degraded=False."""
        g = _group_with_style("american")
        snap = _snap(
            g,
            call_mid=20.0, put_mid=20.0, future_mid=5500.0,
            call_bid=19.5, call_ask=20.5,
        )
        bv = compute_band_violation(g, snap)
        assert bv is not None
        assert not bv.degraded


# ---------------------------------------------------------------------------
# 5. Fee integration
# ---------------------------------------------------------------------------

class TestFeeIntegration:
    def test_violation_actionable_if_excess_clears_fees(self):
        """
        European, excess=5.0, fee_model with fee_per_contract=1.0, 3 legs => fee_total=3.0.
        5 > 3 => actionable.
        """
        g = _group_with_style("european")
        snap = _snap(g, call_mid=15.0, put_mid=10.0, future_mid=5500.0)
        fm = OptionsFeeModel(fee_per_contract=1.0)
        bv = compute_band_violation(g, snap, fee_model=fm)
        assert bv is not None
        assert bv.fee_total == 3.0
        assert bv.actionable

    def test_violation_not_actionable_if_excess_below_fees(self):
        """
        European, excess≈5, fee_model with fee_per_contract=2.0, 3 legs => fee_total=6 > 5.
        => not actionable.
        """
        g = _group_with_style("european")
        snap = _snap(g, call_mid=15.0, put_mid=10.0, future_mid=5500.0)
        fm = OptionsFeeModel(fee_per_contract=2.0)
        bv = compute_band_violation(g, snap, fee_model=fm)
        assert bv is not None
        assert bv.fee_total == 6.0
        assert not bv.actionable

    def test_fee_model_overrides_fee_per_leg(self):
        """fee_model takes priority over fee_per_leg kwarg."""
        g = _group_with_style("european")
        snap = _snap(g, call_mid=15.0, put_mid=10.0, future_mid=5500.0)
        fm = OptionsFeeModel(fee_per_contract=0.5)
        bv = compute_band_violation(g, snap, fee_model=fm, fee_per_leg=100.0)
        assert bv is not None
        assert bv.fee_total == 1.5  # 0.5 * 3 legs

    def test_fee_per_leg_fallback_without_model(self):
        """Without fee_model, fee_per_leg * num_legs is used."""
        g = _group_with_style("european")
        snap = _snap(g, call_mid=15.0, put_mid=10.0, future_mid=5500.0)
        bv = compute_band_violation(g, snap, fee_per_leg=1.0)
        assert bv is not None
        assert bv.fee_total == 3.0


# ---------------------------------------------------------------------------
# 5b. Future spread sides: hedge leg must cross the future's bid/ask correctly
# ---------------------------------------------------------------------------

class TestFutureSpreadSides:
    """
    European, r=0 => df=1, zero-width band at F-K.  Future has NONZERO spread:
    bid=4999.5, ask=5000.5, mid=5000.0.  K=5500 (helper hardcodes strike).

    BandViolation carries only the MID band in band_lo/band_hi (no executable-band
    field), so the executable recomputation is pinned behaviorally: with df=1 the
    executable band point is exec_f - K, and `executable` flips to False exactly
    when exec_spread == exec_f - K.
    """

    def test_rich_spread_hedge_buys_future_at_ask(self):
        """
        Rich: C-P above mid band => direction short_call_long_put => synthetic
        short future => hedge BUYS future at ask => exec band point = ask - K.

        Mid band point = mid - K = 5000.0 - 5500 = -500.0.
        Exec band point = ask - K = 5000.5 - 5500 = -499.5.
        Options degraded (bid==ask==mid) => exec_spread == observed mid spread.

        Case A: observed = -499.5 (call=20, put=519.5): mid excess = +0.5 > 0
          (violation), exec_spread == ask - K exactly => exec excess 0 =>
          executable=False.  (Old bug used bid: -499.5 vs -500.5 => +1.0 =>
          executable=True; this assertion pins the fix.)
        Case B: observed = -499.25 (put=519.25): exec_spread - (ask - K) = +0.25
          => executable=True.
        """
        g = _group_with_style("european", rate=0.0)
        # Case A: exactly at ask - K
        snap = _snap(
            g,
            call_mid=20.0, put_mid=519.5, future_mid=5000.0,
            fut_bid=4999.5, fut_ask=5000.5,
        )
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        # Mid band stored: band_lo == band_hi == mid - K = -500.0
        assert bv.band_lo == bv.band_hi == -500.0
        assert abs(bv.excess - 0.5) < 1e-12  # mid violation: -499.5 - (-500.0)
        assert not bv.executable  # exec band used F=ask: -499.5 - (5000.5-5500) == 0
        assert not bv.actionable

        # Case B: 0.25 above ask - K => executable
        snap2 = _snap(
            g,
            call_mid=20.0, put_mid=519.25, future_mid=5000.0,
            fut_bid=4999.5, fut_ask=5000.5,
        )
        bv2 = compute_band_violation(g, snap2, fee_per_leg=0.0)
        assert bv2 is not None
        assert bv2.executable
        assert bv2.actionable

    def test_cheap_spread_hedge_sells_future_at_bid(self):
        """
        Cheap: C-P below mid band => direction long_call_short_put => synthetic
        long future => hedge SELLS future at bid => exec band point = bid - K.

        Mid band point = -500.0.  Exec band point = bid - K = 4999.5 - 5500 = -500.5.

        Case A: observed = -500.5 (call=20, put=520.5): mid excess = -0.5 < 0,
          exec_spread == bid - K exactly => exec excess 0 => executable=False.
        Case B: observed = -500.75 (put=520.75): exec_spread - (bid - K) = -0.25
          => executable=True.
        """
        g = _group_with_style("european", rate=0.0)
        # Case A: exactly at bid - K
        snap = _snap(
            g,
            call_mid=20.0, put_mid=520.5, future_mid=5000.0,
            fut_bid=4999.5, fut_ask=5000.5,
        )
        bv = compute_band_violation(g, snap, fee_per_leg=0.0)
        assert bv is not None
        assert bv.band_lo == bv.band_hi == -500.0
        assert abs(bv.excess - (-0.5)) < 1e-12  # mid violation: -500.5 - (-500.0)
        assert not bv.executable  # exec band used F=bid: -500.5 - (4999.5-5500) == 0
        assert not bv.actionable

        # Case B: 0.25 below bid - K => executable
        snap2 = _snap(
            g,
            call_mid=20.0, put_mid=520.75, future_mid=5000.0,
            fut_bid=4999.5, fut_ask=5000.5,
        )
        bv2 = compute_band_violation(g, snap2, fee_per_leg=0.0)
        assert bv2 is not None
        assert bv2.executable
        assert bv2.actionable


# ---------------------------------------------------------------------------
# 6. Calendar monotonicity
# ---------------------------------------------------------------------------

class TestCalendarMonotonicity:
    def test_same_underlying_false_skips_check(self):
        """Without same_underlying=True, returns None regardless of prices."""
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=50.0,  # would violate
            t1_expiry_years=0.25,
            t2_expiry_years=0.50,
            rate=0.05,
            same_underlying=False,
        )
        assert result is None

    def test_non_violating_calendar(self):
        """
        C(T1)=100, T1=0.25, T2=0.50, r=0.05.
        df_fwd = e^{-0.05*0.25} ≈ 0.98757.
        required_C_T2 = 0.98757*100 ≈ 98.76.
        C(T2)=110 >= 98.76 => excess=required-actual = 98.76-110 < 0 => not actionable.
        """
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=110.0,
            t1_expiry_years=0.25,
            t2_expiry_years=0.50,
            rate=0.05,
            same_underlying=True,
        )
        assert result is not None
        assert not result.actionable
        assert result.excess < 0.0

    def test_violating_calendar_flagged(self):
        """
        C(T1)=100, T2=0.50, r=0.05.
        df_fwd ≈ 0.98757, required_C_T2 ≈ 98.76.
        C(T2)=80 < 98.76 => excess≈18.76 > 0 => actionable.
        """
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=80.0,
            t1_expiry_years=0.25,
            t2_expiry_years=0.50,
            rate=0.05,
            same_underlying=True,
        )
        assert result is not None
        df_fwd = math.exp(-0.05 * 0.25)
        expected_excess = df_fwd * 100.0 - 80.0
        assert abs(result.excess - expected_excess) < 1e-6
        assert result.actionable
        assert result.same_underlying

    def test_calendar_with_cost_below_excess_actionable(self):
        """Excess=18.76, cost=5 => actionable."""
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=80.0,
            t1_expiry_years=0.25,
            t2_expiry_years=0.50,
            rate=0.05,
            cost_to_check=5.0,
            same_underlying=True,
        )
        assert result is not None
        assert result.actionable

    def test_calendar_with_cost_above_excess_not_actionable(self):
        """Excess≈18.76, cost=25 => not actionable."""
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=80.0,
            t1_expiry_years=0.25,
            t2_expiry_years=0.50,
            rate=0.05,
            cost_to_check=25.0,
            same_underlying=True,
        )
        assert result is not None
        assert not result.actionable

    def test_calendar_t2_before_t1_raises(self):
        """T2 <= T1 is invalid."""
        with pytest.raises(ValueError):
            check_calendar_monotonicity(
                symbol="ES",
                strike=5500.0,
                c_t1=100.0,
                c_t2=80.0,
                t1_expiry_years=0.50,
                t2_expiry_years=0.25,
                rate=0.05,
                same_underlying=True,
            )

    def test_calendar_df_forward_arithmetic(self):
        """Verify df_forward = e^{-r*(T2-T1)} stored correctly."""
        result = check_calendar_monotonicity(
            symbol="ES",
            strike=5500.0,
            c_t1=100.0,
            c_t2=110.0,
            t1_expiry_years=0.25,
            t2_expiry_years=0.75,
            rate=0.04,
            same_underlying=True,
        )
        assert result is not None
        expected_df = math.exp(-0.04 * 0.50)
        assert abs(result.df_forward - expected_df) < 1e-12


# ---------------------------------------------------------------------------
# 7. Regression: existing API still works
# ---------------------------------------------------------------------------

class TestRegressionExistingAPI:
    def _futures_group(self) -> ParityGroup:
        return ParityGroup.from_dict(
            {
                "id": "reg_test",
                "type": "futures_options",
                "rate": {"source": "constant", "value": 0.05},
                "legs": [
                    {"role": "future", "symbol": "UL.c.0"},
                    {"role": "call", "symbol": "UL.c.0", "strike": 5500, "right": "C"},
                    {"role": "put", "symbol": "UL.c.0", "strike": 5500, "right": "P"},
                ],
                "threshold_ticks": 1.0,
                "tick_size": 0.25,
                "time_to_expiry_years": 0.25,
            }
        )

    def test_compute_violation_still_works(self):
        """compute_violation() signature unchanged; zero residual on fair prices."""
        group = self._futures_group()
        df = math.exp(-0.05 * 0.25)
        theo = 5500.0 - df * 5500.0
        call_mid = theo / 2 + 10
        put_mid = call_mid - theo
        quotes = {
            "future": LegQuote("future", "UL.c.0", 5500.0, 5500.0, 1_000_000_000),
            "call": LegQuote("call", "UL.c.0", call_mid, call_mid, 1_000_000_000, strike=5500.0, right="C"),
            "put": LegQuote("put", "UL.c.0", put_mid, put_mid, 1_000_000_000, strike=5500.0, right="P"),
        }
        snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
        v = compute_violation(group, snap)
        assert v is not None
        assert abs(v.residual) < 1e-9

    def test_is_actionable_still_works(self):
        """is_actionable signature unchanged."""
        group = self._futures_group()
        df = math.exp(-0.05 * 0.25)
        theo = 5500.0 - df * 5500.0
        call_mid = theo / 2 + 10 + 2.0  # inject 2 ticks
        put_mid = call_mid - theo - 2.0
        quotes = {
            "future": LegQuote("future", "UL.c.0", 5500.0, 5500.0, 1_000_000_000),
            "call": LegQuote("call", "UL.c.0", call_mid, call_mid, 1_000_000_000, strike=5500.0, right="C"),
            "put": LegQuote("put", "UL.c.0", put_mid, put_mid, 1_000_000_000, strike=5500.0, right="P"),
        }
        snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
        v = compute_violation(group, snap, fee_per_leg=0.0)
        assert v is not None
        assert is_actionable(v, group)

    def test_missing_legs_returns_none(self):
        """compute_band_violation returns None when legs are missing."""
        group = self._futures_group()
        quotes = {
            "call": LegQuote("call", "UL.c.0", 10.0, 10.0, 1_000_000_000, strike=5500.0, right="C"),
        }
        snap = QuoteSnapshot(group.id, 1_000_000_000, quotes)
        bv = compute_band_violation(group, snap)
        assert bv is None

    def test_filtration_respected_in_band_violation(self):
        """compute_band_violation respects as_of_ns filtration."""
        group = self._futures_group()
        quotes = {
            "future": LegQuote("future", "UL.c.0", 5500.0, 5500.0, 2_000_000_000),
            "call": LegQuote("call", "UL.c.0", 20.0, 20.0, 1_000_000_000, strike=5500.0, right="C"),
            "put": LegQuote("put", "UL.c.0", 20.0, 20.0, 1_000_000_000, strike=5500.0, right="P"),
        }
        snap = QuoteSnapshot(group.id, 2_000_000_000, quotes)
        # At t=1s future is not yet visible => None
        bv = compute_band_violation(group, snap, as_of_ns=1_000_000_000)
        assert bv is None
        # At t=2s all legs visible
        bv2 = compute_band_violation(group, snap, as_of_ns=2_000_000_000)
        assert bv2 is not None
