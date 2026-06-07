"""Unified crypto lane readiness report (single bookticker scan)."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
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
READINESS_CACHE_PATH = "runtime/data_audits/crypto_readiness.json"
READINESS_DRY_RUN_CACHE_PATH = "runtime/data_audits/crypto_readiness_dry_run.json"


def crypto_readiness_cache_path() -> Path:
    return repo_root_from_lane() / READINESS_CACHE_PATH


def crypto_readiness_dry_run_cache_path() -> Path:
    return repo_root_from_lane() / READINESS_DRY_RUN_CACHE_PATH


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


def normalized_csv_ready() -> tuple[bool, list[str]]:
    norm = normalized_dir()
    missing = [
        name
        for name in ("spot_perp_ticks.csv", "deribit_surface.csv", "mempool_snapshots.csv")
        if not (norm / name).is_file() or (norm / name).stat().st_size == 0
    ]
    return not missing, missing


def _cache_age_hours(audited_at: str) -> float | None:
    try:
        ts = datetime.fromisoformat(str(audited_at).replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return (datetime.now(UTC) - ts).total_seconds() / 3600.0
    except (TypeError, ValueError):
        return None


def build_crypto_readiness_report(
    *,
    start: str | None = None,
    end: str | None = None,
    vision_probe: bool = False,
    bookticker_summary: dict[str, Any] | None = None,
    clear_cache: bool = False,
    full_synthetic_b2_probe: bool = True,
    use_b2_synthetic_cache: bool = True,
    refresh_b2_synthetic_probe: bool = False,
) -> dict[str, Any]:
    """Build crypto readiness report with at most one bookticker parquet scan."""
    if refresh_b2_synthetic_probe:
        from crypto_lane.src.ingest.b2_synthetic_probe_cache import clear_b2_synthetic_probe_cache

        clear_b2_synthetic_probe_cache()
        use_b2_synthetic_cache = False
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
        full_synthetic_b2_probe=full_synthetic_b2_probe,
        use_b2_synthetic_cache=use_b2_synthetic_cache,
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
    norm_ok, norm_missing = normalized_csv_ready()

    absent_bt = summary["absent"]
    missing_bt = summary["missing"]
    synthetic_bt = summary["synthetic"]
    by_class = summary["by_class"]

    crypto_l3_ready = len(absent_bt) == 0 and len(synthetic_bt) == 0 and not l3_norm_missing
    crypto_mempool_ready = bool(mempool_pf.get("mempool_ready"))
    crypto_ready = crypto_l3_ready and crypto_mempool_ready and norm_ok
    b2_from_cache = bool((l3_pf.get("b2_synthetic") or {}).get("from_cache"))
    b2_probe_note: str | None = None
    if b2_from_cache and not l3_pf.get("purge_safe"):
        b2_probe_note = (
            "purge_safe used cached B2 synthetic probe; run audit_crypto_readiness.py "
            "or fill-test-gaps --refresh-b2-probe after CAE Contabo backfill to B2"
        )

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
        "b2_synthetic_from_cache": b2_from_cache,
        "b2_probe_note": b2_probe_note,
    }


def write_crypto_readiness_cache(report: dict[str, Any], path: Path | None = None) -> Path:
    out = path or crypto_readiness_cache_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return out


def readiness_cache_fresh(
    cached: dict[str, Any],
    *,
    live_synthetic_days: int,
    live_mempool_ready: bool | None = None,
    live_norm_ok: bool | None = None,
    expected_date_range: dict[str, str] | None = None,
    max_age_hours: float = READINESS_CACHE_MAX_AGE_HOURS,
) -> bool:
    """True when cached audit matches live state and is within max age."""
    if not cached.get("crypto_ready"):
        return False
    audited_at = cached.get("audited_at")
    if not audited_at:
        return False
    age_h = _cache_age_hours(str(audited_at))
    if age_h is None or age_h > max_age_hours:
        return False
    if expected_date_range:
        cached_dr = cached.get("crypto_date_range") or {}
        if str(cached_dr.get("start")) != str(expected_date_range.get("start")):
            return False
        if str(cached_dr.get("end")) != str(expected_date_range.get("end")):
            return False
    cached_syn = cached.get("synthetic_days", cached.get("crypto_bookticker_synthetic_days"))
    if cached_syn is None:
        return False
    if int(cached_syn) != int(live_synthetic_days) or int(live_synthetic_days) != 0:
        return False
    if live_mempool_ready is not None and not live_mempool_ready:
        return False
    if cached.get("crypto_mempool_ready") is False and live_mempool_ready is None:
        return False
    if live_norm_ok is not None and not live_norm_ok:
        return False
    if cached.get("crypto_normalized_missing") and live_norm_ok is None:
        return False
    return True


def cache_audited_within_max_age(
    cached: dict[str, Any],
    *,
    max_age_hours: float = READINESS_CACHE_MAX_AGE_HOURS,
) -> bool:
    audited_at = cached.get("audited_at")
    if not audited_at:
        return False
    age_h = _cache_age_hours(str(audited_at))
    return age_h is not None and age_h <= max_age_hours
