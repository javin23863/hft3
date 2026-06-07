"""Unified crypto lane readiness report (single bookticker scan)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import yaml

from crypto_lane.src.ingest.bookticker_quality import (
    clear_bookticker_summary_cache,
    summarize_bookticker_range,
)
from crypto_lane.src.ingest.cae_backfill_status import cae_bookticker_backfill_status
from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps
from crypto_lane.src.ingest.mempool_preflight import AUDIT_B2_PROBE_MAX_DAYS, preflight_mempool_gaps
from crypto_lane.src.ingest.paths import normalized_dir
from crypto_lane.src.types import repo_root_from_lane

READINESS_CACHE_MAX_AGE_HOURS = 24


def crypto_date_range_from_config() -> tuple[str, str]:
    root = repo_root_from_lane()
    for name in (
        "h1_basis_compression_production.yaml",
        "h1_basis_compression.yaml",
    ):
        bt_cfg = root / "backtests/configs/crypto_hypotheses" / name
        if bt_cfg.is_file():
            cfg = yaml.safe_load(bt_cfg.read_text(encoding="utf-8"))
            dr = cfg.get("date_range") or {}
            return str(dr.get("start", "2024-01-01")), str(dr.get("end", "2024-12-31"))
    return "2024-01-01", "2024-12-31"


def build_crypto_readiness_report(
    *,
    start: str | None = None,
    end: str | None = None,
    vision_probe: bool = False,
    bookticker_summary: dict[str, Any] | None = None,
    clear_cache: bool = False,
) -> dict[str, Any]:
    """Build crypto readiness report with at most one bookticker parquet scan."""
    if clear_cache:
        clear_bookticker_summary_cache()
    if start is None or end is None:
        start, end = crypto_date_range_from_config()

    summary = bookticker_summary or summarize_bookticker_range(start=start, end=end)
    l3_pf = preflight_l3_gaps(
        start=start,
        end=end,
        vision_probe=vision_probe,
        bookticker_summary=summary,
    )
    mempool_pf = preflight_mempool_gaps(
        start=start,
        end=end,
        b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS,
    )
    cae = cae_bookticker_backfill_status(start=start, end=end, l3_preflight=l3_pf)

    norm = normalized_dir()
    l3_norm_missing = [
        name
        for name in ("spot_perp_ticks.csv", "deribit_surface.csv")
        if not (norm / name).is_file() or (norm / name).stat().st_size == 0
    ]
    norm_missing = [
        name
        for name in ("spot_perp_ticks.csv", "deribit_surface.csv", "mempool_snapshots.csv")
        if not (norm / name).is_file() or (norm / name).stat().st_size == 0
    ]

    absent_bt = summary["absent"]
    missing_bt = summary["missing"]
    synthetic_bt = summary["synthetic"]
    by_class = summary["by_class"]

    crypto_l3_ready = len(absent_bt) == 0 and len(synthetic_bt) == 0 and not l3_norm_missing
    crypto_mempool_ready = bool(mempool_pf.get("mempool_ready"))
    crypto_ready = crypto_l3_ready and crypto_mempool_ready

    return {
        "audited_at": datetime.now(UTC).isoformat(),
        "crypto_date_range": {"start": start, "end": end},
        "crypto_bookticker_by_class": by_class,
        "crypto_bookticker_absent_days": len(absent_bt),
        "crypto_bookticker_true_l3_gap_days": len(missing_bt),
        "crypto_bookticker_synthetic_days": len(synthetic_bt),
        "crypto_bookticker_synthetic_sample": synthetic_bt[:20],
        "crypto_normalized_missing": norm_missing,
        "crypto_mempool_missing_days": mempool_pf.get("crypto_mempool_missing_days"),
        "crypto_mempool_available_count": mempool_pf.get("crypto_mempool_available_count"),
        "crypto_btc_node_synced": mempool_pf.get("btc_node_synced"),
        "crypto_l3_ready": crypto_l3_ready,
        "crypto_mempool_ready": crypto_mempool_ready,
        "crypto_mempool_recommendation": mempool_pf.get("recommendation"),
        "crypto_ready": crypto_ready,
        "synthetic_days": len(synthetic_bt),
        "purge_safe": bool(l3_pf.get("purge_safe")),
        "purge_safe_estimate": bool(l3_pf.get("purge_safe_estimate")),
        "purge_block_reason": l3_pf.get("purge_block_reason"),
        "l3_recommendation": l3_pf.get("recommendation"),
        "preflight_l3": l3_pf,
        "preflight_mempool": mempool_pf,
        "cae_bookticker_backfill_status": cae,
        "days_until_purge_safe": cae.get("days_until_purge_safe"),
    }


def readiness_cache_fresh(
    cached: dict[str, Any],
    *,
    live_synthetic_days: int,
    max_age_hours: float = READINESS_CACHE_MAX_AGE_HOURS,
) -> bool:
    """True when cached audit matches live synthetic count and is within max age."""
    if not cached.get("crypto_ready"):
        return False
    audited_at = cached.get("audited_at")
    if not audited_at:
        return False
    try:
        ts = datetime.fromisoformat(str(audited_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        age_h = (datetime.now(UTC) - ts).total_seconds() / 3600.0
        if age_h > max_age_hours:
            return False
    except (TypeError, ValueError):
        return False
    cached_syn = cached.get("synthetic_days", cached.get("crypto_bookticker_synthetic_days"))
    if cached_syn is None:
        return False
    return int(cached_syn) == int(live_synthetic_days) and int(live_synthetic_days) == 0
