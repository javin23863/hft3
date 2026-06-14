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

MANDATORY_OPTIONS_CHECKS = (
    "options-datasets",
    "options-fixing-mbo",
    "options-fixing-coverage",
    "options-ohlcv",
    "options-definitions",
    "options-statistics",
)

ADVISORY_OPTIONS_CHECKS = frozenset({
    "options-fixing-mbo-coverage",
})

_PROBLEM_CHECK_STATUSES = {"FAIL", "WARN", "WARNING", "MISSING", "STALE", "UNKNOWN"}


def _latency() -> dict:
    data = paths.read_json(paths.LATENCY_SUMMARY)
    if not isinstance(data, dict):
        return {"status": schemas.MISSING}
    rc = data.get("report_card", {}) if isinstance(data.get("report_card"), dict) else {}
    verdict = rc.get("verdict", {}) if isinstance(rc.get("verdict"), dict) else {}
    lane = data.get("recommended_lane", {}) if isinstance(data.get("recommended_lane"), dict) else {}
    gates = data.get("gates", {}) if isinstance(data.get("gates"), dict) else {}
    overall = verdict.get("overall")
    live_status = schemas.OK if overall == "PASS" else (
        schemas.FAIL if overall == "FAIL" else schemas.UNKNOWN)
    # The System zone is a replay/research cockpit. A CHI404 network-path fail is
    # a live-arm blocker, but it must not paint the research runtime red when the
    # measured paper order-ack band is present and passing.
    if gates.get("order_ack_pass") is True and data.get("order_ack_measured") is True:
        status = schemas.OK
    else:
        status = live_status
    return {
        "status": status,
        "live_arm_status": live_status,
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
        # Conflict-review items are advisory for the autonomous research sweep.
        # Keep the problem feed visible, but do not turn the research cockpit red.
        "status": schemas.OK,
        "review_status": schemas.FAIL if n_problems else schemas.OK,
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
    status = schemas.MISSING
    note = "Databento manifest missing; budget cannot be read"
    try:
        if paths.DATABENTO_MANIFEST.exists():
            import pandas as pd  # heavy; only if available

            df = pd.read_parquet(paths.DATABENTO_MANIFEST)
            rows = int(len(df))
            if "cost" in df.columns:
                total_used = float(df["cost"].sum())
                status = schemas.OK
                note = "Databento budget read from manifest"
            else:
                status = schemas.UNKNOWN
                note = "Databento manifest has no cost column"
    except Exception as exc:  # pandas/pyarrow missing or unreadable parquet
        status = schemas.UNKNOWN
        note = f"Databento manifest unreadable: {exc}"
    out = {
        "status": status,
        "initial_credit": DATABENTO_INITIAL_CREDIT,
        "operating_cap": DATABENTO_OPERATING_CAP,
        "total_used": total_used,
        "remaining": (DATABENTO_INITIAL_CREDIT - total_used) if total_used is not None else None,
        "remaining_authoritative": total_used is not None,
        "manifest_rows": rows,
        "manifest": str(paths.DATABENTO_MANIFEST),
        "note": note,
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


def _options_data_readiness() -> dict:
    data = paths.read_json(paths.DATA_DOCTOR_REPORT)
    if not isinstance(data, dict):
        return {
            "status": schemas.MISSING,
            "note": "run scripts/data_doctor.py",
            "missing_checks": list(MANDATORY_OPTIONS_CHECKS),
        }
    report_utc = data.get("run_utc")
    all_checks = data.get("checks") or []
    options_checks = [c for c in all_checks if isinstance(c, dict) and str(c.get("name", "")).startswith("options-")]
    summary = data.get("options_lane")
    check_names = {str(c.get("name", "")) for c in options_checks}
    missing_checks = [name for name in MANDATORY_OPTIONS_CHECKS if name not in check_names]
    if not options_checks and summary is None:
        return {
            "status": schemas.MISSING,
            "note": "data_doctor report predates options checks",
            "report_utc": report_utc,
            "report_age": schemas.freshness(report_utc, stale_after_s=48 * 3600),
            "checks": options_checks,
            "summary": summary,
            "missing_checks": missing_checks,
        }
    readiness_checks = [
        c for c in options_checks
        if str(c.get("name", "")) not in ADVISORY_OPTIONS_CHECKS
    ]
    readiness_statuses = [str(c.get("status", "")).upper() for c in readiness_checks]
    report_age = schemas.freshness(report_utc, stale_after_s=48 * 3600)
    lake_present = paths.OPTIONS_LAKE_ROOT.is_dir()
    if "FAIL" in readiness_statuses:
        status = schemas.FAIL
    elif report_age == schemas.STALE:
        status = schemas.STALE
    elif not lake_present or not summary or missing_checks:
        status = schemas.MISSING
    elif any(s in _PROBLEM_CHECK_STATUSES for s in readiness_statuses):
        status = schemas.FAIL
    else:
        status = schemas.OK
    lake_root_path = paths.OPTIONS_LAKE_ROOT.parent
    return {
        "status": status,
        "report_utc": report_utc,
        "report_age": report_age,
        "checks": options_checks,
        "summary": summary,
        "missing_checks": missing_checks,
        "lake_present": lake_present,
        "lake_root": str(lake_root_path),
    }


def _options_defect_ledger() -> dict:
    try:
        from hft3.validation.options_defect_ledger import load_options_defect_ledger

        ledger = load_options_defect_ledger(paths.REPO)
        payload = ledger.to_dict()
        return {
            "status": schemas.OK if ledger.empty else schemas.FAIL,
            "open_count": ledger.open_count,
            "open_ids": list(ledger.open_ids),
            "ledger_status": ledger.status,
            "artifact": payload.get("artifact"),
            "reason": payload.get("reason"),
            "items": payload.get("items", []),
        }
    except Exception as exc:
        return {
            "status": schemas.FAIL,
            "open_count": 1,
            "open_ids": ["OPTIONS_LEDGER_UNREADABLE"],
            "ledger_status": "unreadable",
            "artifact": "specs/OPTIONS_LANE.md",
            "reason": str(exc),
            "items": [],
        }


def _lanes() -> dict:
    try:
        from hft3.validation.lanes.lane_registry import LaneRegistry
        from hft3.validation.lanes.registration import register_all_lanes
        register_all_lanes()
        reg = LaneRegistry.instance()
        lane_cert = paths.read_json(paths.LANE_CERT_REPORT)
        items = []
        for lane_reg in reg.all_registrations():
            value = lane_reg.lane.value
            cert_lookup = (lane_cert or {}).get("lane_coverage", {}).get(value, {}).get("run_result")
            cfg = None
            try:
                cfg = lane_reg.config_loader()
            except Exception:
                pass
            cap_profile = {}
            symbols: list = []
            if cfg is not None:
                try:
                    cap_profile = cfg.capability_profile.to_dict()
                except Exception:
                    pass
                try:
                    symbols = list(cfg.symbols)
                except Exception:
                    pass
            items.append({
                "lane": value,
                "capability_profile": cap_profile,
                "model_id_prefixes": list(lane_reg.model_id_prefixes),
                "symbols": symbols,
                "test_paths": list(lane_reg.test_paths),
                "certification": cert_lookup,
            })
        registered = [it["lane"] for it in items]
        return {
            "status": schemas.OK,
            "registered": registered,
            "items": items,
            "cme_options_data": _options_data_readiness(),
            "cme_options_defects": _options_defect_ledger(),
        }
    except Exception as exc:
        return {
            "status": schemas.MISSING,
            "registered": [],
            "items": [],
            "error": str(exc),
            "cme_options_data": _options_data_readiness(),
            "cme_options_defects": _options_defect_ledger(),
        }


def build() -> dict:
    latency = _latency()
    slow = _slow_tier()
    cert = _certification()
    databento = _databento()
    lanes = _lanes()
    cme_options_data = lanes.get("cme_options_data", {}) if isinstance(lanes, dict) else {}
    cme_options_defects = lanes.get("cme_options_defects", {}) if isinstance(lanes, dict) else {}
    components = [latency, slow, cert, databento, _capture()]
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
        "databento": databento,
        "capture": components[-1],
        "execution": _execution(),
        "lanes": lanes,
        "health_scope": "research_replay",
        "shadow_live_blockers": {
            "latency": latency.get("live_arm_status"),
            "cme_options_data": cme_options_data.get("status"),
            "cme_options_defects": cme_options_defects.get("status"),
        },
    }
