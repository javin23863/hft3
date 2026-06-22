import importlib
import json
import textwrap

import pytest
from fastapi.testclient import TestClient

from apps.cockpit.backend.aggregate import ZONES
from apps.cockpit.backend.main import app


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from apps.cockpit.backend import auth

    monkeypatch.setattr(auth, "_LOOPBACK", {"testclient"})
    lc_dir = tmp_path / "lifecycle"
    lc_dir.mkdir(parents=True)
    reg = lc_dir / "model_lifecycle.json"
    reg.write_text(json.dumps({
        "models": {
            "MES_X": {
                "current_state": "DEGRADED",
                "hypothesis_id": 7,
                "symbol": "MES",
                "last_revalidation": {"model_state": "YELLOW"},
            }
        }
    }), encoding="utf-8")
    receipts = lc_dir / "operator_receipts.jsonl"
    from apps.cockpit.backend import paths

    monkeypatch.setattr(paths, "MODEL_LIFECYCLE", reg)
    monkeypatch.setattr(paths, "LIFECYCLE_OPERATOR_RECEIPTS", receipts)
    return TestClient(app), receipts, reg


def test_lifecycle_action_receipt_written(client):
    c, receipts, reg = client
    before = reg.read_text(encoding="utf-8")
    resp = c.post("/api/lifecycle/action", json={
        "model_id": "MES_X",
        "action": "request_rearm",
        "reason": "operator saw GREEN revalidation",
    })
    assert resp.status_code == 200
    after = reg.read_text(encoding="utf-8")
    assert before == after
    lines = receipts.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["action"] == "request_rearm"
    assert rec["actor"] == "control"
    assert rec["source_state"] == "DEGRADED"


def test_lifecycle_action_missing_reason_rejected(client):
    c, receipts, _ = client
    resp = c.post("/api/lifecycle/action", json={
        "model_id": "MES_X",
        "action": "request_retest",
        "reason": "",
    })
    assert resp.status_code == 422
    assert not receipts.exists()


def test_lifecycle_action_unknown_model_rejected(client):
    c, _, _ = client
    resp = c.post("/api/lifecycle/action", json={
        "model_id": "NOPE",
        "action": "request_quarantine",
        "reason": "suspect",
    })
    assert resp.status_code == 404


def test_lifecycle_action_path_traversal_rejected(client):
    c, _, _ = client
    resp = c.post("/api/lifecycle/action", json={
        "model_id": "../etc/passwd",
        "action": "request_retire",
        "reason": "bad id",
    })
    assert resp.status_code == 400
