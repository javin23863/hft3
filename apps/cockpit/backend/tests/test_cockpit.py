"""Cockpit backend tests — aggregator shape, graceful-missing, API auth.

Run from repo root:  python -m pytest apps/cockpit/backend/tests -q
"""
import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.cockpit.backend import loaders, paths
from apps.cockpit.backend import main as cockpit_main
from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.aggregate import system as system_agg
from apps.cockpit.backend.main import app

VIEW_KEYS = {"zone", "generated_utc", "health"}


def _json_roundtrip(obj):
    # Every zone payload must be JSON-serializable (FastAPI ships it as-is).
    return json.loads(json.dumps(obj))


def _write_options_spec(root: Path, status: str) -> None:
    specs = root / "specs"
    specs.mkdir(parents=True, exist_ok=True)
    (specs / "OPTIONS_LANE.md").write_text(
        "# OPTIONS_LANE.md\n\n"
        "| ID | Component | Description | Status |\n"
        "|----|-----------|-------------|--------|\n"
        f"| o-a | `vol_clock` | placeholder | {status} |\n",
        encoding="utf-8",
    )


def _options_ok_checks() -> list[dict]:
    return [{"name": name, "status": "OK", "detail": "ok"} for name in system_agg.MANDATORY_OPTIONS_CHECKS]


def _write_jsonl(path: Path, *records: dict) -> None:
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")


@pytest.mark.parametrize("name", list(ZONES))
def test_zone_shape(name):
    payload = ZONES[name]()
    assert VIEW_KEYS.issubset(payload), f"{name} missing base keys: {payload.keys()}"
    assert payload["zone"] == name
    _json_roundtrip(payload)  # raises if non-serializable


def test_pipeline_has_six_stages():
    p = ZONES["pipeline"]()
    ids = [s["id"] for s in p["stages"]]
    assert ids == ["capture", "feature_build", "stage_a", "gauntlet_b", "m6_gate", "promote"]
    for s in p["stages"]:
        assert {"id", "label", "status"}.issubset(s)


def test_models_registry_and_silent_zero():
    m = ZONES["models"]()
    assert m["registry_total"] == 50
    assert len(m["rows"]) == 50
    # The six structurally-dead prop hyps must be surfaced as silent-zero.
    assert m["silent_zero"]["count"] == 6
    dead_ids = {h["id"] for h in m["silent_zero"]["hypotheses"]}
    assert dead_ids == {20, 30, 32, 35, 36, 38}
    for row in m["rows"]:
        if row["id"] in dead_ids:
            assert row["structurally_dead"] is True
            assert row["status"] == "structurally_dead"


def test_portfolio_live_session_flag(monkeypatch):
    monkeypatch.setenv("EXECUTION_MODE", "REPLAY")
    p = ZONES["portfolio"]()
    assert p["live_session"] is False
    assert p["banner"] and "No live session" in p["banner"]


def test_portfolio_live_without_session_is_amber(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    monkeypatch.setattr(paths, "SESSIONS_ROOT", tmp_path / "sessions")
    p = ZONES["portfolio"]()
    assert p["live_session"] is False
    assert p["health"] == "amber"
    assert p["source"] == "no live session"
    assert any("no readable" in note for note in p["notes"])


def test_portfolio_uses_newest_session_artifact_over_directory_mtime(monkeypatch, tmp_path):
    monkeypatch.setenv("EXECUTION_MODE", "LIVE")
    sessions = tmp_path / "sessions"
    older_dir = sessions / "older-dir"
    newer_dir = sessions / "newer-dir"
    older_dir.mkdir(parents=True)
    newer_dir.mkdir(parents=True)
    _write_jsonl(older_dir / "positions.jsonl", {"timestamp_ns": 2, "positions": {"MES": 7}})
    _write_jsonl(older_dir / "kill_switch_events.jsonl", {"timestamp_ns": 3, "active": False})
    _write_jsonl(newer_dir / "positions.jsonl", {"timestamp_ns": 1, "positions": {"MES": 1}})
    now = time.time()
    os.utime(older_dir / "positions.jsonl", (now - 4, now - 4))
    os.utime(older_dir / "kill_switch_events.jsonl", (now - 3, now - 3))
    os.utime(newer_dir / "positions.jsonl", (now - 300, now - 300))
    os.utime(older_dir, (now - 1000, now - 1000))
    os.utime(newer_dir, (now - 1, now - 1))
    monkeypatch.setattr(paths, "SESSIONS_ROOT", sessions)

    p = ZONES["portfolio"]()

    assert p["live_session"] is True
    assert p["session_id"] == "older-dir"
    assert p["positions"] == [{"symbol": "MES", "quantity": 7}]
    assert p["session_age_s"] is not None and p["session_age_s"] < 30


def test_missing_artifact_is_graceful(monkeypatch, tmp_path):
    # Point Stage A at a nonexistent file; pipeline must render MISSING, not crash.
    monkeypatch.setattr(paths, "STAGE_A_RESULT", tmp_path / "nope.json")
    loaders._cache.clear()
    p = ZONES["pipeline"]()
    stage_a = next(s for s in p["stages"] if s["id"] == "stage_a")
    assert stage_a["status"] == "missing"


def test_alerts_quiet_when_healthy(monkeypatch, tmp_path):
    # Repoint every alert source at empty/missing → alert feed must be quiet.
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY", "CAPTURE_BASELINE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    _write_options_spec(tmp_path, "**FIXED**")
    monkeypatch.setattr(paths, "REPO", tmp_path)
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": _options_ok_checks(),
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    a = ZONES["alerts"]()
    assert a["count"] == 0
    assert a["health"] == "green"


# --- API + auth -------------------------------------------------------------

def test_api_requires_token_when_configured(monkeypatch):
    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    client = TestClient(app)  # no context => watcher/lifespan not started
    # No token from non-loopback testclient → 401.
    assert client.get("/api/pipeline").status_code == 401
    # Correct bearer → 200.
    r = client.get("/api/pipeline", headers={"Authorization": "Bearer secret-view"})
    assert r.status_code == 200
    assert r.json()["zone"] == "pipeline"


def test_health_open():
    client = TestClient(app)
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_spa_fallback_for_client_routes():
    client = TestClient(app)
    for route in ("/chat", "/models", "/lifecycle"):
        r = client.get(route)
        assert r.status_code == 200, route
        assert "text/html" in r.headers.get("content-type", ""), route
        assert '<div id="root">' in r.text, route
    # API + WS routes are NOT shadowed by the SPA catch-all
    h = client.get("/api/health")
    assert h.status_code == 200 and h.json()["status"] == "ok"
    # The GET-only catch-all must not capture the POST /api/chat route either
    # (no view token configured here → require_view 401, never an HTML body).
    chat = client.post("/api/chat", json={"query": "x"})
    assert "text/html" not in chat.headers.get("content-type", "")


def test_spa_catch_all_blocks_path_traversal():
    # The SPA fallback must never serve a file outside dist. URL-encoded `../`
    # is NOT normalized by the client, so it reaches the handler verbatim — the
    # resolve()+containment guard must reject it (else: arbitrary file read of
    # backend source / a .env with credentials).
    client = TestClient(app)
    evil = [
        "/..%2f..%2fbackend%2fvault_rag.py",
        "/..%2f..%2fbackend%2fmain.py",
        "/..%2f..%2f..%2f..%2f.env",
    ]
    for path in evil:
        r = client.get(path)
        # Either a clean 404 (no dist) or the index.html SPA fallback — never
        # the contents of a backend source / secrets file.
        assert "Keyword retrieval over the Obsidian vault" not in r.text, path
        assert "FastAPI aggregation service" not in r.text, path
        if r.status_code == 200:
            assert "text/html" in r.headers.get("content-type", ""), path


def test_rate_limit_ignores_xff_without_trusted_proxy(monkeypatch):
    monkeypatch.setattr(cockpit_main, "_RL_TRUST_PROXY", True)
    monkeypatch.setattr(cockpit_main, "_RL_TRUSTED_PROXIES", set())
    ip = cockpit_main._client_ip_for_rate_limit("testclient", "1.2.3.4")
    assert ip == "testclient"


def test_rate_limit_honors_xff_only_from_allowlisted_proxy(monkeypatch):
    monkeypatch.setattr(cockpit_main, "_RL_TRUST_PROXY", True)
    monkeypatch.setattr(cockpit_main, "_RL_TRUSTED_PROXIES", {"127.0.0.1"})
    assert cockpit_main._client_ip_for_rate_limit("127.0.0.1", "1.2.3.4, 127.0.0.1") == "1.2.3.4"
    assert cockpit_main._client_ip_for_rate_limit("testclient", "1.2.3.4, testclient") == "testclient"


def test_control_rejects_remote_origin():
    # TestClient origin is non-loopback ("testclient") → control forbidden.
    client = TestClient(app)
    r = client.post("/api/control/job", json={"name": "feature_rebuild", "confirm": True})
    assert r.status_code == 403


# --- notifier (push) --------------------------------------------------------

def test_push_notifies_only_on_new_problem(monkeypatch, tmp_path):
    from apps.cockpit.backend import push

    monkeypatch.setattr(paths, "ALERT_STATE", tmp_path / "alert_state.json")
    # No channel configured → notify is a no-op but diff/persist still works.
    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("COCKPIT_NOTIFY_WEBHOOK", raising=False)

    zone = {"alerts": [{"id": "cert-red", "severity": "crit", "source": "certification", "message": "RED"}]}
    first = push.process_alerts(zone)
    assert first == ["cert-red"]          # new problem detected
    second = push.process_alerts(zone)
    assert second == []                    # same standing problem → no re-notify
    # Cleared then recurs → notifies again.
    push.process_alerts({"alerts": []})
    third = push.process_alerts(zone)
    assert third == ["cert-red"]


def test_lifecycle_zone_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", tmp_path / "absent.json")
    z = ZONES["lifecycle"]()
    assert z["registered"] is False
    assert z["health"] == "green"
    assert z["total_models"] == 0


def test_lifecycle_zone_populated_and_alerts(monkeypatch, tmp_path):
    import json
    reg = tmp_path / "model_lifecycle.json"
    reg.write_text(json.dumps({
        "models": {
            "MES_X": {"current_state": "LIVE", "hypothesis_id": 1, "symbol": "MES", "current_state_since": "2026-06-12T00:00:00+00:00"},
            "MGC_Y": {"current_state": "QUARANTINED", "hypothesis_id": 35, "symbol": "MGC",
                       "demotion": {"reason": "feature_training_domain"}, "current_state_since": "2026-06-12T01:00:00+00:00"},
            "MCL_Z": {"current_state": "DEGRADED", "hypothesis_id": 7, "symbol": "MCL",
                       "reentry_routing": {"route": "param_tweak"}, "current_state_since": "2026-06-12T02:00:00+00:00"},
        }
    }))
    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", reg)
    z = ZONES["lifecycle"]()
    assert z["total_models"] == 3 and z["live"] == 1
    assert z["funnel"]["QUARANTINED"] == 1 and z["funnel"]["DEGRADED"] == 1
    assert z["health"] == "red"  # a QUARANTINED model
    # alerts feed surfaces the quarantine (crit) + degraded (warn)
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lifecycle-quar-MGC_Y" in ids
    assert any(al["severity"] == "crit" and al["source"] == "lifecycle" for al in a["alerts"])


def test_autonomy_zone_disabled_by_default(monkeypatch):
    monkeypatch.delenv("HFT3_AUTONOMY_ENABLED", raising=False)
    monkeypatch.delenv("HFT3_AUTONOMY_KILL", raising=False)
    z = ZONES["autonomy"]()
    assert z["available"] is True
    assert z["master_enabled"] is False     # two-key OFF by default
    assert z["can_arm_live"] is False
    assert z["health"] in ("green", "amber")  # green when unfrozen + chain ok


def test_push_no_channel_returns_false(monkeypatch):
    from apps.cockpit.backend import push

    monkeypatch.delenv("NTFY_URL", raising=False)
    monkeypatch.delenv("COCKPIT_NOTIFY_WEBHOOK", raising=False)
    assert push.channel() is None
    assert push.notify("t", "m") is False


# --- Lanes block ------------------------------------------------------------

def test_lanes_registered_contains_cme_options():
    """system zone lanes.registered must include 'cme_options'; its capability profile
    must be research_only and model_id_prefixes must contain a 'FOPT_' entry."""
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    assert "cme_options" in lanes.get("registered", []), \
        f"cme_options missing from registered: {lanes.get('registered')}"
    items = {it["lane"]: it for it in lanes.get("items", [])}
    cme_opts = items.get("cme_options", {})
    cp = cme_opts.get("capability_profile", {})
    assert cp.get("research_only") is True, f"cme_options research_only not True: {cp}"
    prefixes = cme_opts.get("model_id_prefixes", [])
    assert any("FOPT_" in p for p in prefixes), \
        f"FOPT_ prefix not in model_id_prefixes: {prefixes}"


def test_lanes_options_defect_ledger_open_blocks_readiness():
    z = ZONES["system"]()
    defects = z.get("lanes", {}).get("cme_options_defects", {})
    assert defects.get("status") == "fail"
    assert defects.get("open_count", 0) >= 1
    assert "o-a" in set(defects.get("open_ids", []))
    assert z.get("health") == "red"


def test_lanes_missing_data_doctor_report_is_graceful(monkeypatch, tmp_path):
    """Pointing DATA_DOCTOR_REPORT at a nonexistent file -> cme_options_data.status==missing;
    system zone health must not be green because readiness is unknown."""
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING, f"expected missing, got {cod.get('status')}"
    assert z.get("health") in {sc.AMBER, sc.RED}
    _json_roundtrip(z)


def test_lanes_options_warn_is_not_ok(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-mbo", "status": "OK", "detail": "10 files"},
            {"name": "options-statistics", "status": "WARN", "detail": "missing statistics"},
        ],
        "options_lane": {"name": "options_lane", "status": "WARN", "detail": "statistics missing"},
        "failed": 0,
        "warned": 1,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    z = ZONES["system"]()
    cod = z.get("lanes", {}).get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING
    assert "options-datasets" in cod.get("missing_checks", [])
    assert z.get("health") in {sc.AMBER, sc.RED}


def test_lanes_partial_options_report_missing_mandatory_checks(monkeypatch, tmp_path):
    lake = tmp_path / "options"
    lake.mkdir(parents=True)
    report_path = tmp_path / "data_doctor_report.json"
    present = [
        {"name": "options-datasets", "status": "OK", "detail": "ok"},
        {"name": "options-fixing-mbo", "status": "OK", "detail": "ok"},
        {"name": "options-fixing-coverage", "status": "OK", "detail": "ok"},
    ]
    report_path.write_text(
        json.dumps({
            "run_utc": paths.now_iso(),
            "checks": present,
            "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    monkeypatch.setattr(paths, "OPTIONS_LAKE_ROOT", lake)

    z = ZONES["system"]()
    cod = z.get("lanes", {}).get("cme_options_data", {})

    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING
    assert cod.get("missing_checks") == ["options-ohlcv", "options-definitions", "options-statistics"]


def test_lanes_synthetic_data_doctor_report(monkeypatch, tmp_path):
    """A synthetic data_doctor report with options-* checks (incl. one FAIL) and an
    options_lane summary -> cme_options_data status==fail, summary lifted, gap data visible."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-coverage", "status": "FAIL",
             "detail": "missing 2 expiry windows", "gap_count": 2, "stale_gap_count": 1},
            {"name": "options-fixing-mbo", "status": "OK", "detail": "10 quotes + 5 trades"},
            {"name": "options-ohlcv", "status": "OK", "detail": "42 files"},
        ],
        "options_lane": {"name": "options_lane", "status": "OK", "detail": "options_lane summary"},
        "failed": 1,
        "warned": 0,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.FAIL, f"expected fail, got {cod.get('status')}"
    # summary block must be lifted
    summary = cod.get("summary")
    assert summary is not None and summary.get("name") == "options_lane", \
        f"summary not lifted: {summary}"
    # options- checks present
    checks = cod.get("checks", [])
    assert any(c["name"] == "options-fixing-coverage" for c in checks), \
        f"options-fixing-coverage not in checks: {checks}"
    # gap detail accessible
    gap_check = next((c for c in checks if c.get("name") == "options-fixing-coverage"), None)
    assert gap_check is not None and gap_check.get("gap_count") == 2, \
        f"gap check missing or no detail: {gap_check}"
    _json_roundtrip(z)


def test_system_view_reads_real_options_gap_summary():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/SystemView.tsx").read_text(encoding="utf-8")
    assert "summary[\"expiry_coverage\"]" in src
    assert "expiryCoverage?.[\"gap_count\"]" in src


def test_system_view_renders_options_defect_details_and_budget_status():
    src = (paths.REPO / "apps/cockpit/frontend/src/views/SystemView.tsx").read_text(encoding="utf-8")
    assert 'g(defects, "open_ids")' in src
    assert 'join(", ")' in src
    assert '["defect ids", openIds]' in src
    assert '["defect artifact", defectArtifact]' in src
    assert '["defect reason", defectReason]' in src
    assert '<Card title="Databento" status={String(g(db, "status") ?? "unknown")}' in src


def test_databento_manifest_missing_is_not_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATABENTO_MANIFEST", tmp_path / "missing_manifest.parquet")
    monkeypatch.setattr(paths, "DATABENTO_RECEIPT", tmp_path / "missing_receipt.json")
    z = ZONES["system"]()
    db = z["databento"]
    from apps.cockpit.backend import schemas as sc
    assert db["status"] == sc.MISSING
    assert db["total_used"] is None
    assert db["remaining"] is None
    assert db["remaining_authoritative"] is False
    assert z["health"] in {sc.AMBER, sc.RED}


def test_alerts_missing_data_doctor_report(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-data-doctor-missing" in ids


def test_alerts_stale_data_doctor_report(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report_path.write_text(json.dumps({"run_utc": "2020-01-01T00:00:00+00:00", "checks": []}),
                           encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-data-doctor-stale" in ids


def test_alerts_options_warn_check_alert(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-statistics", "status": "WARN",
             "detail": "statistics pending"},
        ],
        "failed": 0,
        "warned": 1,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-statistics" in ids
    assert any(al["severity"] == "warn" for al in a["alerts"])


def test_alerts_missing_mandatory_options_checks(monkeypatch, tmp_path):
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-datasets", "status": "OK", "detail": "ok"},
            {"name": "options-fixing-mbo", "status": "OK", "detail": "ok"},
        ],
        "options_lane": {"name": "options_lane", "status": "OK", "detail": "ok"},
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-datasets-missing" not in ids
    assert "lake-options-fixing-coverage-missing" in ids
    assert "lake-options-ohlcv-missing" in ids
    assert "lake-options-definitions-missing" in ids
    assert "lake-options-statistics-missing" in ids


def test_alerts_options_defect_ledger_open():
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "options-defect-ledger-open" in ids


def test_alerts_options_fixing_coverage_alert(monkeypatch, tmp_path):
    """alerts zone with a failing options-fixing-coverage check -> alert id
    'lake-options-fixing-coverage' present in the alerts feed."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": paths.now_iso(),
        "checks": [
            {"name": "options-fixing-coverage", "status": "FAIL",
             "detail": "missing 3 expiry windows"},
        ],
        "failed": 1,
        "warned": 0,
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", report_path)
    # silence unrelated alert sources
    for attr in ("SLOW_TIER_PROBLEMS", "CERT_REGISTRY", "LATENCY_SUMMARY",
                 "CAPTURE_BASELINE", "MODEL_LIFECYCLE"):
        monkeypatch.setattr(paths, attr, tmp_path / f"{attr}.json")
    a = ZONES["alerts"]()
    ids = {al["id"] for al in a["alerts"]}
    assert "lake-options-fixing-coverage" in ids, \
        f"lake-options-fixing-coverage not in alert ids: {ids}"
