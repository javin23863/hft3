"""Orchestrate true L3 bookticker gap fill: B2 → Binance Vision → degraded fallback."""
from __future__ import annotations

from typing import Any

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.binance_vision_pull import pull_bookticker_from_vision
from crypto_lane.src.ingest.bookticker_quality import (
    purge_synthetic_bookticker,
    summarize_bookticker_range,
    write_quality_manifest_from_summary,
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
    preflight: dict[str, Any] | None = None,
    bookticker_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ensure_crypto_env()
    ensure_data_dirs()

    summary = bookticker_summary or summarize_bookticker_range(start=start, end=end)
    preflight = preflight or preflight_l3_gaps(
        start=start,
        end=end,
        bookticker_summary=summary,
        vision_probe=False,
    )
    report: dict[str, Any] = {
        "preflight": preflight,
        "missing_before": len(summary["missing"]),
        "absent_before": len(summary["absent"]),
        "purged_synthetic_days": [],
        "steps": {},
    }

    def _finalize(final_summary: dict[str, Any]) -> dict[str, Any]:
        report["absent_after"] = len(final_summary["absent"])
        report["missing_after"] = len(final_summary["missing"])
        report["synthetic_after"] = len(final_summary["synthetic"])
        report["quality_manifest"] = str(write_quality_manifest_from_summary(final_summary))
        return report

    if replace_synthetic and not force and not preflight["purge_safe"]:
        report["aborted"] = True
        report["abort_reason"] = preflight["purge_block_reason"]
        return _finalize(summary)

    if replace_synthetic:
        report["purged_synthetic_days"] = purge_synthetic_bookticker(start=start, end=end)
        summary = summarize_bookticker_range(start=start, end=end, use_cache=False)

    report["steps"]["b2"] = pull_bookticker_from_b2(start=start, end=end, max_days=max_days)
    summary = summarize_bookticker_range(start=start, end=end, use_cache=False)
    still_missing = list(summary["absent"])
    if still_missing:
        v_start = still_missing[0].isoformat()
        v_end = still_missing[-1].isoformat()
        report["steps"]["binance_vision"] = pull_bookticker_from_vision(
            start=v_start,
            end=v_end,
            sleep_s=sleep_s,
            max_days=max_days,
        )
        summary = summarize_bookticker_range(start=start, end=end, use_cache=False)
        still_missing = list(summary["absent"])

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
        summary = summarize_bookticker_range(start=start, end=end, use_cache=False)

    return _finalize(summary)
