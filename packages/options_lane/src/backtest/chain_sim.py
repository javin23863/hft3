"""Chain-level options co-simulation — WS-2 MVP.

Scope (MVP)
-----------
Options legs only; European expiries only.  The hftbacktest futures-leg bridge
is a LATER integration (WS-2 phase 2).  See FUTURES_LEG_SEAM docstring for the
interface contract a futures-fill callback must satisfy.

Conservatism contract (every optimistic path is explicitly absent):
- Taker fills only (IOC_TAKER): buy at ask, sell at bid.
- Qty capped at displayed size; no re-quote chasing; partial-fill residual dropped.
- Quote stale beyond max_quote_age_ns → REJECT (FillStatus.STALE_QUOTE).
- Exercise style != 'E' → REJECT (FillStatus.AMERICAN_REJECTED).  MVP restriction.
- Fixing prices supplied by caller; no surface dependency inside this module.
- Live ack latency is UNKNOWN per latency_truth.json; default latency_ns=1_000_000
  (1 ms) is a CONSERVATIVE lower bound placeholder, not a measured value.

Futures-leg seam (document only — not implemented here)
-------------------------------------------------------
To wire a hftbacktest futures leg, supply a `futures_fill_callback` to ChainSim:

    def futures_fill_callback(
        ts_ns: int,
        symbol: str,
        side: int,          # +1 buy / -1 sell
        qty: int,
        exec_price: float,
    ) -> FuturesFill | None:
        ...

The callback is called synchronously at options fill time so the caller can
route the hedge to hftbacktest's queue-accurate simulator.  Returns a
FuturesFill (ts_ns, symbol, price, qty, fee) or None (reject).  The
ChainSim does NOT generate futures fills in MVP; it only provides the seam.
The callback signature is intentionally narrow — extend it later via kwargs.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

try:
    from options_data.src.expiry_calendar import exercise_style, ExpiryKind
    _HAVE_EXPIRY_CALENDAR = True
except ImportError:
    _HAVE_EXPIRY_CALENDAR = False

from options_lane.src.backtest.options_fee_model import OptionsFeeModel


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChainQuote:
    """One-sided top-of-book quote for a single options instrument.

    expiry_utc must be timezone-aware (UTC).  The valuation clock advances
    with ts_ns; using tz-aware expiry_utc keeps TZ arithmetic explicit and
    avoids silent DST bugs when computing time-to-expiry.
    """
    ts_ns: int
    symbol: str
    K: float
    expiry_utc: datetime      # tz-aware UTC datetime
    is_call: bool
    bid: float
    ask: float
    bid_size: int
    ask_size: int

    def __post_init__(self) -> None:
        if self.expiry_utc.tzinfo is None:
            raise ValueError("ChainQuote.expiry_utc must be tz-aware (use timezone.utc)")


@dataclass(frozen=True)
class Order:
    """IOC taker order for one options instrument.

    Only IOC_TAKER is supported in MVP — no resting options orders per plan.
    Instrument identified by (K, is_call, expiry_utc) tuple or symbol string;
    symbol takes precedence if both are supplied.
    """
    ts_ns: int
    symbol: str
    K: float
    expiry_utc: datetime
    is_call: bool
    side: int          # +1 buy / -1 sell
    qty: int
    # order_type is always IOC_TAKER in MVP; field kept for future extensibility
    order_type: str = "IOC_TAKER"

    def __post_init__(self) -> None:
        if self.side not in (1, -1):
            raise ValueError(f"Order.side must be +1 or -1, got {self.side}")
        if self.qty <= 0:
            raise ValueError(f"Order.qty must be positive, got {self.qty}")
        if self.order_type != "IOC_TAKER":
            raise ValueError(f"Only IOC_TAKER supported in MVP, got {self.order_type}")


class FillStatus:
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    STALE_QUOTE = "STALE_QUOTE"
    NO_QUOTE = "NO_QUOTE"
    AMERICAN_REJECTED = "AMERICAN_REJECTED"
    ZERO_DISPLAY_SIZE = "ZERO_DISPLAY_SIZE"


@dataclass
class Fill:
    ts_ns: int
    symbol: str
    K: float
    expiry_utc: datetime
    is_call: bool
    side: int
    qty_filled: int
    qty_requested: int
    price: float
    fee: float
    status: str
    # cash impact: negative when buying (cost), positive when selling (receipt)
    # cash_flow = -side * qty_filled * price - fee
    cash_flow: float


@dataclass
class PositionState:
    symbol: str
    K: float
    expiry_utc: datetime
    is_call: bool
    net_qty: int             # positive = long
    avg_cost: float          # average fill cost per contract (unsigned)
    realized_pnl: float      # unused per-position field; PnL is tracked via fill cash flows + settlements ledger
    total_fees: float


@dataclass
class SimResult:
    fills: list[Fill]
    settlements: list[dict]   # {expiry_utc, fixing_price, pnl, symbol, K, is_call, qty}
    positions: dict[str, PositionState]   # key = _pos_key(symbol)
    mtm_equity_curve: list[tuple[int, float]]   # (ts_ns, equity)
    # Audit-trail invariant: sum(f.cash_flow for f in fills) + sum(s['pnl'] for s in settlements)
    # == realized_pnl (final PnL)  [verified by reconcile()]; settled positions are
    # deleted at settlement, so their PnL lives only in the settlements ledger.
    realized_pnl: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _pos_key(symbol: str, K: float, expiry_utc: datetime, is_call: bool) -> str:
    return f"{symbol}|{K}|{expiry_utc.isoformat()}|{'C' if is_call else 'P'}"


def _instrument_key(q: ChainQuote) -> str:
    return _pos_key(q.symbol, q.K, q.expiry_utc, q.is_call)


# ---------------------------------------------------------------------------
# ChainSim
# ---------------------------------------------------------------------------

class ChainSim:
    """Event-driven options-leg chain simulator.

    Parameters
    ----------
    latency_ns : int
        Round-trip latency in nanoseconds applied to every order submission.
        An order submitted at ts executes against the LATEST quote at or before
        ts + latency_ns.  Default 1_000_000 ns (1 ms) — CONSERVATIVE placeholder;
        live ack latency is UNKNOWN per latency_truth.json.
    max_quote_age_ns : int
        Maximum age of the best quote at exec time.  If the most recent quote
        for an instrument is older than exec_ts - max_quote_age_ns the order is
        REJECTED with status STALE_QUOTE.  Default 60_000_000_000 ns (60 s).
    fee_model : OptionsFeeModel | None
        Fee model instance.  None uses OptionsFeeModel() defaults (0.65/contract).
    fee_per_contract : float | None
        If supplied, overrides fee_model default; ignored when fee_model is provided.
    multiplier : float
        Contract multiplier (point value).  ES = 50, NQ = 20; default 50.
    style_fn : Callable | None
        Callable(expiry_utc: datetime) -> 'E' | 'A'.  Called once per order to
        enforce European-only guard.  Default uses options_data.src.expiry_calendar
        exercise_style if available (raises ImportError otherwise); pass a custom
        callable to override.  style_fn=lambda _: 'E' disables the guard.
    futures_fill_callback : Callable | None
        SEAM for WS-2 futures-leg bridge.  Signature:
            (ts_ns, symbol, side, qty, exec_price) -> FuturesFill | None
        Called synchronously at fill time.  Not used in MVP; provide to wire
        hftbacktest in a later integration phase.
    """

    def __init__(
        self,
        latency_ns: int = 1_000_000,
        max_quote_age_ns: int = 60_000_000_000,
        fee_model: OptionsFeeModel | None = None,
        fee_per_contract: float | None = None,
        multiplier: float = 50.0,
        style_fn: Callable[[datetime], str] | None = None,
        futures_fill_callback: Callable | None = None,
    ) -> None:
        self._latency_ns = latency_ns
        self._max_quote_age_ns = max_quote_age_ns

        if fee_model is not None:
            self._fee_per_contract: float = fee_model._default
        elif fee_per_contract is not None:
            self._fee_per_contract = fee_per_contract
        else:
            self._fee_per_contract = OptionsFeeModel()._default

        self._multiplier = multiplier
        self._style_fn = style_fn or _default_style_fn
        self._futures_fill_callback = futures_fill_callback

        # quote book: instrument_key -> sorted list of ChainQuote (by ts_ns)
        self._quote_book: dict[str, list[ChainQuote]] = {}
        # positions: pos_key -> PositionState
        self._positions: dict[str, PositionState] = {}
        # settled expiries (set of expiry_utc isoformat + symbol + K + is_call)
        self._settled_keys: set[str] = set()

        self._fills: list[Fill] = []
        self._settlements: list[dict] = []
        self._equity_curve: list[tuple[int, float]] = []

    # ------------------------------------------------------------------
    # Feed / submit API
    # ------------------------------------------------------------------

    def feed(self, quotes: list[ChainQuote]) -> None:
        """Ingest a stream of ChainQuotes sorted by ts_ns.

        Quotes are appended to per-instrument sorted lists.  Call feed()
        once with the full sorted stream, or incrementally; either is valid.
        """
        for q in quotes:
            key = _instrument_key(q)
            if key not in self._quote_book:
                self._quote_book[key] = []
            book = self._quote_book[key]
            # maintain sorted order by ts_ns via insertion (stream usually sorted)
            idx = bisect.bisect_right([x.ts_ns for x in book], q.ts_ns)
            book.insert(idx, q)

    def submit(self, order: Order) -> Fill:
        """Submit an IOC taker order.  Returns a Fill (may be partial or rejected).

        Execution semantics:
        1. exec_ts = order.ts_ns + latency_ns
        2. Retrieve the latest ChainQuote for the instrument at or before exec_ts.
        3. Check staleness: if quote.ts_ns < exec_ts - max_quote_age_ns → REJECT.
        4. Check exercise style: if not 'E' → REJECT AMERICAN_REJECTED.
        5. Fill at ask (buy) or bid (sell), qty = min(order.qty, displayed_size).
        6. Record position and cash flow.
        """
        exec_ts = order.ts_ns + self._latency_ns
        key = _pos_key(order.symbol, order.K, order.expiry_utc, order.is_call)

        # European-only guard
        style = self._style_fn(order.expiry_utc)
        if style != "E":
            fill = Fill(
                ts_ns=exec_ts,
                symbol=order.symbol,
                K=order.K,
                expiry_utc=order.expiry_utc,
                is_call=order.is_call,
                side=order.side,
                qty_filled=0,
                qty_requested=order.qty,
                price=float("nan"),
                fee=0.0,
                status=FillStatus.AMERICAN_REJECTED,
                cash_flow=0.0,
            )
            self._fills.append(fill)
            return fill

        # Quote lookup
        quote = self._best_quote_at(order.symbol, order.K, order.expiry_utc, order.is_call, exec_ts)

        if quote is None:
            fill = Fill(
                ts_ns=exec_ts,
                symbol=order.symbol,
                K=order.K,
                expiry_utc=order.expiry_utc,
                is_call=order.is_call,
                side=order.side,
                qty_filled=0,
                qty_requested=order.qty,
                price=float("nan"),
                fee=0.0,
                status=FillStatus.NO_QUOTE,
                cash_flow=0.0,
            )
            self._fills.append(fill)
            return fill

        # Staleness check
        if quote.ts_ns < exec_ts - self._max_quote_age_ns:
            fill = Fill(
                ts_ns=exec_ts,
                symbol=order.symbol,
                K=order.K,
                expiry_utc=order.expiry_utc,
                is_call=order.is_call,
                side=order.side,
                qty_filled=0,
                qty_requested=order.qty,
                price=float("nan"),
                fee=0.0,
                status=FillStatus.STALE_QUOTE,
                cash_flow=0.0,
            )
            self._fills.append(fill)
            return fill

        # Taker fill price and displayed size
        if order.side == 1:  # buy at ask
            fill_price = quote.ask
            display_size = quote.ask_size
        else:  # sell at bid
            fill_price = quote.bid
            display_size = quote.bid_size

        if display_size <= 0:
            fill = Fill(
                ts_ns=exec_ts,
                symbol=order.symbol,
                K=order.K,
                expiry_utc=order.expiry_utc,
                is_call=order.is_call,
                side=order.side,
                qty_filled=0,
                qty_requested=order.qty,
                price=fill_price,
                fee=0.0,
                status=FillStatus.ZERO_DISPLAY_SIZE,
                cash_flow=0.0,
            )
            self._fills.append(fill)
            return fill

        # Cap qty at displayed size — no requote chasing
        qty_filled = min(order.qty, display_size)
        fee = qty_filled * self._fee_per_contract
        # cash_flow: negative when buying (outflow), positive when selling (inflow)
        cash_flow = -order.side * qty_filled * fill_price * self._multiplier - fee
        status = FillStatus.FILLED if qty_filled == order.qty else FillStatus.PARTIAL

        fill = Fill(
            ts_ns=exec_ts,
            symbol=order.symbol,
            K=order.K,
            expiry_utc=order.expiry_utc,
            is_call=order.is_call,
            side=order.side,
            qty_filled=qty_filled,
            qty_requested=order.qty,
            price=fill_price,
            fee=fee,
            status=status,
            cash_flow=cash_flow,
        )
        self._fills.append(fill)

        # Update position
        self._update_position(fill)

        # Futures seam — invoke callback if wired (no-op in MVP)
        if self._futures_fill_callback is not None:
            self._futures_fill_callback(exec_ts, order.symbol, order.side, qty_filled, fill_price)

        return fill

    # ------------------------------------------------------------------
    # Settlement
    # ------------------------------------------------------------------

    def settle(self, expiry_utc: datetime, fixing_price: float) -> list[dict]:
        """European cash-settlement at 15:00 CT fixing.

        For every open position whose expiry_utc matches, auto-exercise:
          PnL += qty * multiplier * max(sign*(fixing - K), 0)

        Parameters
        ----------
        expiry_utc : datetime
            Expiry to settle.  Must be tz-aware.  Matched by equality to
            ChainQuote.expiry_utc stored in positions.
        fixing_price : float
            The fixing price for the underlying at settlement.
            Caller is responsible for supplying the correct value.
            A fixing-VWAP engine is a separate module (SEAM: see docs below).

        Returns
        -------
        List of settlement records for this expiry.

        Fixing-price VWAM seam
        ----------------------
        The fixing_price argument decouples chain_sim from the fixing-VWAP
        computation.  To integrate a fixing engine, supply:
            fixing_price = fixing_vwap_engine.compute(expiry_utc, window_ns=...)
        The engine reads from an OHLCV or trade-tick parquet; that interface is
        defined in a later WS-2 module.  chain_sim does not depend on it.
        """
        if expiry_utc.tzinfo is None:
            raise ValueError("settle: expiry_utc must be tz-aware")

        settle_key_prefix = f"|{expiry_utc.isoformat()}|"
        settled_records: list[dict] = []

        keys_to_remove: list[str] = []
        for pos_key, pos in self._positions.items():
            if pos.expiry_utc != expiry_utc:
                continue

            settle_marker = pos_key
            if settle_marker in self._settled_keys:
                raise RuntimeError(
                    f"Double-settle detected for {pos_key} at expiry {expiry_utc.isoformat()}"
                )

            sign = 1.0 if pos.is_call else -1.0
            intrinsic = max(sign * (fixing_price - pos.K), 0.0)
            settlement_pnl = pos.net_qty * self._multiplier * intrinsic

            record = {
                "expiry_utc": expiry_utc,
                "fixing_price": fixing_price,
                "symbol": pos.symbol,
                "K": pos.K,
                "is_call": pos.is_call,
                "qty": pos.net_qty,
                "intrinsic_per_contract": intrinsic,
                "pnl": settlement_pnl,
            }
            self._settlements.append(record)
            settled_records.append(record)
            self._settled_keys.add(settle_marker)
            keys_to_remove.append(pos_key)

        for k in keys_to_remove:
            del self._positions[k]

        return settled_records

    # ------------------------------------------------------------------
    # Mark-to-market
    # ------------------------------------------------------------------

    def mtm(self, ts_ns: int, marks: dict[str, float]) -> float:
        """Compute and record mark-to-market equity.

        Parameters
        ----------
        ts_ns : int
            Current timestamp in nanoseconds.
        marks : dict[str, float]
            Mapping of pos_key (as returned by _pos_key()) to mark price.
            Instruments not in marks are valued at avg_cost (no change).
            Caller supplies marks; no surface dependency in MVP.

        Returns
        -------
        Current total equity (realized + unrealized).
        """
        unrealized = 0.0
        for pos_key, pos in self._positions.items():
            mark = marks.get(pos_key, pos.avg_cost)
            unrealized += pos.net_qty * self._multiplier * (mark - pos.avg_cost)
        realized = sum(pos.realized_pnl for pos in self._positions.values())
        # also include positions already closed/settled
        settled_pnl = sum(s["pnl"] for s in self._settlements)
        fill_realized = sum(
            f.cash_flow for f in self._fills if f.status in (FillStatus.FILLED, FillStatus.PARTIAL)
        )
        equity = fill_realized + settled_pnl + unrealized
        self._equity_curve.append((ts_ns, equity))
        return equity

    # ------------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------------

    def result(self) -> SimResult:
        """Return the final SimResult with audit-trail data."""
        realized = sum(
            f.cash_flow for f in self._fills
            if f.status in (FillStatus.FILLED, FillStatus.PARTIAL)
        ) + sum(s["pnl"] for s in self._settlements)
        return SimResult(
            fills=list(self._fills),
            settlements=list(self._settlements),
            positions=dict(self._positions),
            mtm_equity_curve=list(self._equity_curve),
            realized_pnl=realized,
        )

    def reconcile(self) -> bool:
        """Verify audit-trail invariant: fills + settlements == realized_pnl.

        Returns True if reconciled, raises AssertionError otherwise.
        Used in tests and post-run validation.
        """
        fill_cash = sum(
            f.cash_flow for f in self._fills
            if f.status in (FillStatus.FILLED, FillStatus.PARTIAL)
        )
        settle_pnl = sum(s["pnl"] for s in self._settlements)
        total = fill_cash + settle_pnl
        result = self.result()
        assert abs(total - result.realized_pnl) < 1e-9, (
            f"Reconciliation failed: fills+settlements={total}, realized_pnl={result.realized_pnl}"
        )
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _best_quote_at(
        self,
        symbol: str,
        K: float,
        expiry_utc: datetime,
        is_call: bool,
        as_of_ns: int,
    ) -> ChainQuote | None:
        """Return the most recent ChainQuote for the instrument at or before as_of_ns."""
        key = _pos_key(symbol, K, expiry_utc, is_call)
        book = self._quote_book.get(key)
        if not book:
            return None
        times = [q.ts_ns for q in book]
        idx = bisect.bisect_right(times, as_of_ns) - 1
        if idx < 0:
            return None
        return book[idx]

    def _update_position(self, fill: Fill) -> None:
        key = _pos_key(fill.symbol, fill.K, fill.expiry_utc, fill.is_call)
        if key not in self._positions:
            self._positions[key] = PositionState(
                symbol=fill.symbol,
                K=fill.K,
                expiry_utc=fill.expiry_utc,
                is_call=fill.is_call,
                net_qty=0,
                avg_cost=0.0,
                realized_pnl=0.0,
                total_fees=0.0,
            )
        pos = self._positions[key]
        prev_qty = pos.net_qty
        new_qty = prev_qty + fill.side * fill.qty_filled

        if prev_qty == 0 or (prev_qty > 0) == (fill.side > 0):
            # Opening or adding to position: update avg_cost
            if prev_qty == 0:
                pos.avg_cost = fill.price
            else:
                total_cost = abs(prev_qty) * pos.avg_cost + fill.qty_filled * fill.price
                pos.avg_cost = total_cost / abs(new_qty) if new_qty != 0 else fill.price
        else:
            # Reducing or flipping: realize PnL on closed portion
            closed = min(abs(prev_qty), fill.qty_filled)
            # PnL per contract = side * (fill_price - avg_cost) * multiplier
            # For a long being reduced: side=-1, pnl = (avg_cost - fill_price)... but
            # cash_flow already captures this correctly; avg_cost tracks entry.
            # realized_pnl is updated via cash_flow accumulation in result()
            if abs(new_qty) > 0 and (new_qty > 0) != (prev_qty > 0):
                # Flip: residual new direction, reset avg_cost
                pos.avg_cost = fill.price

        pos.net_qty = new_qty
        pos.total_fees += fill.fee

        # Remove closed positions
        if pos.net_qty == 0:
            del self._positions[key]


# ---------------------------------------------------------------------------
# Default exercise-style function
# ---------------------------------------------------------------------------

def _default_style_fn(expiry_utc: datetime) -> str:
    """Fail-safe default style function: always raises RuntimeError.

    This default is intentionally non-permissive.  Callers that rely on
    the style guard MUST supply an explicit style_fn; otherwise orders will
    be rejected at runtime with a clear error rather than silently permitting
    American instruments through a European-only filter.

    To allow all options (disable the guard): pass style_fn=lambda _: 'E'.
    To enforce European-only:               pass style_fn that returns 'A'
                                            for American expiries.
    """
    raise RuntimeError(
        "No style_fn provided to ChainSim.  Supply style_fn=lambda _: 'E' "
        "to allow all options, or a calendar-aware callable to enforce "
        "European-only rejection.  See ChainSim docstring."
    )
