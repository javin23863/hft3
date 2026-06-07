"""Preflight true-L3 bookticker fillability before destructive purge."""
from __future__ import annotations

from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.binance_vision_pull import _months_for_days, vision_monthly_url
from crypto_lane.src.ingest.b2_synthetic_probe_cache import (
    load_cached_b2_synthetic_probe,
    save_cached_b2_synthetic_probe,
)
from crypto_lane.src.ingest.bookticker_quality import summarize_bookticker_range
from crypto_lane.src.ingest.gold_pull import _symbol_map
from crypto_lane.src.ingest.gold_reader import gold_key, resolve_gold_bucket
from crypto_lane.src.ingest.mempool_preflight import _sample_probe_days

SYNTHETIC_B2_PROBE_MAX_DAYS = 31
MISSING_B2_PROBE_MAX_DAYS = 31


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


def _empty_b2_probe(bucket: str) -> dict[str, Any]:
    return {
        "bucket": bucket,
        "available_days": [],
        "missing_days": [],
        "available_count": 0,
        "missing_count": 0,
        "error_samples": [],
        "sampled": False,
        "probe_days": 0,
    }


def b2_probe_bookticker_days(
    days: list[date],
    *,
    max_error_samples: int = 5,
) -> dict[str, Any]:
    """Probe B2 for futures_um_bookticker_tick on specific calendar days."""
    ensure_crypto_env()
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    client = B2Client()
    bucket = resolve_gold_bucket("binance")
    if not days:
        return _empty_b2_probe(bucket)
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
        "sampled": False,
        "probe_days": len(days),
    }


# Backward-compatible alias for tests patching _b2_probe
_b2_probe = b2_probe_bookticker_days


def b2_probe_bookticker_days_sampled(
    days: list[date],
    *,
    max_probe_days: int | None = SYNTHETIC_B2_PROBE_MAX_DAYS,
    max_error_samples: int = 5,
) -> dict[str, Any]:
    """Sampled B2 probe for large day lists (audit estimates only)."""
    ensure_crypto_env()
    bucket = resolve_gold_bucket("binance")
    if not days:
        return _empty_b2_probe(bucket)
    probe_days = (
        _sample_probe_days(days, max_probe_days)
        if max_probe_days and len(days) > max_probe_days
        else days
    )
    probe = b2_probe_bookticker_days(probe_days, max_error_samples=max_error_samples)
    probed_hits = int(probe["available_count"])
    if len(probe_days) < len(days):
        ratio = probed_hits / max(len(probe_days), 1)
        estimated = int(round(ratio * len(days)))
        return {
            **probe,
            "probed_available_count": probed_hits,
            "available_count": estimated,
            "missing_count": max(0, len(days) - estimated),
            "sampled": True,
            "probe_days": len(probe_days),
        }
    return {**probe, "probed_available_count": probed_hits}


def _non_synthetic_missing(missing: list[date], synthetic: list[str]) -> list[date]:
    syn_set = set(synthetic)
    return [d for d in missing if d.isoformat() not in syn_set]


def preflight_l3_gaps(
    *,
    start: str,
    end: str,
    vision_probe: bool = True,
    bookticker_summary: dict[str, Any] | None = None,
    full_synthetic_b2_probe: bool = True,
    use_b2_synthetic_cache: bool = True,
) -> dict[str, Any]:
    """Report B2/Vision fillability for missing days (no downloads, no deletes)."""
    symbol = _symbol_map().get("binance_perp", "BTCUSDT")
    summary = bookticker_summary or summarize_bookticker_range(start=start, end=end)
    missing = list(summary["missing"])
    synthetic = list(summary["synthetic"])
    missing_non_syn = _non_synthetic_missing(missing, synthetic)

    b2 = b2_probe_bookticker_days_sampled(
        missing_non_syn,
        max_probe_days=MISSING_B2_PROBE_MAX_DAYS,
    )
    syn_dates = [date.fromisoformat(d) for d in synthetic]
    bucket = resolve_gold_bucket("binance")
    b2_synthetic: dict[str, Any]
    if not syn_dates:
        b2_synthetic = _empty_b2_probe(bucket)
    elif use_b2_synthetic_cache and (cached := load_cached_b2_synthetic_probe(synthetic)):
        b2_synthetic = cached
    elif full_synthetic_b2_probe:
        b2_synthetic = b2_probe_bookticker_days(syn_dates)
        if use_b2_synthetic_cache:
            save_cached_b2_synthetic_probe(synthetic, b2_synthetic)
    else:
        b2_synthetic = _empty_b2_probe(bucket)
        b2_synthetic["skipped"] = True
    synth_n = len(synthetic)
    if syn_dates and b2_synthetic.get("skipped"):
        b2_synthetic_estimate = b2_probe_bookticker_days_sampled(syn_dates)
    elif syn_dates:
        b2_synthetic_estimate = {
            **{k: v for k, v in b2_synthetic.items() if k not in ("from_cache", "cache_age_hours")},
            "sampled": False,
            "probe_days": synth_n,
        }
    else:
        b2_synthetic_estimate = _empty_b2_probe(bucket)

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

    b2_on_synthetic = int(b2_synthetic["available_count"])
    b2_on_synthetic_est = int(b2_synthetic_estimate["available_count"])
    purge_safe = synth_n == 0 or b2_on_synthetic >= synth_n
    purge_safe_estimate = synth_n == 0 or b2_on_synthetic_est >= synth_n
    return {
        "start": start,
        "end": end,
        "missing_days": len(missing),
        "missing_non_synthetic_days": len(missing_non_syn),
        "synthetic_days": synth_n,
        "synthetic_day_list": synthetic,
        "b2": b2,
        "b2_synthetic": b2_synthetic,
        "b2_synthetic_estimate": b2_synthetic_estimate,
        "vision_months": vision_months,
        "vision_available_days_estimate": vision_available_days,
        "vision_not_found_days": vision_not_found_days,
        "b2_missing_non_synthetic_probe": b2,
        "b2_missing_non_synthetic_fillable_estimate": b2["available_count"],
        "b2_missing_non_synthetic_fillable_probed": int(b2.get("probed_available_count", 0)),
        "true_l3_b2_fillable": int(b2.get("probed_available_count", b2["available_count"])),
        "b2_synthetic_fillable": b2_on_synthetic,
        "b2_synthetic_fillable_estimate": b2_on_synthetic_est,
        "b2_synthetic_from_cache": bool(b2_synthetic.get("from_cache")),
        "purge_safe": purge_safe,
        "purge_safe_estimate": purge_safe_estimate,
        "purge_block_reason": (
            None
            if purge_safe
            else (
                f"B2 has {b2_on_synthetic}/{synth_n} synthetic-replacement days (full probe); "
                "Vision monthly alone is not purge-safe (archives may be incomplete). "
                "Run CAE bookticker backfill to B2, or pass --force to purge anyway."
            )
        ),
        "recommendation": _recommendation(
            missing_non_synthetic=len(missing_non_syn),
            synthetic=synth_n,
            b2=b2,
            b2_synthetic_available=b2_on_synthetic,
            vision_not_found=vision_not_found_days,
            purge_safe=purge_safe,
        ),
    }


def _recommendation(
    *,
    missing_non_synthetic: int,
    synthetic: int,
    b2: dict[str, Any],
    b2_synthetic_available: int,
    vision_not_found: int,
    purge_safe: bool,
) -> str:
    if missing_non_synthetic == 0 and synthetic == 0:
        return "no_action"
    probed_hits = int(b2.get("probed_available_count", 0))
    if missing_non_synthetic > 0 and not b2.get("sampled") and probed_hits >= missing_non_synthetic:
        return "run_fill_l3_gaps"
    if missing_non_synthetic > 0 and not b2.get("sampled") and probed_hits > 0:
        return "run_fill_l3_gaps_partial_b2"
    if vision_not_found > 0 and (missing_non_synthetic > 0 or synthetic > 0):
        return "cae_b2_backfill_required_or_allow_degraded"
    if not purge_safe and synthetic > 0:
        return "do_not_replace_synthetic_until_b2_ready"
    if purge_safe and synthetic > 0 and b2_synthetic_available >= synthetic:
        return "run_fill_l3_gaps_replace_synthetic"
    return "run_fill_l3_gaps_then_allow_degraded_if_still_missing"
