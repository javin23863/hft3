"""Ontology gate tests for HftBacktest campaign."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from backtest_pipeline.src.hft_campaign.ontology import (
    FEATURE_PLANE_STATUSES,
    resolve_certification_status,
    scenarios_for_config_stages,
    validate_feature_plane_status,
    validate_screening_feature_plane,
)
from backtest_pipeline.src.hft_campaign.scenario import HftReplayScenario


def _scenario(**overrides) -> HftReplayScenario:
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
    base.update(overrides)
    return HftReplayScenario(**base)


def test_feature_plane_status_enum():
    assert validate_feature_plane_status("scheduled_event_only") == []
    assert validate_feature_plane_status("bogus") == ["feature_plane_status_invalid:bogus"]
    assert "feature_complete_pit_declared" in FEATURE_PLANE_STATUSES


def test_feature_complete_requires_pit_proof():
    reasons = validate_screening_feature_plane({"feature_plane_status": "feature_complete_pit_declared"})
    assert "feature_complete_pit_declared_missing_proof" in reasons
    ok = validate_screening_feature_plane(
        {
            "feature_plane_status": "feature_complete_pit_declared",
            "feature_plane_pit_proof": {"status": "pass"},
        }
    )
    assert ok == []


def test_certification_fail_closed_without_native_evidence():
    status = resolve_certification_status(
        scenario_feature_plane="feature_complete_pit_declared",
        replay_tier="stage2_individual",
        transitional_handoff=False,
        accelerated_mode=False,
        source_lock_reasons=["native_hot_path_evidence_missing"],
        replay_passed=True,
    )
    assert status == "missing_native_hot_path_evidence"


def test_certification_scheduled_event_only_not_full_fidelity():
    hash_backed_lock = {
        "native_hot_path_evidence": [
            f"runtime/latency_reports/rithmic_latency_probe_latency_summary.json#sha256:{'a' * 64}",
            f"reports/cpp_lane/hft3_features_cpp_verify_cpp_parity.json#sha256:{'b' * 64}",
            f"reports/cpp_lane/risk_manager_atomic_stress_spsc_queue_stress_safety_poller_concurrent.json#sha256:{'c' * 64}",
            f"reports/cpp_lane/test_decision_runtime_hardening_test_safety_failure_injection.json#sha256:{'d' * 64}",
            f"reports/cpp_lane/test_engine_loop_hft3_engine.json#sha256:{'e' * 64}",
        ],
    }
    hash_backed_lock["native_hot_path_evidence_receipt_checks"] = [
        {
            "evidence": evidence,
            "path": evidence.split("#", 1)[0],
            "classes": [class_name],
            "status": "synthetic_non_git_fixture",
        }
        for evidence, class_name in zip(
            hash_backed_lock["native_hot_path_evidence"],
            ["latency", "features", "risk_concurrency", "decision_safety", "engine_loop"],
        )
    ]
    status = resolve_certification_status(
        scenario_feature_plane="scheduled_event_only",
        replay_tier="stage2_individual",
        transitional_handoff=False,
        accelerated_mode=False,
        source_lock_reasons=[],
        replay_passed=True,
        source_lock=hash_backed_lock,
    )
    assert status == "scheduled_event_replay_not_full_feature_plane"


def test_feature_complete_pit_blocks_stage2_until_replay_wired():
    scenario = _scenario(
        replay_tier="stage2_individual",
        feature_plane_status="feature_complete_pit_declared",
    )
    assert scenario.feature_plane_status == "feature_complete_pit_declared"
    assert scenario.replay_tier == "stage2_individual"


def test_vault_gate_invalid_timestamp(tmp_path: Path, monkeypatch):
    stamp_dir = tmp_path / "runtime" / "vault-gate"
    stamp_dir.mkdir(parents=True)
    stamp_path = stamp_dir / ".last-vault-gate.json"
    stamp_path.write_text(
        json.dumps({"timestamp_utc": "not-a-date", "hot_excerpt": "x" * 50}),
        encoding="utf-8",
    )
    from backtest_pipeline.src.hft_campaign.ontology import load_vault_gate_receipt

    _, reasons = load_vault_gate_receipt(tmp_path)
    assert "vault_gate_stamp_invalid_timestamp" in reasons


def test_replay_execution_passed_detects_official_status():
    from backtest_pipeline.src.hft_campaign.worker import _replay_execution_passed

    assert _replay_execution_passed({"steps": 1}) is True
    assert _replay_execution_passed({"error": 3, "steps": 1}) is False
    assert _replay_execution_passed({"official_hftbacktest_replay_status": "not_run"}) is False


def test_scenarios_for_config_stages_filters_tiers():
    s1 = _scenario(replay_tier="stage1_minimal")
    s2 = _scenario(scenario_id="s2", replay_tier="stage2_individual")
    s4 = _scenario(scenario_id="s4", replay_tier="stage4_combined")
    runnable, skipped = scenarios_for_config_stages([s1, s2, s4], (1, 2))
    assert [s.scenario_id for s in runnable] == ["s1", "s2"]
    assert [s.scenario_id for s in skipped] == ["s4"]
