"""System zone — infra + lane health.

Latency truth card, slow-tier LLM status, certification, Databento budget,
capture freshness, kill-switch config. All read-only; credential *values* are
never surfaced (only presence via keystore.status())."""
from __future__ import annotations

import os
from typing import Optional

from .. import paths, schemas

# Databento credit envelope (mirrors telemetry/src/dashboard.py constants).
DATABENTO_INITIAL_CREDIT = 125.00
DATABENTO_OPERATING_CAP = 112.50


def _latency() -> dict:
    data = paths.read_json(paths.LATENCY_SUMMARY)
    if not isinstance(data, dict):
        return {"status": schemas.MISSING}
    rc = data.get("report_card", {}) if isinstance(data.get("report_card"), dict) else {}
    verdict = rc.get("verdict", {}) if isinstance(rc.get("verdict"), dict) else {}
    lane = data.get("recommended_lane", {}) if isinstance(data.get("recommended_lane"), dict) else {}
    gates = data.get("gates", {}) if isinstance(data.get("gates"), dict) else {}
    overall = verdict.get("overall")
    status = schemas.OK if overall == "PASS" else (schemas.FAIL if overall == "FAIL" else schemas.UNKNOWN)
    return {
        "status": status,
        "run_id": data.get("run_id"),
        "timestamp_utc": data.get("timestamp_utc"),
        "order_ack_p99_ms": data.get("order_ack_p99_ms"),
        "order_ack_measured": data.get("order_ack_measured"),
        "recommended_lane": lane.get("lane"),
        "lane_name": lane.get("lane_name"),
        "dominant_bottleneck": data.get("dominant_bottleneck"),
        "gates": gates,
        "overall": overall,
        "guidance": data.get("strategy_guidance"),
        "artifact": str(paths.LATENCY_SUMMARY.relative_to(paths.REPO)),
    }


def _slow_tier() -> dict:
    probs = paths.read_json(paths.SLOW_TIER_PROBLEMS)
    evald = paths.read_json(paths.SLOW_TIER_EVAL)
    n_problems = 0
    problems: list = []
    generated = None
    if isinstance(probs, dict):
        n_problems = probs.get("n_problems", 0) or 0
        problems = probs.get("problems", []) or []
        generated = probs.get("generated_utc")
    out = {
        "status": schemas.FAIL if n_problems else schemas.OK,
        "n_problems": n_problems,
        "problems": problems,
        "generated_utc": generated,
        "problems_age": schemas.freshness(generated, stale_after_s=36 * 3600),
    }
    if isinstance(evald, dict):
        out["eval"] = {
            "agreement": evald.get("agreement"),
            "conflict_rate": evald.get("conflict_rate"),
            "gate_pass": evald.get("gate_pass"),
            "n_golden": evald.get("n_golden"),
            "model": evald.get("model"),
        }
    return out


def _certification() -> dict:
    data = paths.read_json(paths.CERT_REGISTRY)
    if not isinstance(data, dict):
        return {"status": schemas.MISSING}
    cert = data.get("latest_certification_status")
    status = schemas.OK if cert == "GREEN" else (schemas.FAIL if cert == "RED" else schemas.UNKNOWN)
    return {
        "status": status,
        "certification_status": cert,
        "run_id": data.get("latest_certification_run_id"),
        "timestamp_utc": data.get("latest_certification_timestamp"),
        "blocking_failures": data.get("blocking_failures", []),
        "warnings": data.get("warnings", []),
        "covered_modules": data.get("covered_modules", []),
        "covered_symbols": data.get("covered_symbols", []),
        "execution_modes": data.get("covered_execution_modes", []),
    }


def _databento() -> dict:
    receipt = paths.read_json(paths.DATABENTO_RECEIPT)
    total_used: Optional[float] = None
    rows: Optional[int] = None
    try:
        import pandas as pd  # heavy; only if available

        if paths.DATABENTO_MANIFEST.exists():
            df = pd.read_parquet(paths.DATABENTO_MANIFEST)
            rows = int(len(df))
            if "cost" in df.columns:
                total_used = float(df["cost"].sum())
    except Exception:  # pandas/pyarrow missing or unreadable parquet
        pass
    out = {
        "status": schemas.OK,
        "initial_credit": DATABENTO_INITIAL_CREDIT,
        "operating_cap": DATABENTO_OPERATING_CAP,
        "total_used": total_used,
        "remaining": (DATABENTO_INITIAL_CREDIT - total_used) if total_used is not None else None,
        "manifest_rows": rows,
    }
    if isinstance(receipt, dict):
        out["last_download"] = {
            "run_utc": receipt.get("run_utc"),
            "windows_processed": receipt.get("windows_processed"),
            "symbols_npz_written": receipt.get("symbols_npz_written"),
            "total_cost_usd": receipt.get("total_cost_usd"),
        }
    return out


def _capture() -> dict:
    data = paths.read_json(paths.CAPTURE_BASELINE)
    if not isinstance(data, dict):
        return {"status": schemas.MISSING}
    gaps = data.get("known_gaps") or []
    drift = data.get("drift_warnings") or []
    return {
        "status": schemas.FAIL if gaps else schemas.OK,
        "host": data.get("host_id"),
        "captured_at": data.get("captured_at"),
        "known_gaps": gaps,
        "drift_warnings": drift,
        "cpu_layout": data.get("cpu_layout"),
        "note": "live per-symbol capture manifest lives on CHI404 (not synced here)",
    }


def _execution() -> dict:
    """Execution-mode + risk-env presence. Values never surfaced — presence only."""
    mode = paths.execution_mode()
    kill = os.environ.get("LIVE_KILL_SWITCH", "").lower()
    return {
        "execution_mode": mode,
        "live": mode == "LIVE",
        "kill_switch": kill or "unset",
        "risk_env": {
            "LIVE_MAX_ORDER_SIZE": "set" if os.environ.get("LIVE_MAX_ORDER_SIZE") else "missing",
            "LIVE_DAILY_LOSS_LIMIT": "set" if os.environ.get("LIVE_DAILY_LOSS_LIMIT") else "missing",
            "LIVE_RISK_ENABLED": "set" if os.environ.get("LIVE_RISK_ENABLED") else "missing",
        },
    }


def build() -> dict:
    latency = _latency()
    slow = _slow_tier()
    cert = _certification()
    components = [latency, slow, cert]
    if any(c.get("status") == schemas.FAIL for c in components):
        health = schemas.RED
    elif any(c.get("status") in (schemas.STALE, schemas.MISSING, schemas.UNKNOWN) for c in components):
        health = schemas.AMBER
    else:
        health = schemas.GREEN
    return {
        "zone": "system",
        "generated_utc": paths.now_iso(),
        "health": health,
        "latency": latency,
        "slow_tier": slow,
        "certification": cert,
        "databento": _databento(),
        "capture": _capture(),
        "execution": _execution(),
    }
