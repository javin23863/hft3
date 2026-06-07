"""Percentile helpers for broker order latency reports."""

from __future__ import annotations

import statistics
from typing import Any


def percentile_us(values_us: list[float], pct: float) -> float | None:
    if not values_us:
        return None
    s = sorted(values_us)
    idx = max(0, min(len(s) - 1, int(len(s) * pct) - 1 if pct < 1.0 else len(s) - 1))
    if pct >= 0.999:
        idx = max(0, int(len(s) * 0.999) - 1)
    elif pct >= 0.99:
        idx = max(0, int(len(s) * 0.99) - 1)
    elif pct >= 0.90:
        idx = max(0, int(len(s) * 0.90) - 1)
    elif pct >= 0.50:
        idx = max(0, int(len(s) * 0.50) - 1)
    return float(s[idx])


def stats_us(values_us: list[float]) -> dict[str, float | int | None]:
    if not values_us:
        return {
            "count": 0,
            "min_us": None,
            "avg_us": None,
            "p50_us": None,
            "p90_us": None,
            "p99_us": None,
            "p999_us": None,
            "max_us": None,
        }
    s = sorted(values_us)
    return {
        "count": len(s),
        "min_us": min(s),
        "avg_us": statistics.mean(s),
        "p50_us": percentile_us(s, 0.50),
        "p90_us": percentile_us(s, 0.90),
        "p99_us": percentile_us(s, 0.99),
        "p999_us": percentile_us(s, 0.999),
        "max_us": max(s),
    }


def stats_by_key(
    records: list[dict[str, Any]],
    key: str,
    value_extractor,
) -> dict[str, dict[str, float | int | None]]:
    buckets: dict[str, list[float]] = {}
    for rec in records:
        k = str(rec.get(key) or "unknown")
        val = value_extractor(rec)
        if val is None:
            continue
        buckets.setdefault(k, []).append(float(val))
    return {k: stats_us(v) for k, v in sorted(buckets.items())}
