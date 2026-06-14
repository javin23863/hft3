"""Workbench evidence snapshot coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import workbench.src.run.evidence_snapshot as evidence_snapshot
from workbench.src.run.evidence_snapshot import (
    _crypto_after_action,
    _crypto_pipeline_coverage,
    _crypto_reports,
    _crypto_relationships,
    _crypto_robustness_explanation,
    _crypto_self_learning_loop,
    _feature_fabric_snapshot,
    _lane_registry_snapshot,
    _latest_latency_baseline_summary,
    _latest_rithmic_trial_bundle,
    _crypto_validation_reports,
    _positive_proxy_pnl_count,
    _trade_manager_snapshot,
    load_run_evidence,
    default_source,
    workbench_run_sources,
)
from workbench.src.run.feature_fabric import ensure_catalog_feature_fabric


REPO = Path(__file__).resolve().parents[2]


def test_crypto_lane_source_is_stale_inside_active_all_lane_boundary(tmp_path: Path) -> None:
    active = tmp_path / "runtime" / "workbench" / "active_run.json"
    active.parent.mkdir(parents=True)
    active.write_text('{"run_id":"fresh_all_lanes_1","scope":"all_lanes"}', encoding="utf-8")

    snapshot = load_run_evidence(tmp_path, "crypto_lane")

    assert snapshot.current_stage == "stale_source_blocked"
    assert snapshot.decision["action"] == "BLOCKED"
    assert any(gate["gate"] == "stale_artifact_source" for gate in snapshot.decision["blocking_gates"])


def test_workbench_run_sources_cover_registered_model_lanes() -> None:
    sources = workbench_run_sources()

    assert "all_lanes" in sources
    assert "crypto_lane" not in sources
    assert "cme_rithmic" in sources
    assert "equities" in sources
    assert "options" in sources
    assert "autonomous" in sources


def test_active_run_manifest_makes_all_lanes_default(tmp_path: Path) -> None:
    active = tmp_path / "runtime" / "workbench" / "active_run.json"
    active.parent.mkdir(parents=True)
    active.write_text('{"run_id":"fresh_all_lanes_1","scope":"all_lanes"}', encoding="utf-8")
    old_smoke = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "latest_status.json"
    old_smoke.parent.mkdir(parents=True)
    old_smoke.write_text('{"run_id":"old_smoke","state":"completed"}', encoding="utf-8")

    assert default_source(tmp_path) == "all_lanes"


def test_all_lanes_snapshot_requires_active_run_and_terminal_states(tmp_path: Path) -> None:
    snapshot = load_run_evidence(tmp_path, "all_lanes")
    assert snapshot.source == "all_lanes"
    assert snapshot.current_stage == "fresh_start_required"
    assert any(gate["gate"] == "active_run_manifest" for gate in snapshot.decision["blocking_gates"])

    active = tmp_path / "runtime" / "workbench" / "active_run.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(
        json.dumps(
            {
                "run_id": "fresh_all_lanes_1",
                "scope": "all_lanes",
                "artifact_reuse_policy": "active_run_id_only",
                "source_data_reused": True,
                "previous_run_artifacts_reused": False,
            }
        ),
        encoding="utf-8",
    )
    run_dir = tmp_path / "runtime" / "workbench" / "all_lanes" / "fresh_all_lanes_1"
    run_dir.mkdir(parents=True)
    (run_dir / "plan.json").write_text(
        json.dumps(
            {
                "run_id": "fresh_all_lanes_1",
                "lane_model_counts": {"crypto": 1, "equities": 1, "cme_options": 0},
                "lane_coverage_gates": [
                    {
                        "gate": "lane_model_universe",
                        "status": "BLOCKING",
                        "lane": "cme_options",
                        "reason": "Registered lane has no model ids resolved from the Workbench model registry.",
                        "model_count": 0,
                    }
                ],
                "models": [
                    {"model_id": "A", "lane": "crypto", "terminal_state": "BLOCKED_MISSING_DATA"},
                    {"model_id": "B", "lane": "equities", "terminal_state": "EXECUTED"},
                ],
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_run_evidence(tmp_path, "all_lanes")
    assert snapshot.run_id == "fresh_all_lanes_1"
    assert snapshot.backtest["summary"]["planned"] == 2
    assert snapshot.data["lane_counts"]["cme_options"] == 0
    assert snapshot.diagnostics["leakage_boundary"]["lane_coverage_gates"][0]["lane"] == "cme_options"
    assert snapshot.decision["terminal_counts"]["EXECUTED"] == 1
    assert snapshot.decision["terminal_counts"]["BLOCKED_MISSING_DATA"] == 1
    assert snapshot.decision["blocking_gates"][0]["gate"] == "lane_model_universe"


def test_cme_rithmic_snapshot_surfaces_paper_endpoint_readiness() -> None:
    snapshot = load_run_evidence(REPO, "cme_rithmic")

    endpoint = snapshot.system["rithmic_endpoint"]
    assert snapshot.source == "cme_rithmic"
    assert endpoint["profile"] == "paper_chicago"
    assert endpoint["system"] == "Rithmic Paper Trading"
    assert endpoint["gateway"] == "Chicago Area"
    assert endpoint["missing_endpoint_params"] == []
    assert endpoint["reason_code"] in {
        "RITHMIC_CREDENTIALS_MISSING",
        "GATEWAY_LIBRARY_NOT_FOUND",
        "",
    }
    assert endpoint["credentials"]["redacted"] is True
    assert "username" not in endpoint
    assert "password" not in endpoint
    assert snapshot.latency["rithmic_order_ack"]["scope"] == "cme_rithmic_submit_to_ack"
    assert snapshot.decision["action"] == "QUARANTINE"


def test_latest_latency_baseline_summary_prefers_newest_observed_broker_run(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "latency_baselines"
    reports.mkdir(parents=True)
    (reports / "synthetic_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "synthetic",
                "generated_at_utc": "2026-06-04T00:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    (reports / "old_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "old",
                "generated_at_utc": "2026-06-04T00:01:00Z",
                "sample_path": str(tmp_path / "old.jsonl"),
                "broker_mode": {"status": "observed", "broker": "rithmic", "environment": "paper"},
                "metrics": {"send_to_ack_us": {"count": 1, "p50_us": 250000.0}},
            }
        ),
        encoding="utf-8",
    )
    (reports / "new_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "new",
                "generated_at_utc": "2026-06-04T00:02:00Z",
                "sample_path": str(tmp_path / "new.jsonl"),
                "broker_mode": {"status": "observed", "broker": "rithmic", "environment": "paper"},
                "metrics": {"send_to_ack_us": {"count": 2, "p50_us": 125000.0}},
            }
        ),
        encoding="utf-8",
    )

    summary = _latest_latency_baseline_summary(tmp_path, broker="rithmic", environment="paper")

    assert summary["run_id"] == "new"
    assert summary["_path"].endswith("new_summary.json")
    assert summary["_sample_path"].endswith("new.jsonl")


def test_latest_latency_baseline_summary_prefers_current_baseline(tmp_path: Path) -> None:
    reports = tmp_path / "reports" / "latency_baselines"
    reports.mkdir(parents=True)
    (reports / "current_baseline.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "baseline_role": "current_baseline",
                "run_id": "accepted",
                "generated_at_utc": "2026-06-04T00:01:00Z",
                "sample_path": str(tmp_path / "accepted.jsonl"),
                "broker_mode": {"status": "observed", "broker": "rithmic", "environment": "paper"},
                "metrics": {"send_to_ack_us": {"count": 1, "p50_us": 3000.0}},
            }
        ),
        encoding="utf-8",
    )
    (reports / "newer_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "newer-but-not-accepted",
                "generated_at_utc": "2026-06-04T00:03:00Z",
                "sample_path": str(tmp_path / "newer.jsonl"),
                "broker_mode": {"status": "observed", "broker": "rithmic", "environment": "paper"},
                "metrics": {"send_to_ack_us": {"count": 1, "p50_us": 2000.0}},
            }
        ),
        encoding="utf-8",
    )

    summary = _latest_latency_baseline_summary(tmp_path, broker="rithmic", environment="paper")

    assert summary["run_id"] == "accepted"
    assert summary["_baseline_role"] == "current_baseline"
    assert summary["_path"].endswith("current_baseline.json")


def test_cme_rithmic_snapshot_uses_latency_baseline_as_ack_evidence(tmp_path: Path, monkeypatch) -> None:
    reports = tmp_path / "reports" / "latency_baselines"
    reports.mkdir(parents=True)
    (reports / "paper_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "paper-hot",
                "generated_at_utc": "2026-06-04T00:02:00Z",
                "sample_path": str(tmp_path / "paper-hot.jsonl"),
                "broker_mode": {
                    "status": "observed",
                    "broker": "rithmic",
                    "environment": "paper",
                    "venue": "CME",
                },
                "broker_artifacts": {"stop_reason": "cancel_ack_timeout", "poll_interval_us": "0"},
                "metrics": {
                    "tick_to_send_us": {"count": 1, "p50_us": 26.8, "p99_us": 26.8},
                    "send_to_ack_us": {"count": 1, "p50_us": 220715.9, "p99_us": 220715.9},
                },
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("RITHMIC_ENDPOINT_PROFILE", "paper_chicago")
    snapshot = load_run_evidence(tmp_path, "cme_rithmic")

    assert snapshot.latency["latency_baseline"]["run_id"] == "paper-hot"
    assert snapshot.latency["rithmic_order_ack"]["order_ack_measured"] is True
    assert snapshot.latency["rithmic_order_ack"]["paired_count"] == 1
    assert snapshot.latency["rithmic_order_ack"]["source"] == "latency_baseline"


def test_latency_baseline_backfills_trigger_metrics_from_cpp_jsonl(tmp_path: Path) -> None:
    sample_dir = tmp_path / "data" / "latency_baselines" / "2026-06-04"
    sample_dir.mkdir(parents=True)
    sample_path = sample_dir / "cpp.jsonl"
    sample_path.write_text(
        json.dumps(
            {
                "run_id": "cpp",
                "order_action": "new",
                "success": True,
                "raw_timestamps": {
                    "market_event_received_ts": 1_000_000,
                    "decision_ready_ts": 1_001_000,
                    "order_api_call_start_ts": 1_003_000,
                    "order_send_ts": 1_043_000,
                    "ack_received_ts": 2_043_000,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    reports = tmp_path / "reports" / "latency_baselines"
    reports.mkdir(parents=True)
    (reports / "cpp_summary.json").write_text(
        json.dumps(
            {
                "schema_version": "latency_baseline_summary_v1",
                "run_id": "cpp",
                "generated_at_utc": "2026-06-04T00:02:00Z",
                # CHI404 summaries can carry remote absolute paths; the loader
                # must resolve the repo-relative data/latency_baselines suffix.
                "sample_path": f"/root/hft3/repo/{sample_path.relative_to(tmp_path).as_posix()}",
                "broker_mode": {
                    "status": "observed",
                    "broker": "rithmic",
                    "environment": "paper",
                    "venue": "CME",
                },
                "broker_artifacts": {"hot_path_language": "c++", "wrapper": "none"},
                "metrics": {
                    "tick_to_send_us": {"count": 1, "p50_us": 43.0},
                    "send_to_ack_us": {"count": 1, "p50_us": 1000.0},
                },
            }
        ),
        encoding="utf-8",
    )

    summary = _latest_latency_baseline_summary(tmp_path, broker="rithmic", environment="paper")

    assert summary["placement_trigger_kpi"] == "tick_to_send_trigger_us"
    assert summary["metrics"]["decision_to_send_trigger_us"]["p50_us"] == pytest.approx(2.0)
    assert summary["metrics"]["tick_to_send_trigger_us"]["p50_us"] == pytest.approx(3.0)
    assert summary["_sample_path"] == str(sample_path)


def test_latest_rithmic_trial_bundle_reads_observed_reports(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "raw" / "rithmic_trial_live_capture" / "2026-06-04" / "ESM6"
    raw_dir.mkdir(parents=True)
    (raw_dir / "events.ndjson").write_text('{"event_type":"trade"}\n', encoding="utf-8")
    raw_file = raw_dir / "events.ndjson"
    raw_checksum = "unit-checksum"
    (raw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "symbol": "ESM6",
                "exchange": "CME",
                "capture_environment": "rithmic_paper",
                "capture_start_time": "2026-06-04T10:00:00Z",
                "capture_end_time": "2026-06-04T10:00:15Z",
                "row_count": 103,
                "checksum_sha256": raw_checksum,
                "raw_file": str(raw_file),
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "reports" / "rithmic_trial" / "2026-06-04" / "ESM6"
    reports.mkdir(parents=True)
    normalized = tmp_path / "data" / "normalized" / "rithmic_trial_live_capture" / "2026-06-04" / "ESM6"
    normalized.mkdir(parents=True)
    normalized_file = normalized / "events.ndjson"
    normalized_file.write_text('{"event_type":"trade"}\n', encoding="utf-8")
    replay = tmp_path / "data" / "replay" / "hftbacktest" / "rithmic_trial" / "2026-06-04" / "ESM6"
    replay.mkdir(parents=True)
    replay_file = replay / "ESM6_2026-06-04_trial.npz"
    replay_file.write_bytes(b"npz")
    (reports / "data_capture_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "manifest": {
                    "raw_file": str(raw_file),
                    "row_count": 103,
                    "checksum_sha256": raw_checksum,
                },
            }
        ),
        encoding="utf-8",
    )
    (reports / "schema_mapping_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "raw_file": str(raw_file),
                "normalized_file": str(normalized_file),
            }
        ),
        encoding="utf-8",
    )
    (reports / "data_quality_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "input_files": [str(normalized_file)],
                "event_count": 103,
                "event_type_counts": {"trade": 43, "quote": 60},
                "checks": {"bad_prices": 0},
            }
        ),
        encoding="utf-8",
    )
    (reports / "hftbacktest_conversion_report.json").write_text(
        json.dumps({"status": "pass", "mode": "trade_only", "output_file": str(replay_file)}),
        encoding="utf-8",
    )
    (reports / "latency_profile.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "input_files": [str(normalized_file)],
                "paired_count": 0,
                "feed_latency_us": {"count": 103},
            }
        ),
        encoding="utf-8",
    )
    (reports / "paper_order_summary.json").write_text('{"paired_count":0}', encoding="utf-8")

    bundle = _latest_rithmic_trial_bundle(tmp_path)

    assert bundle["run_id"] == "rithmic_paper_2026-06-04_ESM6"
    assert bundle["row_count"] == 103
    assert bundle["trade_count"] == 43
    assert bundle["quote_count"] == 60
    assert bundle["paired_count"] == 0
    assert bundle["npz_exists"] is True
    assert bundle["normalized_exists"] is True
    assert bundle["report_binding_status"] == "PASS"
    assert bundle["report_binding_issues"] == []


def test_latest_rithmic_trial_bundle_blocks_stale_reports(tmp_path: Path) -> None:
    raw_dir = tmp_path / "data" / "raw" / "rithmic_trial_live_capture" / "2026-06-04" / "ESM6"
    raw_dir.mkdir(parents=True)
    raw_file = raw_dir / "events.ndjson"
    raw_file.write_text('{"event_type":"trade"}\n', encoding="utf-8")
    (raw_dir / "manifest.json").write_text(
        json.dumps(
            {
                "symbol": "ESM6",
                "exchange": "CME",
                "capture_environment": "rithmic_paper",
                "row_count": 236,
                "checksum_sha256": "fresh-checksum",
                "raw_file": str(raw_file),
            }
        ),
        encoding="utf-8",
    )
    reports = tmp_path / "reports" / "rithmic_trial" / "2026-06-04" / "ESM6"
    reports.mkdir(parents=True)
    stale_normalized = tmp_path / "data" / "normalized" / "rithmic_trial_live_capture" / "2026-06-04" / "MES"
    stale_normalized.mkdir(parents=True)
    stale_norm_file = stale_normalized / "events.ndjson"
    stale_norm_file.write_text('{"event_type":"trade"}\n', encoding="utf-8")
    (reports / "data_capture_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "manifest": {
                    "raw_file": str(raw_file),
                    "row_count": 103,
                    "checksum_sha256": "stale-checksum",
                },
            }
        ),
        encoding="utf-8",
    )
    (reports / "schema_mapping_report.json").write_text(
        json.dumps({"status": "pass", "raw_file": str(raw_file), "normalized_file": str(stale_norm_file)}),
        encoding="utf-8",
    )
    (reports / "data_quality_report.json").write_text(
        json.dumps(
            {
                "status": "pass",
                "input_files": [str(stale_norm_file)],
                "event_count": 103,
                "event_type_counts": {"trade": 43},
            }
        ),
        encoding="utf-8",
    )
    (reports / "latency_profile.json").write_text(
        json.dumps({"status": "pass", "input_files": [str(stale_norm_file)], "paired_count": 0}),
        encoding="utf-8",
    )
    (reports / "paper_order_summary.json").write_text('{"paired_count":0}', encoding="utf-8")
    (reports / "hftbacktest_conversion_report.json").write_text(
        json.dumps({"status": "pass", "mode": "trade_only", "output_file": "stale.npz"}),
        encoding="utf-8",
    )

    bundle = _latest_rithmic_trial_bundle(tmp_path)

    assert bundle["report_binding_status"] == "BLOCKING"
    issues = {issue["issue"] for issue in bundle["report_binding_issues"]}
    assert "checksum_mismatch" in issues
    assert "event_count_mismatch" in issues
    assert "normalized_input_mismatch" in issues
    assert "replay_output_mismatch" in issues


def test_equities_and_options_sources_are_lane_registry_backed() -> None:
    # Post lane-split: "equities" is the historical registry name of the
    # CME options/parity lane; the "options" source resolves to it.
    equities = load_run_evidence(REPO, "equities")
    options = load_run_evidence(REPO, "options")

    assert equities.registry["selected_lane"] == "equities"
    assert options.registry["selected_lane"] == "equities"
    assert equities.diagnostics["feature_fabric"]["consumer_lane"] == "equities"
    assert options.diagnostics["feature_fabric"]["consumer_lane"] == "equities"


def test_feature_fabric_blocks_when_artifacts_are_missing(tmp_path: Path) -> None:
    snapshot = _feature_fabric_snapshot(REPO, selected_root=tmp_path, consumer_lane="equities")

    assert snapshot["status"] == "BLOCKING"
    assert snapshot["gate_status"] == "BLOCKING"
    assert snapshot["evidence_gate_passed"] is False
    assert snapshot["pit_validation_status"] == "MISSING"
    assert any(gate["gate"] == "feature_fabric_artifacts" for gate in snapshot["blocking_gates"])
    assert any(gate["gate"] == "feature_fabric_lineage" for gate in snapshot["blocking_gates"])


def test_catalog_feature_fabric_generation_passes_all_lanes(tmp_path: Path) -> None:
    # Post lane-split: crypto removed; "equities" is the options/parity lane.
    for lane in ("cme_futures", "equities", "options"):
        root = tmp_path / lane
        result = ensure_catalog_feature_fabric(REPO, lane, output_root=root, run_id="fresh_run")
        snapshot = _feature_fabric_snapshot(
            REPO,
            selected_root=root,
            consumer_lane=lane,
            selected_run_id="fresh_run",
        )

        assert result["status"] == "PASS"
        assert result["run_id"] == "fresh_run"
        assert result["row_count"] > 0
        assert snapshot["gate_status"] == "PASS"
        assert snapshot["pit_validation_status"] == "PASS"
        assert snapshot["catalog_pit_eligibility_status"] == "PASS"
        assert snapshot["model_feature_usage_status"] == "not_observed"
        assert snapshot["blocking_gates"] == []
        assert {path.name for path in root.iterdir()} >= {
            "feature_fabric_manifest.json",
            "feature_lineage.json",
            "feature_pit_audit.json",
            "rejected_features.json",
        }
        assert {row["source_lane"] for row in snapshot["rows"]} >= {
            "cme_futures",
            "equities",
            "cme_options",
        }
        assert all(row["run_id"] == "fresh_run" for row in snapshot["rows"])
        assert all(row["evidence_scope"] == "catalog_eligibility_not_model_usage" for row in snapshot["rows"])


def test_feature_fabric_blocks_missing_active_run_identity(tmp_path: Path) -> None:
    ensure_catalog_feature_fabric(REPO, "equities", output_root=tmp_path)

    snapshot = _feature_fabric_snapshot(
        REPO,
        selected_root=tmp_path,
        consumer_lane="equities",
        selected_run_id="fresh_all_lanes",
    )

    assert snapshot["gate_status"] == "BLOCKING"
    assert any(gate["gate"] == "feature_fabric_run_identity" for gate in snapshot["blocking_gates"])
    assert snapshot["run_identity_issues"]


def test_feature_fabric_passes_with_observed_pit_safe_rows(tmp_path: Path) -> None:
    (tmp_path / "feature_fabric_manifest.json").write_text('{"run_id":"unit"}', encoding="utf-8")
    (tmp_path / "feature_lineage.json").write_text(
        '{"features":[{"feature":"btc_mempool_pressure","source_lane":"crypto",'
        '"asset":"BTC","source_available_timestamp":"2026-06-04T00:00:00Z",'
        '"decision_timestamp":"2026-06-04T00:00:01Z","pit_status":"PASS"}]}',
        encoding="utf-8",
    )
    (tmp_path / "feature_pit_audit.json").write_text('{"rows":[]}', encoding="utf-8")
    (tmp_path / "rejected_features.json").write_text('{"rows":[]}', encoding="utf-8")

    snapshot = _feature_fabric_snapshot(REPO, selected_root=tmp_path, consumer_lane="cme_futures")

    assert snapshot["status"] == "OBSERVED"
    assert snapshot["gate_status"] == "PASS"
    assert snapshot["evidence_gate_passed"] is True
    assert snapshot["pit_validation_status"] == "PASS"
    assert snapshot["blocking_gates"] == []


def test_feature_fabric_blocks_pit_leakage_rows(tmp_path: Path) -> None:
    (tmp_path / "feature_fabric_manifest.json").write_text('{"run_id":"unit"}', encoding="utf-8")
    (tmp_path / "feature_lineage.json").write_text(
        '{"features":[{"feature":"late_equity_signal","source_lane":"equities",'
        '"source_available_timestamp":"2026-06-04T00:00:02Z",'
        '"decision_timestamp":"2026-06-04T00:00:01Z","pit_status":"PASS"}]}',
        encoding="utf-8",
    )
    (tmp_path / "feature_pit_audit.json").write_text('{"rows":[]}', encoding="utf-8")
    (tmp_path / "rejected_features.json").write_text('{"rows":[]}', encoding="utf-8")

    snapshot = _feature_fabric_snapshot(REPO, selected_root=tmp_path, consumer_lane="options")

    assert snapshot["status"] == "BLOCKING"
    assert snapshot["pit_validation_status"] == "FAIL"
    assert any(gate["gate"] == "feature_pit_audit" for gate in snapshot["blocking_gates"])
    assert snapshot["pit_issues"][0]["issue"] == "source_available_after_decision_timestamp"


def test_catalog_feature_fabric_rejects_unsafe_cme_instrument(monkeypatch, tmp_path: Path) -> None:
    from workbench.src.data import instrument_registry
    from workbench.src.data.instrument_registry import InstrumentRecord

    safe = InstrumentRecord(
        canonical_internal_symbol="SAFE",
        research_symbol="SAFE.v.0",
        data_vendor_symbol="SAFE.c.0",
        hot_memory_tier="HOT_EXECUTABLE",
        instrument_type="futures",
        exchange="CME",
        venue="GLBX",
        asset_class="futures",
        tradable=True,
        order_book_available=True,
        trade_print_available=True,
        index_sensor_available=False,
        point_in_time_safe=True,
        data_delay_status="LIVE",
        live_feed_status="LIVE",
        historical_feed_status="LIVE",
    )
    unsafe = InstrumentRecord(
        canonical_internal_symbol="UNSAFE",
        research_symbol="UNSAFE.v.0",
        data_vendor_symbol="UNSAFE.c.0",
        hot_memory_tier="HOT_EXECUTABLE",
        instrument_type="futures",
        exchange="CME",
        venue="GLBX",
        asset_class="futures",
        tradable=True,
        order_book_available=True,
        trade_print_available=True,
        index_sensor_available=False,
        point_in_time_safe=False,
        data_delay_status="LIVE",
        live_feed_status="LIVE",
        historical_feed_status="LIVE",
    )
    monkeypatch.setattr(
        instrument_registry,
        "load_instrument_registry",
        lambda repo: {"SAFE": safe, "UNSAFE": unsafe},
    )

    result = ensure_catalog_feature_fabric(REPO, "cme_futures", output_root=tmp_path)
    snapshot = _feature_fabric_snapshot(REPO, selected_root=tmp_path, consumer_lane="cme_futures")
    rejected = json.loads((tmp_path / "rejected_features.json").read_text(encoding="utf-8"))

    assert result["status"] == "PASS"
    assert snapshot["gate_status"] == "PASS"
    assert any(row["source_symbol"] == "SAFE" for row in snapshot["rows"])
    assert not any(row["source_symbol"] == "UNSAFE" for row in snapshot["rows"])
    assert any(row["source_symbol"] == "UNSAFE" and row["reject_reason"] == "point_in_time_safe_false" for row in rejected["rows"])


def test_load_run_evidence_uses_catalog_feature_fabric_for_all_lane_sources() -> None:
    for source in ("all_lanes", "cme_rithmic", "equities", "options"):
        snapshot = load_run_evidence(REPO, source)
        fabric = snapshot.diagnostics["feature_fabric"]

        assert fabric["gate_status"] == "PASS"
        assert fabric["pit_validation_status"] == "PASS"
        assert fabric["catalog_pit_eligibility_status"] == "PASS"
        assert fabric["model_feature_usage_status"] == "not_observed"
        assert snapshot.current_stage != "feature_fabric_blocked"


def test_active_run_blocks_legacy_artifact_sources(tmp_path: Path) -> None:
    active = tmp_path / "runtime" / "workbench" / "active_run.json"
    active.parent.mkdir(parents=True, exist_ok=True)
    active.write_text(
        '{"run_id":"fresh_all_lanes","artifact_reuse_policy":"active_run_id_only"}',
        encoding="utf-8",
    )
    old_campaign = tmp_path / "research_cards" / "workbench_runs" / "old" / "summary.json"
    old_campaign.parent.mkdir(parents=True, exist_ok=True)
    old_campaign.write_text('{"run_id":"old","promote_candidate":true}', encoding="utf-8")

    snapshot = load_run_evidence(tmp_path, "workbench_campaign")

    assert snapshot.current_stage == "stale_source_blocked"
    assert snapshot.decision["action"] == "BLOCKED"
    assert any(gate["gate"] == "stale_artifact_source" for gate in snapshot.decision["blocking_gates"])


def test_no_active_run_does_not_load_latest_campaign_or_autonomous(tmp_path: Path) -> None:
    old_campaign = tmp_path / "research_cards" / "workbench_runs" / "old" / "summary.json"
    old_campaign.parent.mkdir(parents=True, exist_ok=True)
    old_campaign.write_text('{"run_id":"old","promote_candidate":true}', encoding="utf-8")
    old_autonomous = tmp_path / "artifacts" / "runs" / "old_ar" / "manifest.json"
    old_autonomous.parent.mkdir(parents=True, exist_ok=True)
    old_autonomous.write_text('{"run_id":"old_ar"}', encoding="utf-8")

    campaign = load_run_evidence(tmp_path, "workbench_campaign")
    autonomous = load_run_evidence(tmp_path, "autonomous")
    crypto = load_run_evidence(tmp_path, "crypto_lane")

    assert campaign.current_stage == "campaign_selection_required"
    assert autonomous.current_stage == "active_run_required"
    assert crypto.current_stage == "legacy_source_disabled"
    assert campaign.decision["action"] == autonomous.decision["action"] == crypto.decision["action"] == "BLOCKED"


def test_lane_registry_errors_are_workbench_blockers(monkeypatch) -> None:
    from hft3.validation.lanes.lane import Lane
    from hft3.validation.lanes.lane_registry import LaneRegistration, LaneRegistry

    registry = LaneRegistry.instance()

    def bad_loader() -> object:
        raise RuntimeError("unit lane config broke")

    monkeypatch.setattr(
        registry,
        "all_registrations",
        lambda: [
            LaneRegistration(
                lane=Lane.EQUITIES,
                adapter_factory=lambda: object(),
                config_loader=bad_loader,
                validator=lambda: object(),
                test_paths=["tests/unit_lane.py"],
            )
        ],
    )

    snapshot = _lane_registry_snapshot(REPO)

    assert snapshot["status"] == "BLOCKING"
    assert snapshot["rows"][0]["load_status"] == "error"
    assert snapshot["errors"][0]["lane"] == "equities"
    assert snapshot["blocking_gates"][0]["gate"] == "lane_registry"


def test_crypto_pipeline_coverage_does_not_overclaim_unwired_replay(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    reports = [
        {
            "candidate_id": "crypto_example",
            "purged_cv_implemented": True,
            "runs": {"with_btc_node": {"n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            "research_pnl_proxy": {"summary": {"net_pnl_bps": 10.0}},
        }
    ]
    candidate_rows = [
        {
            "candidate_id": "crypto_example",
            "purged_splits": 3,
            "holdout_status": "PASS",
            "negative_controls_ok": True,
            "proxy_net_pnl_bps": 10.0,
            "execution_ack_measured": False,
        }
    ]
    coverage = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        [],
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["purged_walk_forward_oos"]["status"] == "OBSERVED"
    assert by_stage["vectorbt_filter"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["robustness_pack"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["double_walk_forward_correlation"]["status"] == "PRESENT_NOT_WIRED"
    assert by_stage["research_pnl_proxy"]["status"] == "OBSERVED_DIAGNOSTIC_ONLY"
    assert by_stage["research_pnl_proxy"]["role"] == "diagnostic_only"
    assert by_stage["bitcoin_edge_packets"]["role"] == "market_state_only"
    assert by_stage["full_backtest_readiness"]["role"] == "aggregate_readiness_gate"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"
    assert "validation_report.json" in by_stage["hftbacktest_replay"]["artifact_contract"]


def test_trade_manager_snapshot_empty_state_is_explicit(tmp_path: Path) -> None:
    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "not_observed"
    assert snapshot["active_models"] == []
    assert snapshot["open_positions"] == []
    assert snapshot["open_orders"] == []
    assert snapshot["live_routing_status"] == "NOT_WIRED"
    assert "session_manifest.json" in snapshot["unavailable_artifacts"]


def test_trade_manager_snapshot_loads_phase23_session_artifacts(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-1"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"SESSION-1"}', encoding="utf-8")
    (session / "active_models.json").write_text(
        '{"active_models":[{"model_id":"MODEL_A","status":"ACTIVE","allowed_symbols":["ES"]}]}',
        encoding="utf-8",
    )
    (session / "registry_references.json").write_text("{}", encoding="utf-8")
    (session / "risk_limits.json").write_text('{"max_position_size":3}', encoding="utf-8")
    (session / "latency_metrics.json").write_text('{"p99_ns":1000,"status":"OK"}', encoding="utf-8")
    (session / "slippage_metrics.json").write_text('{"avg_ticks":0.25,"status":"OK"}', encoding="utf-8")
    (session / "session_metrics.json").write_text('{"orders":1,"fills":1,"status":"RUNNING"}', encoding="utf-8")
    (session / "order_intents.jsonl").write_text(
        '{"order_intent_id":"OI1","model_id":"MODEL_A","symbol":"ES"}\n',
        encoding="utf-8",
    )
    (session / "order_state_transitions.jsonl").write_text(
        '{"order_intent_id":"OI1","model_id":"MODEL_A","symbol":"ES","state":"RISK_APPROVED","timestamp_ns":10}\n',
        encoding="utf-8",
    )
    (session / "risk_rejections.jsonl").write_text(
        '{"order_intent_id":"OI2","model_id":"MODEL_A","symbol":"ES","reason":"MAX_SIZE","timestamp_ns":11}\n',
        encoding="utf-8",
    )
    (session / "fills.jsonl").write_text(
        '{"order_id":"O1","model_id":"MODEL_A","symbol":"ES","quantity":1,"price":5000,"timestamp_ns":12}\n',
        encoding="utf-8",
    )
    (session / "positions.jsonl").write_text(
        '{"symbol":"ES","quantity":1,"status":"OK","timestamp_ns":13}\n',
        encoding="utf-8",
    )
    (session / "pnl_timeseries.jsonl").write_text(
        '{"timestamp_ns":14,"total_pnl":12.5,"realized_pnl":10,"unrealized_pnl":2.5,"drawdown":-1}\n',
        encoding="utf-8",
    )
    (session / "incident_log.jsonl").write_text(
        '{"timestamp_ns":15,"severity":"INFO","message":"unit"}\n',
        encoding="utf-8",
    )
    (session / "kill_switch_events.jsonl").write_text(
        '{"timestamp_ns":16,"active":false,"status":"CLEAR"}\n',
        encoding="utf-8",
    )
    (session / "session_report.md").write_text("# Session Report", encoding="utf-8")

    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "observed"
    assert snapshot["session_id"] == "SESSION-1"
    assert snapshot["active_models"][0]["model_id"] == "MODEL_A"
    assert snapshot["open_positions"][0]["symbol"] == "ES"
    assert snapshot["open_orders"][0]["order_intent_id"] == "OI1"
    assert snapshot["pnl_latest"]["total_pnl"] == 12.5
    assert snapshot["latency"]["status"] == "OK"
    assert snapshot["kill_switch"]["status"] == "CLEAR"
    assert snapshot["artifact_counts"]["fills.jsonl"] == 1


def test_trade_manager_snapshot_uses_terminal_order_state_source_of_truth(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-TERMINAL"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"SESSION-TERMINAL"}', encoding="utf-8")
    (session / "active_models.json").write_text('{"active_models":[]}', encoding="utf-8")
    (session / "order_state_transitions.jsonl").write_text(
        '{"order_intent_id":"OI1","state":"BROKER_REJECTED","timestamp_ns":10}\n'
        '{"order_intent_id":"OI2","state":"KILLED","timestamp_ns":11}\n'
        '{"order_intent_id":"OI3","state":"ACKNOWLEDGED","timestamp_ns":12}\n',
        encoding="utf-8",
    )

    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "observed"
    assert [row["order_intent_id"] for row in snapshot["open_orders"]] == ["OI3"]


def test_trade_manager_snapshot_loads_native_position_snapshot_dict(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-POSITIONS"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"SESSION-POSITIONS"}', encoding="utf-8")
    (session / "active_models.json").write_text('{"active_models":[]}', encoding="utf-8")
    (session / "positions.jsonl").write_text(
        '{"timestamp_ns":20,"source":"unit","positions":{"ES":2.0,"NQ":0.0},"account_state":{"cash":1000.0}}\n',
        encoding="utf-8",
    )

    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "observed"
    assert snapshot["open_positions"][0]["positions"]["ES"] == 2.0


def test_trade_manager_snapshot_surfaces_malformed_artifacts(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-BAD"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":', encoding="utf-8")

    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "artifact_error"
    assert "MALFORMED_JSON" in snapshot["reason"]
    assert snapshot["active_models"] == []


def test_trade_manager_snapshot_surfaces_bad_position_quantities(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-BAD-POS"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"SESSION-BAD-POS"}', encoding="utf-8")
    (session / "positions.jsonl").write_text(
        '{"timestamp_ns":20,"positions":{"ES":"bad"}}\n',
        encoding="utf-8",
    )

    snapshot = _trade_manager_snapshot(tmp_path)

    assert snapshot["status"] == "artifact_error"
    assert "positions.ES: NUMERIC_REQUIRED" in snapshot["reason"]


def test_trade_manager_snapshot_does_not_attach_unlinked_session_to_selected_run(tmp_path: Path) -> None:
    session = tmp_path / "artifacts" / "sessions" / "SESSION-RUN-A"
    session.mkdir(parents=True)
    (session / "session_manifest.json").write_text('{"session_id":"SESSION-RUN-A"}', encoding="utf-8")
    (session / "active_models.json").write_text(
        '{"active_models":[{"model_id":"MODEL_A","run_id":"RUN-A","status":"ACTIVE"}]}',
        encoding="utf-8",
    )

    unlinked = _trade_manager_snapshot(tmp_path, selected_run_id="RUN-B")
    linked = _trade_manager_snapshot(tmp_path, selected_run_id="RUN-A")

    assert unlinked["status"] == "observed_unlinked"
    assert unlinked["selected_run_link_status"] == "UNLINKED"
    assert unlinked["active_models"][0]["run_id"] == "RUN-A"
    assert linked["status"] == "observed"
    assert linked["selected_run_link_status"] == "MATCHED"


def test_positive_proxy_pnl_count_is_defensive() -> None:
    assert _positive_proxy_pnl_count(
        [
            {"proxy_net_pnl_bps": 10.0},
            {"proxy_net_pnl_bps": "-1.5"},
            {"proxy_net_pnl_bps": None},
            {"proxy_net_pnl_bps": "bad"},
            {"proxy_net_pnl_bps": True},
        ]
    ) == 1


def test_crypto_after_action_and_relationship_snapshot_sections_load_run_artifacts(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    run_dir.mkdir(parents=True)
    (run_dir / "after_action_meta.json").write_text(
        '{"llm_status":"unavailable","skip_reasons":["NO_KEY"],"symbolic_passed":true,'
        '"report_written":false,"required":true,"gate_status":"FAIL","passed":false,'
        '"blocking_reason":"GPT-5.5 xhigh after-action did not pass"}',
        encoding="utf-8",
    )
    (run_dir / "after_action_symbolic.json").write_text('{"passed": true}', encoding="utf-8")
    (run_dir / "after_action_packet.json").write_text('{"skip_reasons":["NO_KEY"]}', encoding="utf-8")
    (run_dir / "kg_slice.json").write_text('{"nodes":[{}],"edges":[{}]}', encoding="utf-8")
    (run_dir / "relationship_summary.json").write_text(
        '{"candidate_count":2,"validated_count":1,"rejected_count":1,'
        '"kg_write_status":"not_attempted","openfoundry_write_status":"not_attempted","promotion_authority":false}',
        encoding="utf-8",
    )
    (run_dir / "relationship_candidates.json").write_text(
        '{"candidates":[{"status":"validated"},{"status":"rejected"}]}',
        encoding="utf-8",
    )

    after_action = _crypto_after_action(run_dir)
    relationships = _crypto_relationships(run_dir)

    assert after_action["llm_status"] == "unavailable"
    assert after_action["gate_status"] == "FAIL"
    assert after_action["passed"] is False
    assert after_action["symbolic_passed"] is True
    assert after_action["kg_slice"]["nodes"]
    assert relationships["candidate_count"] == 2
    assert relationships["kg_write_status"] == "not_attempted"
    assert relationships["promotion_authority"] is False


def test_crypto_robustness_explanation_separates_smoke_pass_from_required_failure() -> None:
    explanation = _crypto_robustness_explanation(
        {
            "status": "FAIL",
            "observed": True,
            "robustness_pack": {
                "checks": [
                    {"name": "negative_control", "status": "PASS", "passed": True},
                    {"name": "latency_sensitivity", "status": "FAIL", "passed": False},
                ],
                "failed": ["transaction_cost_sensitivity"],
            },
            "blocking_gates": [
                {
                    "gate": "robustness_pack",
                    "status": "FAIL",
                    "failed": ["model_combination_degradation"],
                }
            ],
        },
        [{"candidate_id": "crypto_candidate", "pass_fail": "pass"}],
    )

    assert explanation["aggregate_status"] == "FAIL"
    assert explanation["smoke_pass_count"] == 1
    assert explanation["smoke_pass_is_robustness_pass"] is False
    assert explanation["required_fail_count"] == 3
    assert "latency_sensitivity" in explanation["failed_required_checks"]
    assert "Smoke pass is only a prerequisite" in explanation["operator_explanation"]


def test_crypto_self_learning_loop_keeps_llm_advisory_only() -> None:
    loop = _crypto_self_learning_loop(
        {
            "stages": [
                {"name": "walk_forward_smokes", "status": "done"},
                {"name": "vectorbt_filter", "status": "done"},
                {"name": "hft_replay_validation", "status": "done"},
                {"name": "robustness_evidence", "status": "done"},
                {"name": "decision_gate", "status": "blocked"},
            ],
            "decision": {"action": "REJECT", "reason": "robustness evidence failed observed gates"},
        },
        {"llm_status": "unavailable"},
        {"candidate_count": 3},
        {"aggregate_status": "FAIL", "operator_explanation": "required checks failed"},
    )

    assert loop["llm_status"] == "unavailable"
    assert loop["llm_can_promote"] is False
    assert loop["relationship_review_only"] is True
    assert [row["step"] for row in loop["stages"]] == [
        "smoke",
        "VectorBT",
        "HFT replay",
        "robustness",
        "decision",
        "after-action",
        "relationship review",
        "LLM status",
    ]


def test_crypto_reports_prefers_selected_run_local_smoke_reports(tmp_path: Path) -> None:
    global_report = tmp_path / "research_cards" / "crypto" / "old_candidate" / "smoke_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text('{"candidate_id":"old_candidate","pass_fail":"pass"}', encoding="utf-8")

    run_report = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1" / "smoke_reports" / "current_candidate.json"
    run_report.parent.mkdir(parents=True, exist_ok=True)
    run_report.write_text('{"candidate_id":"current_candidate","pass_fail":"fail"}', encoding="utf-8")

    reports = _crypto_reports(tmp_path, run_report.parent.parent)

    assert [report["candidate_id"] for report in reports] == ["current_candidate"]
    assert reports[0]["_path"].endswith("smoke_reports\\current_candidate.json") or reports[0]["_path"].endswith("smoke_reports/current_candidate.json")


def test_crypto_pipeline_coverage_blocks_validation_report_without_vectorbt(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")
    report_path = tmp_path / "research_cards" / "crypto" / "crypto_example" / "validation_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        """
{
  "candidate_id": "crypto_example",
  "model_id": "CRYPTO_H1",
  "asset_class": "CRYPTO",
  "execution_classification": "L2_PROXY_ONLY",
  "validation_path": "L2_PROXY_VALIDATION",
  "npz_path": "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz",
  "result": {
    "net_pnl": 12.5,
    "num_trades": 3,
    "num_intents": 4,
    "fill_rate": 0.75,
    "error": ""
  },
  "notes": []
}
""".strip(),
        encoding="utf-8",
    )

    validation_reports = _crypto_validation_reports(tmp_path)
    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        validation_reports,
        [{"candidate_id": "crypto_example", "execution_ack_measured": False}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert validation_reports[0]["candidate_id"] == "crypto_example"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert by_stage["hftbacktest_replay"]["role"] == "required_execution_replay"
    assert "upstream VectorBT" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_l2_proxy_does_not_complete_execution_realism(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L2_PROXY_ONLY",
                "npz_path": "data/replay/hftbacktest/crypto/binance/btcusdt/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "L3/full execution replay evidence" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["execution_realism"]["status"] == "BLOCKING"
    assert "L3/full replay evidence" in by_stage["execution_realism"]["reason"]


def test_crypto_pipeline_coverage_l3_plus_ack_completes_execution_realism(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "OBSERVED"
    assert by_stage["execution_realism"]["status"] == "OBSERVED"


def test_crypto_validation_reports_use_only_selected_run_artifacts(tmp_path: Path) -> None:
    run_report = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1" / "validation_reports" / "crypto_run.json"
    run_report.parent.mkdir(parents=True, exist_ok=True)
    run_report.write_text(
        '{"candidate_id":"crypto_run","execution_classification":"NO_EXECUTION","result":{"error":"missing npz"}}',
        encoding="utf-8",
    )
    global_report = tmp_path / "research_cards" / "crypto" / "crypto_global" / "validation_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text(
        '{"candidate_id":"crypto_global","execution_classification":"L2_PROXY_ONLY","npz_path":"sample.npz","result":{"error":""}}',
        encoding="utf-8",
    )

    reports = _crypto_validation_reports(tmp_path, run_report.parent.parent)

    assert [r["candidate_id"] for r in reports] == ["crypto_run"]
    assert reports[0]["_path"].endswith("validation_reports\\crypto_run.json") or reports[0]["_path"].endswith("validation_reports/crypto_run.json")


def test_crypto_validation_reports_ignore_global_when_selected_run_has_no_validation(tmp_path: Path) -> None:
    run_dir = tmp_path / "runtime" / "workbench" / "crypto_smoke" / "run_1"
    run_dir.mkdir(parents=True, exist_ok=True)
    global_report = tmp_path / "research_cards" / "crypto" / "crypto_global" / "validation_report.json"
    global_report.parent.mkdir(parents=True, exist_ok=True)
    global_report.write_text(
        '{"candidate_id":"crypto_global","execution_classification":"L2_PROXY_ONLY","npz_path":"sample.npz","result":{"error":""}}',
        encoding="utf-8",
    )

    assert _crypto_validation_reports(tmp_path, run_dir) == []


def test_crypto_pipeline_coverage_blocks_failed_validation_attempt(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "NO_EXECUTION",
                "npz_path": "",
                "result": {"error": "No execution data available for this candidate"},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": False}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "Validation was attempted" in by_stage["hftbacktest_replay"]["reason"]


def test_crypto_pipeline_coverage_blocks_failed_vectorbt_stage(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [
            {
                "candidate_id": "crypto_example",
                "purged_cv_implemented": True,
                "runs": {"with_btc_node": {"n_splits": 3}},
                "holdout_gate": {"status": "PASS"},
                "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            }
        ],
        [
            {
                "candidate_id": "crypto_example",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
            }
        ],
        [{"candidate_id": "crypto_example", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "robustness_pack": {"observed": True},
            "double_walk_forward": {"observed": True},
        },
        {
            "status": "BLOCKING",
            "observed": False,
            "reason": "The vectorBT package is not installed in the active Workbench runtime.",
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["vectorbt_filter"]["status"] == "BLOCKING"
    assert "vectorBT package" in by_stage["vectorbt_filter"]["reason"]
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT filter" in by_stage["hftbacktest_replay"]["reason"]
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_blocks_hft_when_vectorbt_blocks_before_replay(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [],
        [{"candidate_id": "crypto_example"}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {},
        {
            "status": "BLOCKING",
            "observed": False,
            "reason": "The vectorBT package is not installed in the active Workbench runtime.",
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["vectorbt_filter"]["status"] == "BLOCKING"
    assert by_stage["hftbacktest_replay"]["status"] == "BLOCKING"
    assert "upstream VectorBT filter" in by_stage["hftbacktest_replay"]["reason"]


def test_crypto_pipeline_coverage_full_readiness_requires_observed_vectorbt(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    reports = [
        {
            "candidate_id": "crypto_example",
            "purged_cv_implemented": True,
            "runs": {"with_btc_node": {"n_splits": 3}},
            "holdout_gate": {"status": "PASS"},
            "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
        }
    ]
    validation_reports = [
        {
            "candidate_id": "crypto_example",
            "execution_classification": "L3_VALIDATED",
            "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
            "result": {"error": "", "trade_pnls": [1.0, -0.5, 0.25]},
        }
    ]
    candidate_rows = [{"candidate_id": "crypto_example", "execution_ack_measured": True}]
    robustness_summary = {
        "robustness_pack": {"observed": True},
        "double_walk_forward": {"observed": True},
        "trade_sample_candidate_ids": ["crypto_example"],
    }

    without_vectorbt = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        validation_reports,
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        robustness_summary,
    )
    with_vectorbt = _crypto_pipeline_coverage(
        tmp_path,
        reports,
        validation_reports,
        candidate_rows,
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        robustness_summary,
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_example"],
        },
    )

    assert {row["stage"]: row for row in without_vectorbt}["full_backtest_readiness"]["status"] == "BLOCKING"
    assert {row["stage"]: row for row in with_vectorbt}["full_backtest_readiness"]["status"] == "OBSERVED"


def test_crypto_pipeline_coverage_blocks_cross_candidate_readiness(tmp_path: Path) -> None:
    for rel in (
        "packages/backtest_pipeline/src/vectorbt_adapter.py",
        "packages/backtest_pipeline/src/hft_backtest_builder.py",
        "packages/backtest_pipeline/src/signal_backtester.py",
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [
            {
                "candidate_id": "crypto_a",
                "purged_cv_implemented": True,
                "runs": {"with_btc_node": {"n_splits": 3}},
                "holdout_gate": {"status": "PASS"},
                "negative_controls": {"shuffled_degraded": True, "shifted_degraded": True},
            }
        ],
        [
            {
                "candidate_id": "crypto_b",
                "execution_classification": "L3_VALIDATED",
                "npz_path": "data/replay/hftbacktest/crypto/kraken/BTC_USD/sample.npz",
                "result": {"error": "", "trade_pnls": [1.0]},
            }
        ],
        [{"candidate_id": "crypto_c", "execution_ack_measured": True}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "robustness_pack": {"observed": True},
            "double_walk_forward": {"observed": True},
            "trade_sample_candidate_ids": ["crypto_b"],
        },
        {
            "status": "OBSERVED",
            "observed": True,
            "promoted_source_candidate_ids": ["crypto_a"],
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"
    assert "same candidate" in by_stage["full_backtest_readiness"]["reason"]


def test_crypto_pipeline_coverage_blocks_failed_robustness_stage(tmp_path: Path) -> None:
    for rel in (
        "apps/workbench/src/robustness/pack.py",
        "apps/workbench/src/robustness/wfc/double_wf.py",
    ):
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# present\n", encoding="utf-8")

    coverage = _crypto_pipeline_coverage(
        tmp_path,
        [],
        [],
        [{"candidate_id": "crypto_example"}],
        {"observed": True, "transport": "length_prefixed_protobuf_tcp"},
        {
            "status": "BLOCKING",
            "observed": False,
            "robustness_pack": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No replay trade_pnls or fill_events were emitted by crypto execution validation.",
            },
            "double_walk_forward": {
                "status": "BLOCKING",
                "observed": False,
                "reason": "No independent walk-forward matrices were emitted by crypto replay validation.",
            },
        },
    )
    by_stage = {row["stage"]: row for row in coverage}

    assert by_stage["robustness_pack"]["status"] == "BLOCKING"
    assert "trade_pnls" in by_stage["robustness_pack"]["reason"]
    assert by_stage["double_walk_forward_correlation"]["status"] == "BLOCKING"
    assert "walk-forward matrices" in by_stage["double_walk_forward_correlation"]["reason"]
    assert by_stage["full_backtest_readiness"]["status"] == "BLOCKING"


def test_crypto_pipeline_coverage_rejects_unknown_status() -> None:
    from workbench.src.run.evidence_snapshot import _coverage_row

    try:
        _coverage_row("bad_layer", "MADE_UP_STATUS", "artifact", "reason")
    except ValueError as exc:
        assert "unknown coverage status" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("coverage row accepted an unknown status")
