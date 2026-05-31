"""Config-driven universe screener with auditable filter results."""
from __future__ import annotations

from pathlib import Path

from equities_lane.src.ingest.daily_bars_io import load_daily_bars
from equities_lane.src.ingest.float_metadata import bars_before, load_float_csv, lookup_float
from equities_lane.src.ingest.session_io import load_session
from equities_lane.src.l3_policy import require_l3_session
from equities_lane.src.models import SessionTick
from equities_lane.src.types import FilterConfig, FilterResult, ScreenResult, SessionMeta


def screen_session(
    session_path: str | Path,
    filters: FilterConfig,
    float_csv: str | Path,
    daily_bars_path: str | Path,
    *,
    daily_bars_fallback: str | Path | None = None,
    l3_only: bool = True,
    allow_degraded: bool = False,
) -> ScreenResult:
    meta, ticks = load_session(session_path)
    require_l3_session(meta, l3_only=l3_only, allow_degraded=allow_degraded, context="screen")
    float_records = load_float_csv(float_csv)
    daily_bars = load_daily_bars(daily_bars_path, symbol=meta.symbol)
    if not daily_bars and daily_bars_fallback is not None:
        daily_bars = load_daily_bars(daily_bars_fallback, symbol=meta.symbol)
    results: list[FilterResult] = []
    assumptions: list[str] = list(meta.degraded.assumptions)

    float_rec = lookup_float(float_records, meta.symbol, meta.session_date)
    results.append(_check_float(filters, float_rec))
    results.append(_check_gap(filters, meta))
    results.append(_check_premarket_volume(filters, ticks, meta))
    results.append(_check_rvol(filters, ticks, meta, daily_bars))
    results.append(_check_float_rotation(filters, ticks, float_rec))
    results.append(_check_prior_runner(filters, meta, daily_bars))
    if filters.overhead_resistance:
        results.append(_check_overhead(filters, meta, daily_bars))
    else:
        results.append(
            FilterResult(
                name="overhead_resistance",
                enabled=False,
                status="skip",
                value=None,
                threshold=None,
                reason="disabled in config",
            )
        )

    enabled_results = [r for r in results if r.enabled and r.status != "skip"]
    passed = all(r.status == "pass" for r in enabled_results) if enabled_results else False
    for r in results:
        if r.status == "skip" and r.reason:
            assumptions.append(f"{r.name}: {r.reason}")

    return ScreenResult(
        symbol=meta.symbol,
        session_date=meta.session_date,
        passed=passed,
        filters=results,
        assumptions=assumptions,
    )


def _check_float(filters: FilterConfig, rec) -> FilterResult:
    enabled, threshold = filters.float_max_shares
    if not enabled:
        return FilterResult("float_max_shares", False, "skip", None, threshold, "disabled")
    if rec is None:
        return FilterResult("float_max_shares", True, "skip", None, threshold, "no float metadata")
    ok = rec.float_shares <= threshold
    return FilterResult(
        "float_max_shares",
        True,
        "pass" if ok else "fail",
        rec.float_shares,
        threshold,
    )


def _check_gap(filters: FilterConfig, meta: SessionMeta) -> FilterResult:
    enabled, threshold = filters.min_premarket_gap_pct
    if not enabled:
        return FilterResult("min_premarket_gap_pct", False, "skip", None, threshold, "disabled")
    if meta.prior_close <= 0:
        return FilterResult(
            "min_premarket_gap_pct", True, "skip", None, threshold, "missing prior_close"
        )
    gap_pct = (meta.premarket_open - meta.prior_close) / meta.prior_close * 100.0
    ok = gap_pct >= threshold
    return FilterResult(
        "min_premarket_gap_pct",
        True,
        "pass" if ok else "fail",
        gap_pct,
        threshold,
    )


def _premarket_ticks(ticks: list[SessionTick], meta: SessionMeta) -> list[SessionTick]:
    """Ticks before regular session open (09:30 ET approximated via first 30% of tape)."""
    if not ticks:
        return []
    cutoff_idx = max(1, len(ticks) // 5)
    return ticks[:cutoff_idx]


def _check_premarket_volume(
    filters: FilterConfig,
    ticks: list[SessionTick],
    meta: SessionMeta,
) -> FilterResult:
    enabled, threshold = filters.min_premarket_volume
    if not enabled:
        return FilterResult("min_premarket_volume", False, "skip", None, threshold, "disabled")
    pm = _premarket_ticks(ticks, meta)
    vol = sum(t.trade_sz or 0 for t in pm)
    if vol == 0 and not pm:
        return FilterResult(
            "min_premarket_volume", True, "skip", None, threshold, "no pre-market tape"
        )
    ok = vol >= threshold
    return FilterResult(
        "min_premarket_volume",
        True,
        "pass" if ok else "fail",
        float(vol),
        threshold,
    )


def _check_rvol(
    filters: FilterConfig,
    ticks: list[SessionTick],
    meta: SessionMeta,
    daily_bars,
) -> FilterResult:
    enabled, threshold = filters.min_rvol
    if not enabled:
        return FilterResult("min_rvol", False, "skip", None, threshold, "disabled")
    hist = bars_before(daily_bars, meta.symbol, meta.session_date)
    lookback = hist[-filters.rvol_lookback_days :]
    if not lookback:
        return FilterResult("min_rvol", True, "skip", None, threshold, "insufficient history")
    avg_vol = sum(b.volume for b in lookback) / len(lookback)
    session_vol = sum(t.trade_sz or 0 for t in ticks) or sum(t.bid_sz + t.ask_sz for t in ticks)
    rvol = session_vol / avg_vol if avg_vol > 0 else 0.0
    ok = rvol >= threshold
    return FilterResult("min_rvol", True, "pass" if ok else "fail", rvol, threshold)


def _check_float_rotation(filters: FilterConfig, ticks: list[SessionTick], rec) -> FilterResult:
    enabled, threshold = filters.min_float_rotation
    if not enabled:
        return FilterResult("min_float_rotation", False, "skip", None, threshold, "disabled")
    if rec is None or rec.float_shares <= 0:
        return FilterResult(
            "min_float_rotation", True, "skip", None, threshold, "no float metadata"
        )
    vol = sum(t.trade_sz or 0 for t in ticks)
    rotation = vol / rec.float_shares
    ok = rotation >= threshold
    return FilterResult(
        "min_float_rotation",
        True,
        "pass" if ok else "fail",
        rotation,
        threshold,
    )


def _check_prior_runner(filters: FilterConfig, meta: SessionMeta, daily_bars) -> FilterResult:
    if not filters.prior_runner:
        return FilterResult("prior_runner", False, "skip", None, None, "disabled")
    hist = bars_before(daily_bars, meta.symbol, meta.session_date)
    if len(hist) < 2:
        return FilterResult("prior_runner", True, "skip", None, None, "insufficient history")
    prev = hist[-1]
    run_pct = (prev.close - prev.open) / prev.open * 100.0 if prev.open > 0 else 0.0
    ok = run_pct >= filters.min_prior_run_pct
    return FilterResult(
        "prior_runner",
        True,
        "pass" if ok else "fail",
        run_pct,
        filters.min_prior_run_pct,
    )


def _check_overhead(filters: FilterConfig, meta: SessionMeta, daily_bars) -> FilterResult:
    hist = bars_before(daily_bars, meta.symbol, meta.session_date)
    window = hist[-filters.consolidation_days :]
    if len(window) < filters.consolidation_days:
        return FilterResult(
            "overhead_resistance", True, "skip", None, None, "insufficient consolidation window"
        )
    highs = [b.high for b in window]
    breakout = meta.prior_close > max(highs[:-1]) if len(highs) > 1 else False
    return FilterResult(
        "overhead_resistance",
        True,
        "pass" if breakout else "fail",
        meta.prior_close,
        max(highs[:-1]) if len(highs) > 1 else None,
    )
