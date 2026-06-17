from __future__ import annotations

import json
from pathlib import Path

import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt2
from backtest_pipeline.src.hftbacktest_realism import (
    validate_hftbacktest_latency_model,
    write_hftbacktest_realism_artifacts,
)
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash

from hft_screening_fixtures import (
    NATIVE_CPP_LATENCY_EVIDENCE,
    native_probe_latency_fields,
    screening_artifact_shell,
)


def _screening_artifact(candidate_id: str = "cand_hbt2") -> dict:
    return screening_artifact_shell("vbt_handoff_hbt2", candidate_id)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _native_probe_fields() -> dict:
    return native_probe_latency_fields()


def _base_latency_fields() -> dict:
    return {
        "feed_latency_source": "hftbacktest_event_local_exchange_timestamp_delta",
        "order_entry_latency_source": "hft3_native_cpp_rithmic_latency_probe",
        "order_response_latency_source": "hft3_native_cpp_rithmic_latency_probe",
        "latency_units": "milliseconds",
        "latency_value_or_sample_hash": f"sha256:{'b' * 64}",
        "latency_p50_ms": 3.1,
        "latency_p90_ms": 5.2,
        "latency_p99_ms": 6.256,
        "latency_source_authority": "hft3_native_cpp_latency_probe",
        "latency_proxy_status": "measured",
        "latency_component_mapping": {
            "feed_latency": "event local_ts - exch_ts, in milliseconds",
            "order_entry_latency": "native C++ req_ts to exch_ts, in milliseconds",
            "order_response_latency": "native C++ exch_ts to resp_ts, in milliseconds",
        },
    }


def _constant_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        **_native_probe_fields(),
        "latency_model_family": "ConstantLatency",
        "feed_latency_ms": 0.24,
        "order_entry_latency_ms": 3.128,
        "order_response_latency_ms": 3.128,
    }
    return model


def _feed_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        "latency_model_family": "FeedLatency",
        "order_entry_latency_source": "generated_from_feed_latency_proxy",
        "order_response_latency_source": "generated_from_feed_latency_proxy",
        "latency_source_authority": "hftbacktest_feed_latency_proxy",
        "latency_proxy_status": "proxy_only",
        "native_latency_probe_artifact": "not_run",
        "native_latency_probe_status": "not_run",
        "order_latency_unavailable_reason": "native order-latency samples not available for this research-only pass",
    }
    return model


def _intp_latency_model() -> dict:
    model = {
        **_base_latency_fields(),
        **_native_probe_fields(),
        "latency_model_family": "IntpOrderLatency",
        "latency_sample_artifact": "data/latency_baselines/2026-06-11/order_ack_campaign.jsonl",
        "latency_sample_row_count": 1002,
        "latency_sample_schema": ["req_ts", "exch_ts", "resp_ts", "_padding"],
        "interpolation_method": "hftbacktest.IntpOrderLatency",
    }
    return model


def test_constant_latency_requires_three_component_measured_native_contract() -> None:
    assert validate_hftbacktest_latency_model(_constant_latency_model()) == []


def test_constant_latency_rejects_missing_feed_component_and_bad_native_provenance() -> None:
    model = _constant_latency_model()
    model.pop("feed_latency_ms")
    model["native_latency_probe_artifact_hash"] = "sha256:not-a-digest"
    model["native_latency_probe_host"] = "workstation"
    model["native_latency_probe_provenance"] = "python_runtime_probe"

    reasons = validate_hftbacktest_latency_model(model)

    assert "invalid_constant_feed_latency_ms" in reasons
    assert "invalid_native_latency_probe_artifact_hash" in reasons
    assert "invalid_native_latency_probe_host" in reasons
    assert "invalid_native_latency_probe_provenance" in reasons


def test_measured_latency_rejects_non_measured_proxy_status_and_bad_value_hash() -> None:
    model = _constant_latency_model()
    model["latency_proxy_status"] = "synthetic"
    model["latency_value_or_sample_hash"] = "not-a-sha256"

    reasons = validate_hftbacktest_latency_model(model)

    assert "measured_latency_proxy_status_must_be_measured" in reasons
    assert "invalid_latency_value_or_sample_hash" in reasons


def test_latency_percentile_units_and_order_are_fail_closed() -> None:
    model = _constant_latency_model()
    model["latency_units"] = "microseconds"
    model["latency_p50_ms"] = 9.0
    model["latency_p99_ms"] = 6.256

    reasons = validate_hftbacktest_latency_model(model)

    assert "latency_units_must_be_milliseconds" in reasons
    assert "invalid_latency_percentile_order" in reasons


def test_interpolated_order_latency_requires_official_sample_schema() -> None:
    assert validate_hftbacktest_latency_model(_intp_latency_model()) == []

    model = _intp_latency_model()
    model["latency_sample_schema"] = ["req_ts", "resp_ts"]
    model["latency_sample_row_count"] = 0

    reasons = validate_hftbacktest_latency_model(model)

    assert "invalid_latency_sample_schema" in reasons
    assert "invalid_latency_sample_row_count" in reasons


def test_feed_latency_is_proxy_only_not_generic_fail() -> None:
    reasons = validate_hftbacktest_latency_model(_feed_latency_model())
    assert reasons == ["latency_proxy_only"]

    status = hbt2._replay_status_from_fail_reasons(
        ["hbt0_source_lock_only_replay_not_run", "latency_proxy_only"],
        {"data_validation_status": "not_run"},
    )
    assert status == "latency_proxy_only"


def test_feed_latency_requires_explicit_order_latency_unavailability_reason() -> None:
    model = _feed_latency_model()
    model.pop("order_latency_unavailable_reason")

    reasons = validate_hftbacktest_latency_model(model)

    assert "missing_order_latency_unavailable_reason" in reasons
    assert "latency_proxy_only" not in reasons


def test_missing_latency_model_remains_not_run_research_only() -> None:
    status = hbt2._replay_status_from_fail_reasons(
        ["hbt0_source_lock_only_replay_not_run", "data_npz_path_missing_hbt1_not_run", "latency_model_path_missing"],
        {"data_validation_status": "not_run"},
    )

    assert status == "research_only"


def test_write_artifacts_persists_latency_model_and_summary_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(hbt2, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt2, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt2,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )

    screening_path = _write_json(tmp_path / "screening_artifact.json", _screening_artifact())
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt2_test"

    payload = write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        latency_model_path=latency_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=["reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"],
        run_id="hbt2_test",
    )

    latency_artifact = json.loads((out_dir / "latency_model.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert latency_artifact["latency_model_status"] == "pass"
    assert summary["latency_model_family"] == "ConstantLatency"
    assert summary["latency_p99_ms"] == 6.256
    assert "latency_model_path_missing" not in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] == "research_only"
