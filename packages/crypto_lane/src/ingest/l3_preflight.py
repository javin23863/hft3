"""Preflight true-L3 bookticker fillability before destructive purge."""
from __future__ import annotations

from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.binance_vision_pull import _months_for_days, vision_monthly_url
from crypto_lane.src.ingest.bookticker_quality import (
    classify_bookticker_day,
    missing_bookticker_days,
    synthetic_bookticker_days,
)
from crypto_lane.src.ingest.gold_pull import _symbol_map
from crypto_lane.src.ingest.gold_reader import gold_key, resolve_gold_bucket


def probe_vision_month(symbol: str, year: int, month: int, *, timeout_s: int = 30) -> str:
    """Return: available | not_found | network_error."""
    url = vision_monthly_url(symbol, year, month)
    req = Request(url, headers={"User-Agent": "hft3-crypto-vision-preflight/1.0"})
    try:
        with urlopen(req, timeout=timeout_s) as resp:
            if resp.status in (200, 206):
                return "available"
            return f"http_{resp.status}"
    except HTTPError as exc:
        if exc.code == 404:
            return "not_found"
        return f"http_{exc.code}"
    except URLError:
        return "network_error"


def _b2_probe(
    days: list[date],
    *,
    max_error_samples: int = 5,
) -> dict[str, Any]:
    ensure_crypto_env()
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    client = B2Client()
    bucket = resolve_gold_bucket("binance")
    available: list[str] = []
    missing: list[str] = []
    error_samples: list[dict[str, str]] = []
    for day in days:
        key = gold_key("binance", symbol, day, "futures_um_bookticker_tick")
        try:
            if client.file_exists(bucket, key):
                available.append(day.isoformat())
            else:
                missing.append(day.isoformat())
        except B2ClientError as exc:
            missing.append(day.isoformat())
            if len(error_samples) < max_error_samples:
                error_samples.append({"day": day.isoformat(), "error": str(exc)})
    return {
        "bucket": bucket,
        "available_days": available,
        "missing_days": missing,
        "available_count": len(available),
        "missing_count": len(missing),
        "error_samples": error_samples,
    }


def preflight_l3_gaps(
    *,
    start: str,
    end: str,
    vision_probe: bool = True,
) -> dict[str, Any]:
    """Report B2/Vision fillability for missing days (no downloads, no deletes)."""
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    missing = missing_bookticker_days(start=start, end=end)
    synthetic = synthetic_bookticker_days(start=start, end=end)

    b2 = _b2_probe(missing)
    vision_months: dict[str, dict[str, Any]] = {}
    vision_available_days = 0
    vision_not_found_days = 0

    if vision_probe and missing:
        for year, month, month_days in _months_for_days(missing):
            label = f"{year}-{month:02d}"
            status = probe_vision_month(symbol, year, month)
            vision_months[label] = {
                "status": status,
                "days_needed": len(month_days),
                "url": vision_monthly_url(symbol, year, month),
            }
            if status == "available":
                vision_available_days += len(month_days)
            elif status == "not_found":
                vision_not_found_days += len(month_days)

    purge_safe = (
        len(synthetic) == 0
        or b2["available_count"] >= len(synthetic)
    )
    return {
        "start": start,
        "end": end,
        "missing_days": len(missing),
        "synthetic_days": len(synthetic),
        "b2": b2,
        "vision_months": vision_months,
        "vision_available_days_estimate": vision_available_days,
        "vision_not_found_days": vision_not_found_days,
        "true_l3_b2_fillable": b2["available_count"],
        "purge_safe": purge_safe,
        "purge_block_reason": (
            None
            if purge_safe
            else (
                f"B2 has {b2['available_count']}/{len(synthetic)} synthetic days; "
                "Vision monthly alone is not purge-safe (archives may be incomplete). "
                "Run CAE bookticker backfill to B2, or pass --force to purge anyway."
            )
        ),
        "recommendation": _recommendation(
            missing=len(missing),
            synthetic=len(synthetic),
            b2_available=b2["available_count"],
            vision_not_found=vision_not_found_days,
            purge_safe=purge_safe,
        ),
    }


def _recommendation(
    *,
    missing: int,
    synthetic: int,
    b2_available: int,
    vision_not_found: int,
    purge_safe: bool,
) -> str:
    if missing == 0 and synthetic == 0:
        return "no_action"
    if b2_available >= missing:
        return "run_fill_l3_gaps"
    if b2_available > 0:
        return "run_fill_l3_gaps_partial_b2"
    if vision_not_found > 0 and missing > 0:
        return "cae_b2_backfill_required_or_allow_degraded"
    if not purge_safe and synthetic > 0:
        return "do_not_replace_synthetic_until_b2_ready"
    return "run_fill_l3_gaps_then_allow_degraded_if_still_missing"
