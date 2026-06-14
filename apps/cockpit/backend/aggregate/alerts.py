"""Alerts zone — problem-only roll-up.

Quiet when healthy: an empty alert list + GREEN health is the steady state.
Aggregates the failure signals scattered across the other zones into one feed
the trader (and the outbound notifier, W4) can watch."""
from __future__ import annotations

from .. import paths, schemas
from .system import ADVISORY_OPTIONS_CHECKS, MANDATORY_OPTIONS_CHECKS, _q001_inventory


def _alert(id_: str, severity: str, source: str, message: str, ts=None) -> dict:
    return {"id": id_, "severity": severity, "source": source, "message": message, "ts": ts}


def _q001_alert() -> dict | None:
    q001 = _q001_inventory()
    status = q001.get("status")
    if status == schemas.OK:
        return None
    if status == schemas.FAIL:
        severity = schemas.SEV_CRIT
    elif status in {schemas.STALE, schemas.MISSING, schemas.UNKNOWN}:
        severity = schemas.SEV_WARN
    else:
        severity = schemas.SEV_WARN

    artifact = q001.get("artifact")
    if isinstance(artifact, str):
        artifact = artifact.replace("\\", "/")
    evidence = [
        f"q001_status={q001.get('q001_status')}",
        f"artifact={artifact}",
    ]
    for key in (
        "missing_or_unavailable_slots",
        "data_doctor_status",
        "strict_mbo_gap_count",
        "strict_mbo_stale_gap_count",
    ):
        if key in q001:
            evidence.append(f"{key}={q001.get(key)}")
    return _alert(
        "q001-paid-data-inventory",
        severity,
        "q001_inventory",
        f"Q001 paid-data inventory {status}: " + ", ".join(evidence),
    )


def collect() -> list[dict]:
    out: list[dict] = []

    # slow-tier problem feed (already problem-only)
    probs = paths.read_json(paths.SLOW_TIER_PROBLEMS)
    if isinstance(probs, dict):
        ts = probs.get("generated_utc")
        evald = paths.read_json(paths.SLOW_TIER_EVAL)
        gate_pass = isinstance(evald, dict) and evald.get("gate_pass") is True
        if not gate_pass:
            for i, msg in enumerate(probs.get("problems", []) or []):
                out.append(_alert(f"slowtier-{i}", schemas.SEV_WARN, "slow_tier", str(msg), ts))

    # certification RED
    cert = paths.read_json(paths.CERT_REGISTRY)
    if isinstance(cert, dict) and cert.get("latest_certification_status") == "RED":
        bf = cert.get("blocking_failures", [])
        out.append(_alert("cert-red", schemas.SEV_CRIT, "certification",
                          f"certification RED ({len(bf)} blocking)", cert.get("latest_certification_timestamp")))

    # latency overall FAIL (infra gate)
    lat = paths.read_json(paths.LATENCY_SUMMARY)
    if isinstance(lat, dict):
        rc = lat.get("report_card", {}) if isinstance(lat.get("report_card"), dict) else {}
        verdict = rc.get("verdict", {}) if isinstance(rc.get("verdict"), dict) else {}
        gates = lat.get("gates", {}) if isinstance(lat.get("gates"), dict) else {}
        research_ack_ok = gates.get("order_ack_pass") is True and lat.get("order_ack_measured") is True
        if verdict.get("overall") == "FAIL" and not research_ack_ok:
            out.append(_alert("latency-fail", schemas.SEV_WARN, "latency",
                              f"latency gate FAIL: {lat.get('dominant_bottleneck')}", lat.get("timestamp_utc")))

    # capture known gaps
    cap = paths.read_json(paths.CAPTURE_BASELINE)
    if isinstance(cap, dict):
        gaps = cap.get("known_gaps") or []
        if gaps:
            out.append(_alert("capture-gaps", schemas.SEV_CRIT, "capture",
                              f"{len(gaps)} known capture gap(s)", cap.get("captured_at")))

    # live kill-switch fired
    exec_mode = paths.execution_mode()
    import os
    if exec_mode == "LIVE" and os.environ.get("LIVE_KILL_SWITCH", "").lower() == "fired":
        out.append(_alert("killswitch-fired", schemas.SEV_CRIT, "execution", "LIVE kill switch FIRED", None))

    # lifecycle demotions — quarantined (crit), degraded/paused (warn)
    lc = paths.read_json(paths.MODEL_LIFECYCLE)
    if isinstance(lc, dict):
        for mid, rec in (lc.get("models", {}) or {}).items():
            state = rec.get("current_state")
            since = rec.get("current_state_since")
            if state == "QUARANTINED":
                reason = (rec.get("demotion") or {}).get("reason", "")
                out.append(_alert(f"lifecycle-quar-{mid}", schemas.SEV_CRIT, "lifecycle",
                                  f"{mid} QUARANTINED {reason}".strip(), since))
            elif state in ("DEGRADED", "ARCHIVED_PAUSED"):
                route = (rec.get("reentry_routing") or {}).get("route", "")
                out.append(_alert(f"lifecycle-deg-{mid}", schemas.SEV_WARN, "lifecycle",
                                  f"{mid} {state} -> {route}".strip(), since))

    # data lake health (scripts/data_doctor.py nightly report) — problem-only
    doc = paths.read_json(paths.DATA_DOCTOR_REPORT)
    if isinstance(doc, dict):
        ts = doc.get("run_utc")
        if schemas.freshness(ts, stale_after_s=48 * 3600) == schemas.STALE:
            out.append(_alert("lake-data-doctor-stale", schemas.SEV_WARN, "data_lake",
                              "data_doctor report STALE", ts))
        options_check_names = {
            str(c.get("name", ""))
            for c in doc.get("checks", []) or []
            if isinstance(c, dict) and str(c.get("name", "")).startswith("options-")
        }
        for name in MANDATORY_OPTIONS_CHECKS:
            if name not in options_check_names:
                out.append(_alert(f"lake-{name}-missing", schemas.SEV_CRIT, "cme_options_backfill",
                                  f"mandatory options check {name} MISSING", ts))
        for c in doc.get("checks", []) or []:
            status = str(c.get("status", "")).upper()
            if status in {"FAIL", "WARN", "WARNING", "MISSING", "STALE", "UNKNOWN"}:
                name = c.get("name")
                if str(name).startswith("options-"):
                    if str(name) in ADVISORY_OPTIONS_CHECKS:
                        continue
                    sev = schemas.SEV_CRIT
                    source = "cme_options_backfill"
                else:
                    if status != "FAIL":
                        continue
                    sev = schemas.SEV_CRIT if name == "disk-free" else schemas.SEV_WARN
                    source = "data_lake"
                out.append(_alert(f"lake-{name}", sev, source,
                                  f"lake check {name} {status}: {c.get('detail', '')}", ts))
    else:
        out.append(_alert("lake-data-doctor-missing", schemas.SEV_WARN, "data_lake",
                          "data_doctor report MISSING", None))

    q001_alert = _q001_alert()
    if q001_alert is not None:
        out.append(q001_alert)

    # options known-defect ledger — blocks shadow/live arm while non-empty
    try:
        from hft3.validation.options_defect_ledger import load_options_defect_ledger

        load_options_defect_ledger(paths.REPO)
        # The options defect ledger blocks shadow/live arm only; the System page
        # exposes the open ids. Do not turn the replay/research alert feed red for it.
    except Exception:
        pass

    # autonomy — frozen breaker or tampered audit chain
    try:
        from autonomy.status import snapshot
        snap = snapshot()
        br = snap.get("breaker", {}) or {}
        if br.get("frozen"):
            out.append(_alert("autonomy-frozen", schemas.SEV_CRIT, "autonomy",
                              f"autonomy FROZEN: {br.get('freeze_reason', '')}".strip(), br.get("frozen_at")))
        if snap.get("audit_chain_ok") is False:
            out.append(_alert("autonomy-chain-break", schemas.SEV_CRIT, "autonomy",
                              "autonomy audit hash-chain broken (tamper signal)", None))
    except Exception:
        pass

    return out


def build() -> dict:
    alerts = collect()
    if any(a["severity"] == schemas.SEV_CRIT for a in alerts):
        health = schemas.RED
    elif alerts:
        health = schemas.AMBER
    else:
        health = schemas.GREEN
    return {
        "zone": "alerts",
        "generated_utc": paths.now_iso(),
        "health": health,
        "count": len(alerts),
        "alerts": alerts,
    }
