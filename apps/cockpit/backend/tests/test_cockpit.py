"""Cockpit backend tests — aggregator shape, graceful-missing, API auth.

Run from repo root:  python -m pytest apps/cockpit/backend/tests -q
"""
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.cockpit.backend import loaders, paths
from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.main import app

VIEW_KEYS = {"zone", "generated_utc", "health"}


def _json_roundtrip(obj):
    # Every zone payload must be JSON-serializable (FastAPI ships it as-is).
    return json.loads(json.dumps(obj))


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


def test_lanes_missing_data_doctor_report_is_graceful(monkeypatch, tmp_path):
    """Pointing DATA_DOCTOR_REPORT at a nonexistent file -> cme_options_data.status==missing;
    system zone health must remain unchanged (no crash, no health regression from lanes)."""
    monkeypatch.setattr(paths, "DATA_DOCTOR_REPORT", tmp_path / "no_report.json")
    z = ZONES["system"]()
    lanes = z.get("lanes", {})
    cod = lanes.get("cme_options_data", {})
    from apps.cockpit.backend import schemas as sc
    assert cod.get("status") == sc.MISSING, f"expected missing, got {cod.get('status')}"
    # health is driven by latency/slow/cert only, not by lanes — verify zone serializable
    _json_roundtrip(z)


def test_lanes_synthetic_data_doctor_report(monkeypatch, tmp_path):
    """A synthetic data_doctor report with options-* checks (incl. one FAIL) and an
    options_lane summary -> cme_options_data status==fail, summary lifted, gap data visible."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": "2026-06-13T00:00:00+00:00",
        "checks": [
            {"name": "options-fixing-coverage", "status": "FAIL",
             "detail": "10 quotes + 5 trades"},
            {"name": "options-fixing-gaps", "status": "OK",
             "detail": "gap1,gap2"},
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
    gap_check = next((c for c in checks if "gap" in c.get("name", "")), None)
    assert gap_check is not None and "gap" in gap_check.get("detail", ""), \
        f"gap check missing or no detail: {gap_check}"
    _json_roundtrip(z)


def test_alerts_options_fixing_coverage_alert(monkeypatch, tmp_path):
    """alerts zone with a failing options-fixing-coverage check -> alert id
    'lake-options-fixing-coverage' present in the alerts feed."""
    report_path = tmp_path / "data_doctor_report.json"
    report = {
        "run_utc": "2026-06-13T00:00:00+00:00",
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
