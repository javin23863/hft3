"""Build session metadata from real daily bars and intraday tape."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from equities_lane.src.ingest.float_metadata import bars_before, load_daily_bars
from equities_lane.src.models import DailyBar, SessionTick
from equities_lane.src.types import DegradedModeFlags, SessionMeta

ET = ZoneInfo("America/New_York")


def prior_close_from_bars(
    bars: list[DailyBar],
    symbol: str,
    session_date: str,
) -> float | None:
    hist = bars_before(bars, symbol, session_date)
    if not hist:
        return None
    return hist[-1].close


def premarket_open_from_ticks(ticks: list[SessionTick], session_date: str) -> float | None:
    """First trade or mid quote at/after 04:00 ET on session_date."""
    if not ticks:
        return None
    target = date.fromisoformat(session_date)
    session_open_et = datetime(
        target.year, target.month, target.day, 9, 30, tzinfo=ET
    )
    premarket_start_et = datetime(
        target.year, target.month, target.day, 4, 0, tzinfo=ET
    )
    open_ns = int(session_open_et.timestamp() * 1e9)
    start_ns = int(premarket_start_et.timestamp() * 1e9)

    for t in ticks:
        if t.ts_ns < start_ns:
            continue
        if t.ts_ns >= open_ns:
            break
        if t.trade_px and t.trade_px > 0:
            return t.trade_px
        mid = _mid(t)
        if mid > 0:
            return mid
    return None


def build_session_meta(
    symbol: str,
    session_date: str,
    ticks: list[SessionTick],
    daily_bars_path: str | None = None,
    daily_bars: list[DailyBar] | None = None,
    *,
    schema: str = "mbo",
) -> SessionMeta:
    assumptions: list[str] = []
    bars = daily_bars
    if bars is None and daily_bars_path:
        bars = load_daily_bars(daily_bars_path)

    prior = prior_close_from_bars(bars or [], symbol, session_date)
    if prior is None:
        assumptions.append("prior_close missing from daily OHLCV; used first tape mid")
        prior = _mid(ticks[0]) if ticks else 0.0

    pm_open = premarket_open_from_ticks(ticks, session_date)
    if pm_open is None:
        assumptions.append("no premarket prints; used first RTH trade/mid")
        for t in ticks:
            if t.trade_px and t.trade_px > 0:
                pm_open = t.trade_px
                break
            mid = _mid(t)
            if mid > 0:
                pm_open = mid
                break
        if pm_open is None:
            pm_open = prior
            assumptions.append("premarket_open fell back to prior_close")

    degraded = DegradedModeFlags(
        degraded_mode=schema != "mbo",
        assumptions=(
            [f"schema={schema}; L3 features degraded"] if schema != "mbo" else []
        )
        + assumptions,
    )
    return SessionMeta(
        symbol=symbol,
        session_date=session_date,
        prior_close=float(prior),
        premarket_open=float(pm_open),
        degraded=degraded,
    )


def _mid(t: SessionTick) -> float:
    if t.bid_px > 0 and t.ask_px > 0:
        return (t.bid_px + t.ask_px) / 2.0
    return t.trade_px or 0.0
