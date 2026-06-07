"""Orchestrate crypto data gap-fill for full-year production testing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from crypto_lane.src.align.latency_profile import calibrate_ws_rtt, measure_node_profile_from_btc, save_node_profile
from crypto_lane.src.config.env_loader import ensure_crypto_env, redacted_env_report
from crypto_lane.src.ingest.bookticker_quality import clear_bookticker_summary_cache
from crypto_lane.src.ingest.cae_backfill_status import cae_bookticker_backfill_status
from crypto_lane.src.ingest.l3_gap_fill import fill_l3_gaps as _fill_l3_gaps
from crypto_lane.src.ingest.l3_preflight import preflight_l3_gaps
from crypto_lane.src.ingest.mempool_preflight import AUDIT_B2_PROBE_MAX_DAYS, preflight_mempool_gaps
from crypto_lane.src.ingest.mempool_pull import backfill_blockspace_from_node
from crypto_lane.src.ingest.node_remote_sync import sync_chi404_btc_node_artifacts
from crypto_lane.src.ingest.gold_pull import (
    pull_gold,
    supplement_dvol_from_deribit,
    supplement_perp_from_binance,
)
from crypto_lane.src.ingest.normalize import normalize_all
from crypto_lane.src.types import repo_root_from_lane


def _crypto_date_range() -> tuple[str, str]:
    bt_cfg = (
        repo_root_from_lane()
        / "backtests/configs/crypto_hypotheses/h1_basis_compression_production.yaml"
    )
    if not bt_cfg.is_file():
        bt_cfg = repo_root_from_lane() / "backtests/configs/crypto_hypotheses/h1_basis_compression.yaml"
    if bt_cfg.is_file():
        cfg = yaml.safe_load(bt_cfg.read_text(encoding="utf-8"))
        dr = cfg.get("date_range") or {}
        return str(dr.get("start", "2024-01-01")), str(dr.get("end", "2024-12-31"))
    return "2024-01-01", "2024-12-31"


def _crypto_audit_snapshot() -> dict[str, Any]:
    import sys

    repo = repo_root_from_lane()
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    from scripts.audit_crypto_readiness import crypto_readiness_report

    return crypto_readiness_report()


def run_fill_test_gaps(
    *,
    dry_run: bool = False,
    sync_chi404_node: bool = False,
    skip_chi404: bool = False,
    ws_rtt_ms: float | None = None,
    force_replace_synthetic: bool = False,
    allow_degraded: bool = False,
) -> dict[str, Any]:
    """Run gap-fill pipeline for production crypto testing."""
    clear_bookticker_summary_cache()
    ensure_crypto_env()
    start, end = _crypto_date_range()
    steps: dict[str, Any] = {
        "date_range": {"start": start, "end": end},
        "dry_run": dry_run,
        "errors": [],
    }

    steps["env_check"] = redacted_env_report()
    l3_pf = preflight_l3_gaps(start=start, end=end, vision_probe=not dry_run)
    mp_pf = preflight_mempool_gaps(
        start=start, end=end, b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS
    )
    steps["preflight_l3"] = l3_pf
    steps["preflight_mempool"] = mp_pf
    steps["cae_bookticker_backfill_status"] = cae_bookticker_backfill_status(
        start=start, end=end, l3_preflight=l3_pf
    )

    if dry_run:
        audit = _crypto_audit_snapshot()
        steps["crypto_audit"] = {
            k: v
            for k, v in audit.items()
            if k.startswith("crypto_")
            or k
            in (
                "purge_safe",
                "purge_block_reason",
                "days_until_purge_safe",
                "cae_bookticker_backfill_status",
                "l3_recommendation",
            )
        }
        steps["ready"] = bool(audit.get("crypto_ready"))
        return steps

    if sync_chi404_node and not skip_chi404:
        steps["chi404_node_sync"] = sync_chi404_btc_node_artifacts()
    elif skip_chi404:
        steps["chi404_node_sync"] = {"skipped": True}

    try:
        gold = pull_gold(start=start, end=end)
        gold["perp_binance_api"] = supplement_perp_from_binance(start=start, end=end)
        gold["dvol_deribit_api"] = supplement_dvol_from_deribit(start=start, end=end)
        steps["pull_gold"] = gold
    except Exception as exc:
        steps["errors"].append(f"pull_gold: {exc}")
        steps["pull_gold_error"] = str(exc)

    mp_pf = preflight_mempool_gaps(
        start=start, end=end, b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS
    )
    if not mp_pf.get("mempool_ready"):
        try:
            steps["pull_gold_mempool"] = pull_gold(start=start, end=end, sources=["mempool"])
            mp_pf = preflight_mempool_gaps(
                start=start, end=end, b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS
            )
        except Exception as exc:
            steps["errors"].append(f"pull_gold_mempool: {exc}")
            steps["pull_gold_mempool_error"] = str(exc)

    if not skip_chi404 and not sync_chi404_node:
        try:
            steps["chi404_node_sync"] = sync_chi404_btc_node_artifacts()
        except Exception as exc:
            steps["errors"].append(f"chi404_node_sync: {exc}")
            steps["chi404_node_sync_error"] = str(exc)

    mp_pf = preflight_mempool_gaps(
        start=start, end=end, b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS
    )
    if not mp_pf.get("mempool_ready") and mp_pf.get("btc_node_synced"):
        try:
            steps["blockspace_written"] = backfill_blockspace_from_node(
                start=start, end=end, step_hours=1
            )
            clear_bookticker_summary_cache()
            mp_pf = preflight_mempool_gaps(
                start=start, end=end, b2_probe_max_days=AUDIT_B2_PROBE_MAX_DAYS
            )
        except Exception as exc:
            steps["errors"].append(f"blockspace: {exc}")
            steps["blockspace_error"] = str(exc)
    elif not mp_pf.get("mempool_ready"):
        steps["blockspace_skipped"] = "mempool gaps remain; btc node not synced or status unknown"

    steps["mempool_preflight_after_pull"] = mp_pf
    clear_bookticker_summary_cache()
    l3_pf = preflight_l3_gaps(start=start, end=end)
    purge_safe = bool(l3_pf.get("purge_safe"))
    replace_synthetic = (purge_safe or force_replace_synthetic) and int(l3_pf.get("synthetic_days", 0)) > 0
    if force_replace_synthetic and not purge_safe:
        steps["replace_synthetic_forced"] = True
    elif int(l3_pf.get("synthetic_days", 0)) > 0 and not purge_safe:
        steps["replace_synthetic_skipped"] = l3_pf.get("purge_block_reason")

    try:
        fill_report = _fill_l3_gaps(
            start=start,
            end=end,
            replace_synthetic=replace_synthetic,
            allow_degraded=allow_degraded,
            force=force_replace_synthetic,
        )
        steps["fill_l3_gaps"] = fill_report
        if fill_report.get("aborted"):
            steps["errors"].append(f"fill_l3_gaps aborted: {fill_report.get('purge_block_reason')}")
    except Exception as exc:
        steps["errors"].append(f"fill_l3_gaps: {exc}")
        steps["fill_l3_gaps_error"] = str(exc)

    try:
        paths = normalize_all(start=start, end=end)
        steps["normalize"] = {k: str(v) for k, v in paths.items()}
    except Exception as exc:
        steps["errors"].append(f"normalize: {exc}")
        steps["normalize_error"] = str(exc)

    if ws_rtt_ms is not None:
        profile = calibrate_ws_rtt("binance_perp", ws_rtt_ms=ws_rtt_ms, live_measured=True)
        steps["calibrate_ws_rtt"] = profile.__dict__
        try:
            node = measure_node_profile_from_btc()
            save_node_profile(node)
            steps["measure_node"] = node.__dict__
        except OSError as exc:
            steps["measure_node_error"] = str(exc)
    else:
        steps["calibrate_ws_rtt_skipped"] = (
            "pass --ws-rtt-ms for pit_strict production smokes (H4–H7)"
        )

    clear_bookticker_summary_cache()
    steps["crypto_audit"] = _crypto_audit_snapshot()
    steps["ready"] = bool(steps["crypto_audit"].get("crypto_ready")) and not steps["errors"]
    return steps


def write_fill_report(report: dict[str, Any], path: Path | None = None) -> Path:
    out = path or (repo_root_from_lane() / "runtime/data_audits/fill_test_gaps_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out
