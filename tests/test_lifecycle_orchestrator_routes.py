import importlib
import json

import pytest

from model_metrics import lifecycle


@pytest.fixture()
def env(tmp_path, monkeypatch):
    lc_dir = tmp_path / "lifecycle"
    lc_dir.mkdir(parents=True)
    monkeypatch.setenv("HFT3_LIFECYCLE_DIR", str(lc_dir))
    routes = importlib.import_module("lifecycle_orchestrator.src.routes")
    job_runner = importlib.import_module("lifecycle_orchestrator.src.job_runner")
    return routes, job_runner, lc_dir, tmp_path


def _degraded(mid, route, *, cell=None):
    lifecycle.apply_transition(
        mid, lifecycle.CANDIDATE, trigger="register", reason="t", actor="t",
        create=True, initial={"hypothesis_id": 1, "symbol": "MES"},
    )
    for to in (lifecycle.SCREENING, lifecycle.GAUNTLET, lifecycle.CERTIFIED, lifecycle.SHADOW, lifecycle.LIVE):
        lifecycle.apply_transition(mid, to, trigger="advance", reason="t", actor="t")
    lifecycle.apply_transition(
        mid, lifecycle.DEGRADED, trigger="decay", reason="slippage drift", actor="t",
        route=route, record_updates={
            "demotion": {"reason": "slippage drift", "from_state": "LIVE"},
            "research_card_links": {"cell": cell or {"hyp_id": 5, "event_type": "CPI_TIGHT"}, "vectorbt": "research_cards/x.json"},
            "last_revalidation": {"model_state": "RED", "triggers": ["slippage"]},
        },
    )
    return lifecycle.get_record(mid)


@pytest.mark.parametrize("route,kind", [
    (lifecycle.ROUTE_REGIME_SHIFT, "archive_pause"),
    (lifecycle.ROUTE_PARAM_TWEAK, "gauntlet_retest"),
    (lifecycle.ROUTE_HYPOTHESIS_TWEAK, "screening_retest"),
    (lifecycle.ROUTE_EDGE_GONE, "retire_recommendation"),
])
def test_each_route_creates_expected_manifest(env, route, kind):
    routes, job_runner, _, _ = env
    rec = _degraded(f"M_{route}", route)
    info = routes.create_route_manifest(rec, reason="test", created_by="pytest")
    manifest = info["manifest"]
    assert manifest["route"] == route
    assert manifest["kind"] == kind
    assert manifest["model_id"] == rec.model_lifecycle_id
    assert manifest["source_evidence"]
    assert job_runner.validate_route_manifest(manifest) == []


def test_malformed_route_rejected(env):
    routes, _, _, _ = env
    rec = _degraded("BAD", lifecycle.ROUTE_PARAM_TWEAK)
    rec.reentry_routing = {"route": "not_a_route"}
    with pytest.raises(ValueError, match="unknown or missing route"):
        routes.build_route_manifest(rec, reason="x", created_by="t")


def test_missing_artifact_evidence_rejected(env):
    routes, _, _, _ = env
    rec = _degraded("NOEV", lifecycle.ROUTE_PARAM_TWEAK)
    rec.research_card_links = {}
    with pytest.raises(ValueError, match="missing source evidence"):
        routes.build_route_manifest(rec, reason="x", created_by="t")


def test_duplicate_manifest_id_rejected(env):
    routes, job_runner, _, _ = env
    rec = _degraded("DUP", lifecycle.ROUTE_PARAM_TWEAK)
    manifest = routes.build_route_manifest(rec, reason="one", created_by="t")
    manifest["manifest_id"] = "fixed_id"
    job_runner.write_route_manifest(manifest)
    with pytest.raises(FileExistsError):
        job_runner.write_route_manifest(manifest)
