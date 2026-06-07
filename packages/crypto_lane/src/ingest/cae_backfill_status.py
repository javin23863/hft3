"""Read-only B2 bookticker backfill status for synthetic L3 purge readiness."""
from __future__ import annotations

from typing import Any

from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps


def _month_labels(days: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for iso in days:
        label = iso[:7]
        counts[label] = counts.get(label, 0) + 1
    return counts


def cae_bookticker_backfill_status(
    *,
    start: str,
    end: str,
    l3_preflight: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Report B2 coverage for synthetic bookticker days and purge readiness.

    Uses l3_preflight B2 synthetic probe only (no crypto-alpha-engine imports).
    """
    pf = l3_preflight or preflight_l3_gaps(start=start, end=end, vision_probe=False)
    synthetic = list(pf.get("synthetic_day_list") or [])
    b2_syn = pf.get("b2_synthetic") or {}
    b2_on_synthetic = int(b2_syn.get("available_count", 0))
    synth_n = len(synthetic)
    purge_safe = bool(pf.get("purge_safe"))
    days_until_purge_safe = max(0, synth_n - b2_on_synthetic)
    return {
        "date_range": {"start": start, "end": end},
        "synthetic_days": synth_n,
        "synthetic_by_month": _month_labels(synthetic),
        "b2_synthetic_probe": b2_syn,
        "b2_available_for_synthetic": b2_on_synthetic,
        "b2_available_for_missing": int((pf.get("b2") or {}).get("available_count", 0)),
        "purge_safe": purge_safe,
        "purge_block_reason": pf.get("purge_block_reason"),
        "days_until_purge_safe": days_until_purge_safe,
        "recommendation": (
            "ready_for_replace_synthetic"
            if purge_safe and synthetic
            else (
                "no_synthetic_days"
                if not synthetic
                else "cae_contabo_bookticker_backfill_required"
            )
        ),
    }
