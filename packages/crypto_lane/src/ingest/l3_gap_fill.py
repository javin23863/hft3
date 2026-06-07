"""Orchestrate true L3 bookticker gap fill: B2 → Binance Vision → degraded fallback."""
from __future__ import annotations

from typing import Any

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.binance_vision_pull import pull_bookticker_from_vision
from crypto_lane.src.ingest.bookticker_quality import (
    absent_bookticker_days,
    missing_bookticker_days,
    purge_synthetic_bookticker,
    summarize_bookticker_range,
    synthetic_bookticker_days,
    write_quality_manifest,
)
from crypto_lane.src.ingest.coinstats_pull import fill_bookticker_gaps_degraded
from crypto_lane.src.ingest.gold_pull import pull_bookticker_from_b2
from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps
from crypto_lane.src.ingest.paths import ensure_data_dirs


def audit_l3_gaps(*, start: str, end: str) -> dict[str, Any]:
    ensure_data_dirs()
    summary = summarize_bookticker_range(start=start, end=end)
    absent = summary["absent"]
    missing = summary["missing"]
    synthetic = summary["synthetic"]
    by_class = dict(summary["by_class"])
    return {
        "granularity": "futures_um_bookticker_tick",
        "symbol": "BTCUSDT",
        "absent_days": len(absent),
        "missing_days": len(missing),
        "true_l3_gap_days": len(missing),
        "synthetic_days": len(synthetic),
        "by_class": by_class,
        "dates_absent": [d.isoformat() for d in absent[:50]],
        "dates_missing": [d.isoformat() for d in missing[:50]],
        "dates_synthetic": synthetic[:50],
        "truncated": len(absent) > 50 or len(missing) > 50 or len(synthetic) > 50,
    }


def fill_l3_gaps(
    *,
    start: str,
    end: str,
    replace_synthetic: bool = False,
    allow_degraded: bool = False,
    force: bool = False,
    sleep_s: float = 0.2,
    max_days: int | None = None,
) -> dict[str, Any]:
    ensure_crypto_env()
    ensure_data_dirs()

    preflight = preflight_l3_gaps(start=start, end=end)
    report: dict[str, Any] = {
        "preflight": preflight,
        "missing_before": preflight["missing_days"],
        "absent_before": len(absent_bookticker_days(start=start, end=end)),
        "purged_synthetic_days": [],
        "steps": {},
    }

    if replace_synthetic and not force and not preflight["purge_safe"]:
        report["aborted"] = True
        report["abort_reason"] = preflight["purge_block_reason"]
        report["absent_after"] = len(absent_bookticker_days(start=start, end=end))
        report["missing_after"] = len(missing_bookticker_days(start=start, end=end))
        report["synthetic_after"] = len(synthetic_bookticker_days(start=start, end=end))
        report["quality_manifest"] = str(write_quality_manifest(start=start, end=end))
        return report

    if replace_synthetic:
        report["purged_synthetic_days"] = purge_synthetic_bookticker(start=start, end=end)

    report["steps"]["b2"] = pull_bookticker_from_b2(start=start, end=end, max_days=max_days)

    still_missing = absent_bookticker_days(start=start, end=end)
    if still_missing:
        v_start = still_missing[0].isoformat()
        v_end = still_missing[-1].isoformat()
        report["steps"]["binance_vision"] = pull_bookticker_from_vision(
            start=v_start,
            end=v_end,
            sleep_s=sleep_s,
            max_days=max_days,
        )

    still_missing = absent_bookticker_days(start=start, end=end)
    if still_missing and allow_degraded:
        d_start = still_missing[0].isoformat()
        d_end = still_missing[-1].isoformat()
        report["steps"]["degraded"] = fill_bookticker_gaps_degraded(
            start=d_start,
            end=d_end,
            sleep_s=sleep_s,
            max_days=max_days,
            prefer_klines=True,
        )

    report["absent_after"] = len(absent_bookticker_days(start=start, end=end))
    report["missing_after"] = len(missing_bookticker_days(start=start, end=end))
    report["synthetic_after"] = len(synthetic_bookticker_days(start=start, end=end))
    report["quality_manifest"] = str(write_quality_manifest(start=start, end=end))
    return report
