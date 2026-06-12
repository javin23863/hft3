"""Tests for ChainSim — options-leg chain co-simulation MVP.

All goldens hand-computed inline; no external fixtures required.

Multiplier = 50 (ES default) throughout unless noted.
Fee = 0.65 per contract (OptionsFeeModel default).

Golden hand-computations
------------------------
G1 — buy 1 call, settle ITM:
  Ask = 5.00, qty=1, fee=0.65
  cash_flow_fill = -1 * 1 * 5.00 * 50 - 0.65 = -250.65
  settlement: K=4500, fix=4550, intrinsic=50, pnl = 1 * 50 * 50 = 2500
  net = -250.65 + 2500 = 2249.35

G2 — sell 1 put, settle OTM:
  Bid = 3.00, qty=1, fee=0.65
  cash_flow_fill = -(-1) * 1 * 3.00 * 50 - 0.65 = 150 - 0.65 = 149.35
  settlement: K=4500, fix=4600 (put OTM: max(-1*(4600-4500),0)=0), pnl=0
  net = 149.35

G3 — partial fill at displayed size:
  Ask = 6.00, ask_size=3, order qty=5 -> fills 3
  remainder 2 NOT filled.

G4 — stale quote rejection:
  max_quote_age_ns=1_000_000 (1 ms), quote at t=0, order at t=2_000_000
  exec_ts = 2_000_000 + latency, quote age = exec_ts - 0 > 1_000_000 -> REJECT

G5 — latency: quote changes during latency window:
  quote1 at t=0: ask=5.00
  quote2 at t=500_000: ask=6.00
  order submitted at t=0, latency=1_000_000, exec_ts=1_000_000
  -> fills at ask=6.00 (quote2 is latest at or before exec_ts=1_000_000)
  This tests no-lookahead: fill uses the quote visible AT exec time.

G6 — American instrument rejected.

G7 — settle removes position; double-settle raises.

G8 — fee model integration golden: fee_per_contract=1.25, qty=3
  fee = 3 * 1.25 = 3.75

G9 — equity reconciliation: sum(fills.cash_flow) + sum(settlements.pnl) == realized_pnl.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "packages"))

from options_lane.src.backtest.chain_sim import (
    ChainQuote,
    ChainSim,
    Fill,
    FillStatus,
    Order,
    _pos_key,
)
from options_lane.src.backtest.options_fee_model import OptionsFeeModel


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_EXPIRY = datetime(2025, 6, 20, 20, 0, 0, tzinfo=timezone.utc)  # CDT 15:00 → 20:00 UTC
_EXPIRY_2 = datetime(2025, 6, 27, 20, 0, 0, tzinfo=timezone.utc)
_MUL = 50.0


def _call_quote(ts_ns: int, ask: float, bid: float = 0.01,
                bid_size: int = 10, ask_size: int = 10,
                K: float = 4500.0, symbol: str = "ESM5") -> ChainQuote:
    return ChainQuote(
        ts_ns=ts_ns,
        symbol=symbol,
        K=K,
        expiry_utc=_EXPIRY,
        is_call=True,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def _put_quote(ts_ns: int, bid: float, ask: float = 99.0,
               bid_size: int = 10, ask_size: int = 10,
               K: float = 4500.0, symbol: str = "ESM5") -> ChainQuote:
    return ChainQuote(
        ts_ns=ts_ns,
        symbol=symbol,
        K=K,
        expiry_utc=_EXPIRY,
        is_call=False,
        bid=bid,
        ask=ask,
        bid_size=bid_size,
        ask_size=ask_size,
    )


def _make_sim(fee_per_contract: float = 0.65, latency_ns: int = 1_000_000,
              max_quote_age_ns: int = 60_000_000_000) -> ChainSim:
    return ChainSim(
        latency_ns=latency_ns,
        max_quote_age_ns=max_quote_age_ns,
        fee_per_contract=fee_per_contract,
        multiplier=_MUL,
        style_fn=lambda _: "E",  # all European in tests (override per test where needed)
    )


# ---------------------------------------------------------------------------
# G1: buy 1 call at ask + fee, settle ITM -> exact PnL golden
# ---------------------------------------------------------------------------

class TestBuyCallSettleITM:
    """G1: buy 1 call (ask=5.00), settle ITM at fixing=4550, K=4500."""

    def test_fill_price_and_fee(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00, bid=4.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.FILLED
        assert fill.qty_filled == 1
        assert fill.price == 5.00
        assert abs(fill.fee - 0.65) < 1e-9

    def test_cash_flow_fill(self):
        # cash_flow = -1 * 1 * 5.00 * 50 - 0.65 = -250.65
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00, bid=4.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert abs(fill.cash_flow - (-250.65)) < 1e-9, f"cash_flow={fill.cash_flow}"

    def test_settlement_itm(self):
        # settlement pnl = 1 * 50 * (4550-4500) = 2500
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00, bid=4.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        sim.submit(order)
        records = sim.settle(_EXPIRY, fixing_price=4550.0)
        assert len(records) == 1
        assert abs(records[0]["pnl"] - 2500.0) < 1e-9

    def test_net_pnl_golden(self):
        # net = -250.65 + 2500 = 2249.35
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00, bid=4.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        sim.submit(order)
        sim.settle(_EXPIRY, fixing_price=4550.0)
        result = sim.result()
        assert abs(result.realized_pnl - 2249.35) < 1e-9, f"realized_pnl={result.realized_pnl}"


# ---------------------------------------------------------------------------
# G2: sell put, settle OTM -> keep premium minus fees
# ---------------------------------------------------------------------------

class TestSellPutSettleOTM:
    """G2: sell 1 put (bid=3.00), settle OTM at fixing=4600, K=4500."""

    def test_cash_flow_fill(self):
        # cash_flow = -(-1) * 1 * 3.00 * 50 - 0.65 = 149.35
        sim = _make_sim()
        sim.feed([_put_quote(ts_ns=0, bid=3.00, ask=3.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=False, side=-1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.FILLED
        assert fill.price == 3.00
        assert abs(fill.cash_flow - 149.35) < 1e-9, f"cash_flow={fill.cash_flow}"

    def test_settlement_otm(self):
        # put OTM when fixing > K: intrinsic = max(-1*(4600-4500),0) = 0
        sim = _make_sim()
        sim.feed([_put_quote(ts_ns=0, bid=3.00, ask=3.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=False, side=-1, qty=1)
        sim.submit(order)
        records = sim.settle(_EXPIRY, fixing_price=4600.0)
        assert len(records) == 1
        assert abs(records[0]["pnl"] - 0.0) < 1e-9

    def test_net_pnl_golden(self):
        # net = 149.35 + 0 = 149.35
        sim = _make_sim()
        sim.feed([_put_quote(ts_ns=0, bid=3.00, ask=3.50)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=False, side=-1, qty=1)
        sim.submit(order)
        sim.settle(_EXPIRY, fixing_price=4600.0)
        result = sim.result()
        assert abs(result.realized_pnl - 149.35) < 1e-9, f"realized_pnl={result.realized_pnl}"


# ---------------------------------------------------------------------------
# G3: partial fill at displayed size; remainder NOT filled
# ---------------------------------------------------------------------------

class TestPartialFill:
    """G3: ask_size=3, order qty=5 -> fill 3; remainder 2 NOT filled."""

    def test_partial_fill_qty(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=6.00, ask_size=3)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=5)
        fill = sim.submit(order)
        assert fill.status == FillStatus.PARTIAL
        assert fill.qty_filled == 3
        assert fill.qty_requested == 5

    def test_partial_fill_no_second_fill(self):
        # After partial fill there is no automatic re-quote chasing
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=6.00, ask_size=3)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=5)
        fill = sim.submit(order)
        # Only one fill event; qty_filled < qty_requested and no further fill
        result = sim.result()
        assert len(result.fills) == 1
        assert result.fills[0].qty_filled == 3

    def test_partial_cash_flow(self):
        # cash_flow = -1 * 3 * 6.00 * 50 - (3 * 0.65) = -900 - 1.95 = -901.95
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=6.00, ask_size=3)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=5)
        fill = sim.submit(order)
        expected = -1 * 3 * 6.00 * _MUL - 3 * 0.65
        assert abs(fill.cash_flow - expected) < 1e-9


# ---------------------------------------------------------------------------
# G4: stale-quote rejection
# ---------------------------------------------------------------------------

class TestStaleQuoteRejection:
    """G4: quote at t=0, order at t=2_000_000, max_quote_age=1_000_000, latency=0."""

    def test_stale_quote_rejected(self):
        sim = _make_sim(latency_ns=0, max_quote_age_ns=1_000_000)
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        # order submitted at t=2_000_000, exec_ts=2_000_000
        # quote age = 2_000_000 - 0 = 2_000_000 > 1_000_000 -> REJECT
        order = Order(ts_ns=2_000_000, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.STALE_QUOTE
        assert fill.qty_filled == 0

    def test_fresh_quote_not_rejected(self):
        sim = _make_sim(latency_ns=0, max_quote_age_ns=5_000_000)
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        order = Order(ts_ns=2_000_000, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.FILLED


# ---------------------------------------------------------------------------
# G5: latency — fill at quote visible at exec_ts, not at submit_ts
# ---------------------------------------------------------------------------

class TestLatencyFillAtExecTime:
    """G5: quote changes inside the latency window; fill uses the later quote.

    quote1 at t=0: ask=5.00
    quote2 at t=500_000: ask=6.00
    order at t=0, latency=1_000_000, exec_ts=1_000_000
    -> latest quote at or before 1_000_000 is quote2 (t=500_000)
    -> fill at ask=6.00 (not 5.00)

    This verifies the no-lookahead-and-no-stale-fill property:
    the simulator does NOT fill at a quote the order could not have seen
    (i.e. it does NOT fill at a quote *after* exec_ts).
    """

    def test_fills_at_quote_at_exec_ts(self):
        sim = _make_sim(latency_ns=1_000_000, max_quote_age_ns=60_000_000_000)
        q1 = _call_quote(ts_ns=0, ask=5.00)
        q2 = _call_quote(ts_ns=500_000, ask=6.00)
        sim.feed([q1, q2])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        # exec_ts = 1_000_000; latest quote <= 1_000_000 is q2 (ts=500_000)
        assert fill.status == FillStatus.FILLED
        assert fill.price == 6.00, f"expected fill at 6.00, got {fill.price}"

    def test_quote_after_exec_ts_not_used(self):
        # quote3 at t=1_500_000 (after exec_ts=1_000_000) must NOT be used
        sim = _make_sim(latency_ns=1_000_000, max_quote_age_ns=60_000_000_000)
        q1 = _call_quote(ts_ns=0, ask=5.00)
        q2 = _call_quote(ts_ns=500_000, ask=6.00)
        q3 = _call_quote(ts_ns=1_500_000, ask=99.00)  # after exec_ts
        sim.feed([q1, q2, q3])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.price == 6.00, f"used post-exec quote; fill.price={fill.price}"


# ---------------------------------------------------------------------------
# G6: American instrument rejected with reason
# ---------------------------------------------------------------------------

class TestAmericanRejected:
    """G6: style_fn returns 'A' -> AMERICAN_REJECTED."""

    def test_american_rejected(self):
        sim = ChainSim(
            latency_ns=0,
            max_quote_age_ns=60_000_000_000,
            fee_per_contract=0.65,
            multiplier=_MUL,
            style_fn=lambda _: "A",  # always American
        )
        q = _call_quote(ts_ns=0, ask=5.00)
        sim.feed([q])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.AMERICAN_REJECTED
        assert fill.qty_filled == 0

    def test_european_allowed(self):
        sim = ChainSim(
            latency_ns=0,
            max_quote_age_ns=60_000_000_000,
            fee_per_contract=0.65,
            multiplier=_MUL,
            style_fn=lambda _: "E",
        )
        q = _call_quote(ts_ns=0, ask=5.00)
        sim.feed([q])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.FILLED


# ---------------------------------------------------------------------------
# G7: settle removes position; double-settle raises
# ---------------------------------------------------------------------------

class TestSettlePositionCleanup:
    """G7: settle removes position; second settle on same expiry raises."""

    def test_settle_removes_position(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        assert len(sim._positions) == 1
        sim.settle(_EXPIRY, fixing_price=4550.0)
        assert len(sim._positions) == 0

    def test_double_settle_raises(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        sim.settle(_EXPIRY, fixing_price=4550.0)
        # On second call there are no open positions for that expiry,
        # so no double-settle is detected (positions already removed).
        # The RuntimeError only fires if we somehow try to settle the same
        # position key twice — verify this via the internal set mechanism.
        # Force a second open of the same position and attempt double settle.
        sim2 = _make_sim()
        sim2.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim2.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                          is_call=True, side=1, qty=1))
        sim2.settle(_EXPIRY, fixing_price=4550.0)
        # Re-open same position (new buy after first settle)
        sim2.feed([_call_quote(ts_ns=1_000_000_000, ask=5.00)])
        sim2.submit(Order(ts_ns=1_000_000_000, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                          is_call=True, side=1, qty=1))
        # Second settle on same expiry key should raise
        with pytest.raises(RuntimeError, match="Double-settle"):
            sim2.settle(_EXPIRY, fixing_price=4600.0)

    def test_settle_different_expiry_ok(self):
        sim = _make_sim()
        # Two instruments: different expiry
        q1 = ChainQuote(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                        is_call=True, bid=4.50, ask=5.00, bid_size=10, ask_size=10)
        q2 = ChainQuote(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY_2,
                        is_call=True, bid=4.00, ask=4.50, bid_size=10, ask_size=10)
        sim.feed([q1, q2])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY_2,
                         is_call=True, side=1, qty=1))
        sim.settle(_EXPIRY, fixing_price=4550.0)
        sim.settle(_EXPIRY_2, fixing_price=4560.0)
        assert len(sim._positions) == 0


# ---------------------------------------------------------------------------
# G8: fee model integration golden
# ---------------------------------------------------------------------------

class TestFeeModelIntegration:
    """G8: fee_per_contract=1.25, qty=3 -> fee=3.75."""

    def test_fee_golden(self):
        sim = _make_sim(fee_per_contract=1.25)
        sim.feed([_call_quote(ts_ns=0, ask=5.00, ask_size=10)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=3)
        fill = sim.submit(order)
        assert fill.qty_filled == 3
        assert abs(fill.fee - 3.75) < 1e-9, f"fee={fill.fee}"

    def test_fee_model_object(self):
        fee_model = OptionsFeeModel(fee_per_contract=2.00)
        sim = ChainSim(
            latency_ns=0,
            max_quote_age_ns=60_000_000_000,
            fee_model=fee_model,
            multiplier=_MUL,
            style_fn=lambda _: "E",
        )
        sim.feed([_call_quote(ts_ns=0, ask=5.00, ask_size=10)])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=2)
        fill = sim.submit(order)
        assert abs(fill.fee - 4.00) < 1e-9


# ---------------------------------------------------------------------------
# G9: equity reconciliation — fills + settlements == realized_pnl
# ---------------------------------------------------------------------------

class TestEquityReconciliation:
    """G9: audit-trail invariant: sum(fills.cash_flow) + sum(settlements.pnl) == realized_pnl."""

    def test_reconcile_buy_call_itm(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        sim.settle(_EXPIRY, fixing_price=4550.0)
        assert sim.reconcile()

    def test_reconcile_mixed_legs(self):
        # Buy call + sell put, settle both
        sim = _make_sim()
        q_call = _call_quote(ts_ns=0, ask=5.00, bid=4.50)
        q_put = _put_quote(ts_ns=0, bid=3.00, ask=3.50)
        sim.feed([q_call, q_put])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=False, side=-1, qty=1))
        sim.settle(_EXPIRY, fixing_price=4550.0)
        assert sim.reconcile()

    def test_reconcile_result_matches(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        sim.settle(_EXPIRY, fixing_price=4550.0)
        result = sim.result()
        fill_cash = sum(
            f.cash_flow for f in result.fills
            if f.status in (FillStatus.FILLED, FillStatus.PARTIAL)
        )
        settle_pnl = sum(s["pnl"] for s in result.settlements)
        assert abs(fill_cash + settle_pnl - result.realized_pnl) < 1e-9


# ---------------------------------------------------------------------------
# G10: mtm() includes unrealized component
# ---------------------------------------------------------------------------

class TestMtmUnrealized:
    """G10: mtm() returns realized + unrealized when marks differ from avg_cost.

    Hand-computed goldens
    ----------------------
    Buy 1 call at ask=5.00:
      cash_flow_fill = -1 * 1 * 5.00 * 50 - 0.65 = -250.65
      avg_cost = 5.00 (per contract)

    Case A — mark unchanged (mark=5.00):
      unrealized = 1 * 50 * (5.00 - 5.00) = 0.00
      equity = -250.65 + 0.00 = -250.65

    Case B — mark rises to 7.00:
      unrealized = 1 * 50 * (7.00 - 5.00) = 100.00
      equity = -250.65 + 100.00 = -150.65

    Case C — mark falls to 3.00:
      unrealized = 1 * 50 * (3.00 - 5.00) = -100.00
      equity = -250.65 + (-100.00) = -350.65

    After settlement (fixing=4550, K=4500):
      settled_pnl = 1 * 50 * 50 = 2500
      fill_realized = -250.65
      no open positions -> unrealized = 0
      equity = -250.65 + 2500 = 2249.35
    """

    def _setup(self):
        sim = _make_sim()
        sim.feed([_call_quote(ts_ns=0, ask=5.00)])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        return sim

    def test_mtm_mark_unchanged(self):
        # Case A: mark == avg_cost -> unrealized = 0
        sim = self._setup()
        pos_key = _pos_key("ESM5", 4500.0, _EXPIRY, True)
        equity = sim.mtm(ts_ns=1_000_000, marks={pos_key: 5.00})
        assert abs(equity - (-250.65)) < 1e-9, f"equity={equity}"

    def test_mtm_mark_rises(self):
        # Case B: mark=7.00 -> unrealized=+100.00
        sim = self._setup()
        pos_key = _pos_key("ESM5", 4500.0, _EXPIRY, True)
        equity = sim.mtm(ts_ns=1_000_000, marks={pos_key: 7.00})
        expected = -250.65 + 100.00  # = -150.65
        assert abs(equity - expected) < 1e-9, f"equity={equity}, expected={expected}"

    def test_mtm_mark_falls(self):
        # Case C: mark=3.00 -> unrealized=-100.00
        sim = self._setup()
        pos_key = _pos_key("ESM5", 4500.0, _EXPIRY, True)
        equity = sim.mtm(ts_ns=1_000_000, marks={pos_key: 3.00})
        expected = -250.65 + (-100.00)  # = -350.65
        assert abs(equity - expected) < 1e-9, f"equity={equity}, expected={expected}"

    def test_mtm_after_settlement_no_unrealized(self):
        # After settlement position is removed; unrealized=0; equity = realized only
        sim = self._setup()
        sim.settle(_EXPIRY, fixing_price=4550.0)
        # No open positions; marks dict doesn't matter
        equity = sim.mtm(ts_ns=2_000_000, marks={})
        expected = -250.65 + 2500.0  # = 2249.35
        assert abs(equity - expected) < 1e-9, f"equity={equity}, expected={expected}"

    def test_mtm_equity_curve_appended(self):
        sim = self._setup()
        pos_key = _pos_key("ESM5", 4500.0, _EXPIRY, True)
        sim.mtm(ts_ns=1_000_000, marks={pos_key: 7.00})
        sim.mtm(ts_ns=2_000_000, marks={pos_key: 6.00})
        result = sim.result()
        assert len(result.mtm_equity_curve) == 2
        ts0, eq0 = result.mtm_equity_curve[0]
        ts1, eq1 = result.mtm_equity_curve[1]
        assert ts0 == 1_000_000
        assert ts1 == 2_000_000
        # First: mark=7 -> equity=-150.65; Second: mark=6 -> unrealized=+50 -> equity=-200.65
        assert abs(eq0 - (-150.65)) < 1e-9
        assert abs(eq1 - (-200.65)) < 1e-9


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_quote_returns_no_fill(self):
        sim = _make_sim()
        # No feed — no quote for instrument
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.NO_QUOTE

    def test_zero_display_size_rejected(self):
        sim = _make_sim()
        q = _call_quote(ts_ns=0, ask=5.00, ask_size=0)
        sim.feed([q])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=True, side=1, qty=1)
        fill = sim.submit(order)
        assert fill.status == FillStatus.ZERO_DISPLAY_SIZE

    def test_sell_at_bid_not_ask(self):
        sim = _make_sim()
        q = _put_quote(ts_ns=0, bid=3.00, ask=3.50, bid_size=5)
        sim.feed([q])
        order = Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                      is_call=False, side=-1, qty=1)
        fill = sim.submit(order)
        assert fill.price == 3.00  # sell at bid, NOT ask

    def test_order_side_validation(self):
        with pytest.raises(ValueError, match="side"):
            Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                  is_call=True, side=0, qty=1)

    def test_order_qty_validation(self):
        with pytest.raises(ValueError, match="qty"):
            Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                  is_call=True, side=1, qty=0)

    def test_chainquote_naive_expiry_raises(self):
        with pytest.raises(ValueError, match="tz-aware"):
            ChainQuote(ts_ns=0, symbol="ESM5", K=4500.0,
                       expiry_utc=datetime(2025, 6, 20, 20, 0, 0),  # naive
                       is_call=True, bid=4.50, ask=5.00, bid_size=5, ask_size=5)

    def test_settle_naive_expiry_raises(self):
        sim = _make_sim()
        with pytest.raises(ValueError, match="tz-aware"):
            sim.settle(datetime(2025, 6, 20, 20, 0, 0), fixing_price=4550.0)

    def test_put_itm_settlement(self):
        # put ITM: K=4500, fix=4400 -> intrinsic = max(-1*(4400-4500),0) = 100
        # buy 1 put: cash_flow = -(-1)*1*3.00*50 - 0.65... wait, buy put: side=+1
        # buy put at ask=3.50: cash_flow = -1 * 1 * 3.50 * 50 - 0.65 = -175.65
        # settlement: 1 * 50 * 100 = 5000
        # net = -175.65 + 5000 = 4824.35
        sim = _make_sim()
        q = _put_quote(ts_ns=0, bid=3.00, ask=3.50)
        sim.feed([q])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=False, side=1, qty=1))
        records = sim.settle(_EXPIRY, fixing_price=4400.0)
        assert len(records) == 1
        assert abs(records[0]["intrinsic_per_contract"] - 100.0) < 1e-9
        result = sim.result()
        expected_net = -1 * 1 * 3.50 * _MUL - 0.65 + 5000.0
        assert abs(result.realized_pnl - expected_net) < 1e-9

    def test_futures_fill_callback_invoked(self):
        """Seam test: futures_fill_callback is called with correct args on fill."""
        calls = []

        def cb(ts_ns, symbol, side, qty, price):
            calls.append((ts_ns, symbol, side, qty, price))

        sim = ChainSim(
            latency_ns=0,
            max_quote_age_ns=60_000_000_000,
            fee_per_contract=0.65,
            multiplier=_MUL,
            style_fn=lambda _: "E",
            futures_fill_callback=cb,
        )
        q = _call_quote(ts_ns=0, ask=5.00)
        sim.feed([q])
        sim.submit(Order(ts_ns=0, symbol="ESM5", K=4500.0, expiry_utc=_EXPIRY,
                         is_call=True, side=1, qty=1))
        assert len(calls) == 1
        _, sym, side, qty, price = calls[0]
        assert sym == "ESM5"
        assert side == 1
        assert qty == 1
        assert price == 5.00
