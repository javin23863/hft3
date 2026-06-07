"""Orchestrate crypto data gap-fill for full-year production testing."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from crypto_lane.src.align.latency_profile import calibrate_ws_rtt, measure_node_profile_from_btc, save_node_profile
from crypto_lane.src.config.env_loader import ensure_crypto_env, redacted_env_report
from crypto_lane.src.config_loader import load_yaml
from crypto_lane.src.ingest.bookticker_quality import (
    clear_bookticker_summary_cache,
    summarize_bookticker_range,
)
from crypto_lane.src.ingest.crypto_readiness import (
    build_crypto_readiness_report,
    crypto_date_range_from_config,
    crypto_readiness_dry_run_cache_path,
    write_crypto_readiness_cache,
)
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

_PIT_STRICT_CONFIGS = (
    "h4_mempool_volatility_production.yaml",
    "h5_blockspace_liquidity_stress_production.yaml",
    "h6_mempool_clear_reversion_production.yaml",
    "h7_congestion_event_study_production.yaml",
)


def _pit_strict_hypotheses() -> list[str]:
    root = repo_root_from_lane() / "backtests/configs/crypto_hypotheses"
    out: list[str] = []
    for name in _PIT_STRICT_CONFIGS:
        path = root / name
        if path.is_file():
            cfg = load_yaml(path)
            out.append(str(cfg.get("hypothesis_id", name)))
    return out


def _pit_strict_blocked(ws_rtt_ms: float | None) -> bool:
    return ws_rtt_ms is None


def _crypto_audit_subset(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in audit.items()
        if k.startswith("crypto_")
        or k
        in (
            "audited_at",
            "synthetic_days",
            "purge_safe",
            "purge_safe_estimate",
            "purge_block_reason",
            "days_until_purge_safe",
            "l3_recommendation",
        )
    }


def run_fill_test_gaps(
    *,
    dry_run: bool = False,
    sync_chi404_node: bool = False,
    skip_chi404: bool = False,
    ws_rtt_ms: float | None = None,
    force_replace_synthetic: bool = False,
    allow_degraded: bool = False,
    continue_on_error: bool = False,
    refresh_b2_synthetic_probe: bool = False,
) -> dict[str, Any]:
    """Run gap-fill pipeline for production crypto testing."""
    if not dry_run:
        clear_bookticker_summary_cache()
    ensure_crypto_env()
    start, end = crypto_date_range_from_config()
    steps: dict[str, Any] = {
        "date_range": {"start": start, "end": end},
        "dry_run": dry_run,
        "errors": [],
    }
    pit_blocked = _pit_strict_blocked(ws_rtt_ms)

    steps["env_check"] = redacted_env_report()

    if dry_run:
        gate_path = repo_root_from_lane() / "runtime/data_audits/crypto_readiness.json"
        gate_mtime_before = gate_path.stat().st_mtime if gate_path.is_file() else None
        audit = build_crypto_readiness_report(
            start=start,
            end=end,
            vision_probe=False,
            clear_cache=False,
            full_synthetic_b2_probe=True,
            use_b2_synthetic_cache=not refresh_b2_synthetic_probe,
            refresh_b2_synthetic_probe=refresh_b2_synthetic_probe,
        )
        steps["preflight_l3"] = audit["preflight_l3"]
        steps["preflight_mempool"] = audit["preflight_mempool"]
        steps["cae_bookticker_backfill_status"] = audit["cae_bookticker_backfill_status"]
        steps["crypto_audit"] = _crypto_audit_subset(audit)
        dry_out = crypto_readiness_dry_run_cache_path()
        dry_out.parent.mkdir(parents=True, exist_ok=True)
        dry_out.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")
        steps["readiness_dry_run_path"] = str(dry_out)
        steps["readiness_gate_path"] = str(gate_path)
        gate_mtime_after = gate_path.stat().st_mtime if gate_path.is_file() else None
        steps["readiness_gate_unchanged"] = gate_mtime_before == gate_mtime_after
        if audit.get("b2_probe_note"):
            steps["b2_probe_note"] = audit["b2_probe_note"]
        steps["ready"] = bool(audit.get("crypto_ready")) and not pit_blocked
        if pit_blocked:
            steps["pit_strict_blocked"] = True
            steps["pit_strict_hypotheses"] = _pit_strict_hypotheses()
        return steps

    def _abort(msg: str) -> bool:
        steps["errors"].append(msg)
        return not continue_on_error

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
        steps["pull_gold_error"] = str(exc)
        if _abort(f"pull_gold: {exc}"):
            steps["ready"] = False
            return steps

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
            steps["pull_gold_mempool_error"] = str(exc)
            if _abort(f"pull_gold_mempool: {exc}"):
                steps["ready"] = False
                return steps

    if not skip_chi404 and not sync_chi404_node:
        try:
            steps["chi404_node_sync"] = sync_chi404_btc_node_artifacts()
        except Exception as exc:
            steps["chi404_node_sync_error"] = str(exc)
            if _abort(f"chi404_node_sync: {exc}"):
                steps["ready"] = False
                return steps

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
            steps["blockspace_error"] = str(exc)
            if _abort(f"blockspace: {exc}"):
                steps["ready"] = False
                return steps
    elif not mp_pf.get("mempool_ready"):
        steps["blockspace_skipped"] = "mempool gaps remain; btc node not synced or status unknown"

    steps["mempool_preflight_after_pull"] = mp_pf
    if not mp_pf.get("mempool_ready"):
        steps["mempool_not_ready"] = True
        if _abort("mempool not ready after pull/sync"):
            steps["ready"] = False
            return steps

    summary = summarize_bookticker_range(start=start, end=end)
    l3_pf = preflight_l3_gaps(
        start=start,
        end=end,
        vision_probe=False,
        bookticker_summary=summary,
        full_synthetic_b2_probe=True,
        use_b2_synthetic_cache=True,
    )
    steps["preflight_l3"] = l3_pf
    purge_safe = bool(l3_pf.get("purge_safe"))
    replace_synthetic = (purge_safe or force_replace_synthetic) and int(l3_pf.get("synthetic_days", 0)) > 0
    if force_replace_synthetic and not purge_safe:
        steps["replace_synthetic_forced"] = True
    elif int(l3_pf.get("synthetic_days", 0)) > 0 and not purge_safe:
        steps["replace_synthetic_skipped"] = l3_pf.get("purge_block_reason")

    fill_aborted = False
    try:
        fill_report = _fill_l3_gaps(
            start=start,
            end=end,
            replace_synthetic=replace_synthetic,
            allow_degraded=allow_degraded,
            force=force_replace_synthetic,
            preflight=l3_pf,
            bookticker_summary=summary,
        )
        steps["fill_l3_gaps"] = fill_report
        if fill_report.get("aborted"):
            fill_aborted = True
            steps["errors"].append(
                f"fill_l3_gaps aborted: {fill_report.get('abort_reason')}"
            )
            steps["normalize_skipped"] = "fill_l3_gaps aborted"
            steps["ready"] = False
            if not continue_on_error:
                return steps
    except Exception as exc:
        steps["fill_l3_gaps_error"] = str(exc)
        if _abort(f"fill_l3_gaps: {exc}"):
            steps["ready"] = False
            return steps

    if not fill_aborted:
        try:
            paths = normalize_all(start=start, end=end)
            steps["normalize"] = {k: str(v) for k, v in paths.items()}
        except Exception as exc:
            steps["normalize_error"] = str(exc)
            if _abort(f"normalize: {exc}"):
                steps["ready"] = False
                return steps

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
        steps["pit_strict_blocked"] = True
        steps["pit_strict_hypotheses"] = _pit_strict_hypotheses()
        steps["calibrate_ws_rtt_skipped"] = (
            "pass --ws-rtt-ms for pit_strict production smokes (H4–H7)"
        )

    from crypto_lane.src.ingest.bookticker_quality import invalidate_bookticker_caches

    invalidate_bookticker_caches()
    audit = build_crypto_readiness_report(
        start=start,
        end=end,
        vision_probe=False,
        clear_cache=False,
        full_synthetic_b2_probe=True,
        use_b2_synthetic_cache=False,
        refresh_b2_synthetic_probe=True,
    )
    steps["crypto_audit"] = _crypto_audit_subset(audit)
    write_crypto_readiness_cache(audit)
    steps["ready"] = bool(audit.get("crypto_ready")) and not steps["errors"] and not pit_blocked
    return steps


def write_fill_report(report: dict[str, Any], path: Path | None = None) -> Path:
    out = path or (repo_root_from_lane() / "runtime/data_audits/fill_test_gaps_report.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return out
