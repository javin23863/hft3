"""Preflight B2/local mempool gold coverage and CAE btc-node sync status."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from crypto_lane.src.config.env_loader import ensure_crypto_env
from crypto_lane.src.ingest.b2_client import B2Client, B2ClientError
from crypto_lane.src.ingest.gold_pull import _date_range, _parse_date, _symbol_map
from crypto_lane.src.ingest.gold_reader import _local_cache_path, gold_key, resolve_gold_bucket
from crypto_lane.src.ingest.node_remote_sync import local_mempool_jsonl_days
from crypto_lane.src.ingest.paths import normalized_dir
from crypto_lane.src.types import repo_root_from_lane

MEMPOOL_MIN_COVERAGE_RATIO = 0.95
AUDIT_B2_PROBE_MAX_DAYS = 31


def _sample_probe_days(days: list[date], max_probe_days: int) -> list[date]:
    if max_probe_days <= 0 or len(days) <= max_probe_days:
        return days
    n = len(days)
    idxs = sorted({int(round(i * (n - 1) / (max_probe_days - 1))) for i in range(max_probe_days)})
    return [days[i] for i in idxs]


def _desk():
    root = repo_root_from_lane()
    import sys

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import desk_env

    return root, desk_env


def _read_btc_node_status() -> dict[str, Any] | None:
    root, desk_env = _desk()
    return desk_env.read_btc_node_status(root)


def _normalized_mempool_covers_range(start: str, end: str) -> bool:
    path = normalized_dir() / "mempool_snapshots.csv"
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        import polars as pl

        df = pl.read_csv(path)
        if df.is_empty() or "node_observation_time" not in df.columns:
            return False
        start_ms = int(
            datetime.combine(_parse_date(start), datetime.min.time(), tzinfo=timezone.utc).timestamp()
            * 1000
        )
        end_ms = int(
            datetime.combine(_parse_date(end), datetime.max.time(), tzinfo=timezone.utc).timestamp()
            * 1000
        )
        in_range = df.filter(
            (pl.col("node_observation_time") >= start_ms) & (pl.col("node_observation_time") <= end_ms)
        )
        return in_range.height > 0
    except Exception:
        return False


def _merged_mempool_available_days(
    days: list[date],
    *,
    b2_available: set[str],
    local_jsonl_days: set[str],
) -> tuple[list[str], list[str]]:
    available: list[str] = []
    missing: list[str] = []
    for day in days:
        iso = day.isoformat()
        if iso in b2_available or iso in local_jsonl_days:
            available.append(iso)
        else:
            missing.append(iso)
    return available, missing


def _mempool_probe(
    days: list[date],
    *,
    start: str,
    end: str,
    max_error_samples: int = 5,
) -> dict[str, Any]:
    ensure_crypto_env()
    sym = _symbol_map()
    btc_sym = sym.get("bitcoind", "BTC")
    client = B2Client()
    bucket = resolve_gold_bucket("bitcoind")
    b2_available: set[str] = set()
    error_samples: list[dict[str, str]] = []
    local_jsonl_days = set(local_mempool_jsonl_days(start=start, end=end))
    for day in days:
        iso = day.isoformat()
        if iso in local_jsonl_days:
            b2_available.add(iso)
            continue
        key = gold_key("bitcoind", btc_sym, day, "mempool_snapshot_15m")
        local_parquet = _local_cache_path(key)
        if local_parquet.is_file():
            b2_available.add(iso)
            continue
        try:
            if client.file_exists(bucket, key):
                b2_available.add(iso)
        except B2ClientError as exc:
            if len(error_samples) < max_error_samples:
                error_samples.append({"day": iso, "error": str(exc)})
    available, missing = _merged_mempool_available_days(
        days,
        b2_available=b2_available,
        local_jsonl_days=local_jsonl_days,
    )
    return {
        "bucket": bucket,
        "local_jsonl_days": sorted(local_jsonl_days),
        "local_jsonl_day_count": len(local_jsonl_days),
        "available_days": available,
        "missing_days": missing,
        "available_count": len(available),
        "missing_count": len(missing),
        "error_samples": error_samples,
    }


def preflight_mempool_gaps(
    *,
    start: str,
    end: str,
    allow_degraded_mempool: bool = False,
    b2_probe_max_days: int | None = None,
) -> dict[str, Any]:
    """Report mempool_snapshot_15m coverage on B2/local cache and node sync."""
    start_d = _parse_date(start)
    end_d = _parse_date(end)
    days = list(_date_range(start_d, end_d))
    total_days = len(days)
    probe_days = _sample_probe_days(days, b2_probe_max_days) if b2_probe_max_days else days
    probe = _mempool_probe(probe_days, start=start, end=end)
    local_all = set(local_mempool_jsonl_days(start=start, end=end))
    sampled = len(probe_days) < total_days
    if sampled:
        non_local_probe = [d for d in probe_days if d.isoformat() not in local_all]
        probe_hits = set(probe["available_days"])
        non_local_hits = sum(1 for d in non_local_probe if d.isoformat() in probe_hits)
        b2_est_ratio = non_local_hits / max(len(non_local_probe), 1) if non_local_probe else 1.0
        non_local_total = sum(1 for d in days if d.isoformat() not in local_all)
        available_count = len(local_all) + int(round(b2_est_ratio * non_local_total))
        missing_count = max(0, total_days - available_count)
        coverage_ratio = available_count / max(total_days, 1)
    else:
        available_set = set(probe["available_days"]) | local_all
        available_count = len(available_set)
        missing_count = max(0, total_days - available_count)
        coverage_ratio = available_count / max(total_days, 1)
    node_status = _read_btc_node_status()
    if node_status is None:
        synced = None
        status_stale = None
    else:
        status_stale = bool(node_status.get("stale"))
        synced = bool(node_status.get("synced")) and not status_stale
    normalized_ok = _normalized_mempool_covers_range(start, end)
    if allow_degraded_mempool:
        mempool_ready = normalized_ok or coverage_ratio >= 0.5
    else:
        mempool_ready = missing_count == 0 or coverage_ratio >= MEMPOOL_MIN_COVERAGE_RATIO
    root, desk_env = _desk()

    return {
        "date_range": {"start": start, "end": end},
        "total_days": total_days,
        "b2_probe": probe,
        "b2_probe_sampled": sampled,
        "b2_probe_days": len(probe_days),
        "crypto_mempool_missing_days": missing_count,
        "crypto_mempool_available_count": available_count,
        "missing_days_sample": probe["missing_days"][:20],
        "btc_node_status_path": str(desk_env.resolve_btc_node_status_path(root) or ""),
        "btc_node_env_path": str(desk_env.resolve_btc_node_env_path(root) or ""),
        "btc_node_synced": synced,
        "btc_node_status_stale": status_stale,
        "local_mempool_jsonl_days": sorted(local_all),
        "local_mempool_jsonl_day_count": len(local_all),
        "normalized_mempool_covers_range": normalized_ok,
        "mempool_coverage_ratio": coverage_ratio,
        "mempool_ready": mempool_ready,
        "recommendation": (
            "ready"
            if mempool_ready
            else "run pull-gold --sources mempool then normalize; or ensure CAE btc_mempool_snapshot_ingest → B2"
        ),
    }
