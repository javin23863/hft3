"""Core HftBacktest campaign tests (no hftbacktest runtime required)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.cockpit.backend.tests.test_cockpit import (
    _screening_candidate_row,
    _write_screening_artifact,
)
from backtest_pipeline.src.hft_campaign import validation as campaign_validation
from backtest_pipeline.src.hft_campaign.accelerated import annotate_accelerated_replay
from backtest_pipeline.src.hft_campaign.artifacts import (
    commit_scenario_success,
    validate_cached_scenario,
    write_scenario_artifacts,
)
from backtest_pipeline.src.hft_campaign.manifest import (
    ManifestGenerationConfig,
    generate_scenario_manifest,
    select_replay_eligible_candidates,
)
from backtest_pipeline.src.hft_campaign.prepared_data import build_prepared_data_key
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario, compute_scenario_id
from backtest_pipeline.src.hft_campaign.worker import BoundedCache
from backtest_pipeline.src.vectorbt_adapter import validate_screening_artifact


def test_deterministic_scenario_id():
    payload = {
        "candidate_id": "c1",
        "event_id": "E1",
        "latency_model_hash": "abc",
        "seed": 0,
    }
    assert compute_scenario_id(payload) == compute_scenario_id(payload)
    payload["seed"] = 1
    assert compute_scenario_id(payload) != compute_scenario_id({"candidate_id": "c1", "event_id": "E1", "latency_model_hash": "abc", "seed": 0})


def test_scenario_hash_changes_with_latency():
    base = {
        "scenario_id": "s1",
        "upstream_screening_artifact": Path("screen.json"),
        "upstream_screening_artifact_hash": "h1",
        "candidate_id": "c1",
        "model_id": "HYP_5",
        "symbol": "MES.v.0",
        "event_id": "E1",
        "event_type": "screen",
        "prepared_data_path": Path("events.npz"),
        "prepared_data_hash": "pd1",
        "source_data_hash": "sd1",
        "feature_set_id": "f1",
        "feature_set_hash": "fh1",
        "research_clock": "continuous_intraday",
        "latency_model_path": Path("latency.json"),
        "latency_model_hash": "lh1",
        "fill_queue_model_path": Path("queue.json"),
        "fill_queue_model_hash": "qh1",
        "fee_model_id": "fee1",
        "split_scheme_id": "split1",
        "replay_mode": "baseline",
        "seed": 0,
    }
    a = HftReplayScenario(**base)
    b = HftReplayScenario(**{**base, "latency_model_hash": "lh2"})
    assert a.scenario_hash() != b.scenario_hash()


def test_reject_non_replay_eligible_candidate():
    row = _screening_candidate_row("run1", replay_eligible=False, surface_defined=True)
    screening = {"promoted": [row], "promoted_ids": [row["candidate_id"]]}
    selected, reasons = select_replay_eligible_candidates(screening, select_all_replay_eligible=True)
    assert selected == []
    assert reasons


def test_validate_screening_artifact_public_api(tmp_path: Path):
    artifact = _write_screening_artifact(
        tmp_path, "run1", "2026-01-01T00:00:00Z", replay_eligible=True, surface_defined=True
    )
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert validate_screening_artifact(payload) == []


def test_prepared_data_key_changes_with_event_id(tmp_path: Path):
    npz = tmp_path / "a.npz"
    npz.write_bytes(b"placeholder")
    key_a = build_prepared_data_key(source_npz_path=npz, repo_root=tmp_path, symbol="MES", event_id="E1")
    key_b = build_prepared_data_key(source_npz_path=npz, repo_root=tmp_path, symbol="MES", event_id="E2")
    assert key_a.prepared_data_hash() != key_b.prepared_data_hash()


def test_bounded_cache_eviction():
    cache = BoundedCache(max_entries=2)
    cache.put("a", 1)
    cache.put("b", 2)
    cache.put("c", 3)
    assert cache.get("a") is None
    assert cache.evictions >= 1


def test_atomic_scenario_artifacts(tmp_path: Path):
    scenario = HftReplayScenario(
        scenario_id="s1",
        upstream_screening_artifact=Path("screen.json"),
        upstream_screening_artifact_hash="h1",
        candidate_id="c1",
        model_id="HYP_5",
        symbol="MES.v.0",
        event_id="E1",
        event_type="screen",
        prepared_data_path=Path("events.npz"),
        prepared_data_hash="pd1",
        source_data_hash="sd1",
        feature_set_id="f1",
        feature_set_hash="fh1",
        research_clock="continuous_intraday",
        latency_model_path=Path("latency.json"),
        latency_model_hash="lh1",
        fill_queue_model_path=Path("queue.json"),
        fill_queue_model_hash="qh1",
        fee_model_id="fee1",
        split_scheme_id="split1",
        replay_mode="baseline",
        seed=0,
    )
    out = tmp_path / "scenario"
    write_scenario_artifacts(
        out,
        scenario=scenario,
        replay_result={"steps": 1, "balance": 0.0},
        replay_summary={"status": "pass"},
        timings={"replay_loop": 0.1},
        resource_usage={"worker_pid": 1},
    )
    ok, reasons = validate_cached_scenario(out, scenario, repo_commit="", package_version="")
    assert ok, reasons


def test_corrupted_cache_rejected(tmp_path: Path):
    scenario = HftReplayScenario(
        scenario_id="s1",
        upstream_screening_artifact=Path("screen.json"),
        upstream_screening_artifact_hash="h1",
        candidate_id="c1",
        model_id="HYP_5",
        symbol="MES.v.0",
        event_id="E1",
        event_type="screen",
        prepared_data_path=Path("events.npz"),
        prepared_data_hash="pd1",
        source_data_hash="sd1",
        feature_set_id="f1",
        feature_set_hash="fh1",
        research_clock="continuous_intraday",
        latency_model_path=Path("latency.json"),
        latency_model_hash="lh1",
        fill_queue_model_path=Path("queue.json"),
        fill_queue_model_hash="qh1",
        fee_model_id="fee1",
        split_scheme_id="split1",
        replay_mode="baseline",
        seed=0,
    )
    out = tmp_path / "scenario"
    out.mkdir(parents=True)
    commit_scenario_success(out)
    ok, reasons = validate_cached_scenario(out, scenario, repo_commit="", package_version="")
    assert not ok
    assert reasons


def test_accelerated_mode_non_certifying():
    payload = annotate_accelerated_replay({"steps": 10})
    assert payload["certification_status"] == "accelerated_not_certifying"


def test_manifest_requires_explicit_candidate_selection(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HFT3_ARTIFACTS_ROOT", str(tmp_path / "artifacts"))
    screening = _write_screening_artifact(
        tmp_path,
        "run1",
        "2026-01-01T00:00:00Z",
        replay_eligible=True,
        surface_defined=True,
    )
    npz = tmp_path / "data.npz"
    npz.write_bytes(b"not-a-real-npz")
    latency = tmp_path / "latency.json"
    queue = tmp_path / "queue.json"
    latency.write_text('{"order_entry_latency_ms": 1.0, "order_response_latency_ms": 1.0}\n')
    queue.write_text('{"fill_model_scope": "l3_mbo", "tick_size": 0.25, "lot_size": 1.0}\n')
    cfg = ManifestGenerationConfig(
        screening_artifact_path=screening,
        repo_root=tmp_path,
        event_id="E1",
        source_npz_path=npz,
        latency_model_path=latency,
        fill_queue_model_path=queue,
    )
    scenarios, reasons = generate_scenario_manifest(cfg)
    assert scenarios == []
    assert any("candidate_selection_not_explicit" in r for r in reasons)


def test_stage0_passes_explicit_repo_root_to_latency_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = tmp_path / "explicit_repo_root"
    latency_path, queue_path = _write_latency_queue(tmp_path)
    prepared_data_path = tmp_path / "prepared" / "events.npz"
    prepared_data_path.parent.mkdir()
    prepared_data_path.write_bytes(b"placeholder")
    captured: dict[str, Path | None] = {}

    monkeypatch.setattr(
        campaign_validation,
        "load_screening_artifact",
        lambda _path: (
            {"promoted": [{"candidate_id": "c1", "replay_eligibility_status": "eligible"}], "promoted_ids": ["c1"]},
            [],
            False,
        ),
    )
    monkeypatch.setattr(campaign_validation, "validate_screening_artifact", lambda _screening: [])
    monkeypatch.setattr(campaign_validation, "validate_screening_feature_plane", lambda _screening: [])
    monkeypatch.setattr(campaign_validation, "validate_candidate_replay_eligibility", lambda _row: [])
    monkeypatch.setattr(campaign_validation, "validate_feature_recipe_hash_handoff", lambda **_kwargs: [])
    monkeypatch.setattr(campaign_validation, "validate_feature_plane_status", lambda _status: [])
    monkeypatch.setattr(campaign_validation, "load_vault_gate_receipt", lambda _root: ({}, []))
    monkeypatch.setattr(campaign_validation, "validate_prepared_data_dir", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(campaign_validation, "validate_hftbacktest_data_path", lambda _path: {"status": "pass"})
    monkeypatch.setattr(campaign_validation, "validate_hftbacktest_fill_queue_model", lambda _model: [])
    monkeypatch.setattr(campaign_validation, "build_campaign_source_lock", lambda _root: ({}, []))

    def capture_latency_repo_root(_model: dict, *, repo_root: Path | None = None) -> list[str]:
        captured["repo_root"] = repo_root
        return []

    monkeypatch.setattr(campaign_validation, "validate_hftbacktest_latency_model", capture_latency_repo_root)

    result = campaign_validation.validate_stage0_scenario(
        HftReplayScenario(
            scenario_id="s1",
            upstream_screening_artifact=tmp_path / "screening.json",
            upstream_screening_artifact_hash="",
            candidate_id="c1",
            model_id="HYP_5",
            symbol="MES.v.0",
            event_id="E1",
            event_type="screen",
            prepared_data_path=prepared_data_path,
            prepared_data_hash="",
            source_data_hash="sd1",
            feature_set_id="f1",
            feature_set_hash="fh1",
            research_clock="continuous_intraday",
            latency_model_path=latency_path,
            latency_model_hash="",
            fill_queue_model_path=queue_path,
            fill_queue_model_hash="",
            fee_model_id="fee1",
            split_scheme_id="split1",
            replay_mode="baseline",
            seed=0,
        ),
        repo_root=repo_root,
    )

    assert result.ok, result.reasons
    assert captured["repo_root"] == repo_root


def _write_latency_queue(tmp_path: Path) -> tuple[Path, Path]:
    latency = tmp_path / "latency.json"
    queue = tmp_path / "queue.json"
    latency.write_text(
        json.dumps(
            {
                "latency_model_family": "ConstantLatency",
                "order_entry_latency_ms": 1.0,
                "order_response_latency_ms": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    queue.write_text(
        json.dumps(
            {
                "fill_model_scope": "l3_mbo",
                "queue_model_family": "L3FIFOQueueModel",
                "tick_size": 0.25,
                "lot_size": 1.0,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return latency, queue
