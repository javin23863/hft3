"""V1 — per-hypothesis construction + results drill-down (ast-parsed)."""
from fastapi.testclient import TestClient

from apps.cockpit.backend.aggregate import model_detail as md
from apps.cockpit.backend.main import app


def test_healthy_hyp_construction():
    d = md.build(2)  # StopRunExhaustionFade
    assert d["id"] == 2
    c = d["construction"]
    assert c is not None
    assert c["class_name"] == "StopRunExhaustionFade"
    assert "aggressor_volume_imbalance" in c["features"]
    assert "near_touch_cancel_pressure" in c["features"]
    assert "book_slope" in c["features"]
    assert d["structurally_dead"] is False
    assert "reads" in c["how_it_works"]
    assert "def evaluate" in c["evaluate_source"] or "evaluate" in c["evaluate_source"]


def test_event_gated_dead_hyp():
    d = md.build(30)  # CutoffPanicExits — gated + structurally dead
    c = d["construction"]
    assert "PROP_FLATTEN_TOPSTEP" in c["event_gates"]
    assert "FRIDAY_CLOSE" in c["event_gates"]
    assert d["structurally_dead"] is True
    assert "defect_note" in c


def test_cross_asset_hyp():
    d = md.build(20)  # MicroContractRetailLag — reads ES leg
    c = d["construction"]
    assert "ES" in c["cross_assets"]
    assert d["structurally_dead"] is True  # 20 in PROP_STRUCTURALLY_DEAD


def test_results_shape():
    d = md.build(1)
    r = d["results"]
    assert "stage_a_cells" in r and "total_trades" in r and "card" in r
    assert isinstance(r["stage_a_cells"], list)


def test_unknown_hyp_graceful():
    d = md.build(999)
    assert d["id"] == 999
    assert d["construction"] is None  # no class for 999


# --- HTTP route: auth gate + error envelope ---------------------------------

def test_model_route_requires_view_token(monkeypatch):
    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    client = TestClient(app)
    assert client.get("/api/model/2").status_code == 401
    r = client.get("/api/model/2", headers={"Authorization": "Bearer secret-view"})
    assert r.status_code == 200
    assert {"id", "construction", "results"} <= set(r.json())


def test_model_route_wraps_build_error(monkeypatch):
    # A build() crash must become a 200 JSON error envelope, not an unhandled 500.
    from apps.cockpit.backend.aggregate import model_detail as _md

    monkeypatch.setenv("COCKPIT_VIEW_TOKEN", "secret-view")
    monkeypatch.delenv("COCKPIT_CONTROL_TOKEN", raising=False)
    monkeypatch.setattr(_md, "build", lambda hyp_id: (_ for _ in ()).throw(RuntimeError("boom")))
    client = TestClient(app)
    r = client.get("/api/model/2", headers={"Authorization": "Bearer secret-view"})
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == 2 and "boom" in body["error"]
