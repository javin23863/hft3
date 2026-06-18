"""Tests for HftBacktest three-component latency helpers."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "packages"))

from backtest_pipeline.src.latency_components import (  # noqa: E402
    build_new_send_to_ack_from_live_stats,
    critical_bands_measured,
    default_component_bands,
    enrich_latency_model_from_component_bands,
    merge_component_bands_from_cc_summaries,
    resolve_new_send_to_ack_ms,
)
from backtest_pipeline.src.chi404_latency import (  # noqa: E402
    build_latency_model_from_summary,
    enrich_latency_model_probe_evidence,
    resolve_latency_model,
)
from backtest_pipeline.src.hftbacktest_realism import validate_hftbacktest_latency_model  # noqa: E402


def test_build_new_send_to_ack_distribution() -> None:
    dist = build_new_send_to_ack_from_live_stats(
        {"count": 200, "p50_us": 3537.0, "p99_us": 9810.0}
    )
    assert dist is not None
    assert dist["ms"]["p99_ms"] == pytest.approx(9.81)
    assert dist["metric"] == "new_send_to_ack"


def test_resolve_new_send_to_ack_prefers_canonical_block() -> None:
    summary = {
        "new_send_to_ack_ms": build_new_send_to_ack_from_live_stats(
            {"count": 10, "p99_us": 5000.0}
        ),
        "live_order_ack_p99_ms": 9.0,
    }
    p99, block, source = resolve_new_send_to_ack_ms(summary)
    assert p99 == pytest.approx(5.0)
    assert block is not None
    assert source == "new_send_to_ack_ms.authoritative"


def test_default_component_bands_marks_critical_open() -> None:
    bands = default_component_bands()
    assert bands["feed_latency_us"]["measurement_status"] == "OPEN"
    assert not critical_bands_measured(bands)


def test_merge_component_bands_from_cc3_summary(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    cc3 = {
        "run_id": "cc3_new_decomp_fixture",
        "metrics": {
            "feed_latency_us": {"count": 200, "p50_us": 2400.0, "p99_us": 8700.0},
            "new_send_to_exchange_us": {"count": 200, "p50_us": 2900.0, "p99_us": 3600.0},
            "new_exchange_to_ack_us": {"count": 200, "p50_us": 370.0, "p99_us": 1800.0},
        },
    }
    (baselines / "cc3_new_decomp_20260618T000001Z_summary.json").write_text(
        json.dumps(cc3), encoding="utf-8"
    )
    cc4 = {
        "run_id": "cc4_cancel_20260618T000001Z",
        "metrics": {
            "cancel_send_to_exchange_us": {"count": 0},
            "cancel_exchange_to_ack_us": {"count": 0},
        },
    }
    (baselines / "cc4_cancel_20260618T000001Z_summary.json").write_text(json.dumps(cc4), encoding="utf-8")

    merged = merge_component_bands_from_cc_summaries(tmp_path, default_component_bands())
    assert merged["feed_latency_us"]["measurement_status"] == "MEASURED"
    assert merged["feed_latency_us"]["sample_count"] == 200
    assert merged["feed_latency_us"]["source_run_id"] == "cc3_new_decomp_fixture"
    assert merged["new_send_to_exchange_us"]["distribution_us"]["p99_us"] == pytest.approx(3600.0)
    assert merged["cancel_exchange_to_ack_us"]["measurement_status"] == "UNMEASURED"
    assert "cc4_cancel_20260618T000001Z" in merged["cancel_exchange_to_ack_us"]["note"]
    assert merged["cancel_send_to_exchange_us"]["measurement_status"] == "OPEN"


def test_enrich_latency_model_from_component_bands() -> None:
    bands = {
        "feed_latency_us": {
            "measurement_status": "MEASURED",
            "source_run_id": "cc3_fixture",
            "distribution_us": {"p99_us": 8739.0},
        },
        "new_send_to_exchange_us": {
            "measurement_status": "MEASURED",
            "source_run_id": "cc3_fixture",
            "distribution_us": {"p99_us": 3595.0},
        },
        "new_exchange_to_ack_us": {
            "measurement_status": "MEASURED",
            "source_run_id": "cc3_fixture",
            "distribution_us": {"p99_us": 1793.0},
        },
    }
    model = enrich_latency_model_from_component_bands(
        {"latency_model_family": "ConstantLatency", "feed_latency_ms": None},
        bands,
        regime="stress",
    )
    assert model["feed_latency_ms"] == pytest.approx(8.739)
    assert model["order_entry_latency_ms"] == pytest.approx(3.595)
    assert model["order_response_latency_ms"] == pytest.approx(1.793)
    assert model["feed_latency_source"] == "cc3_fixture"


def test_resolve_latency_model_stress_regime() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    model = resolve_latency_model(regime="stress", chi404_summary=summary_path)
    assert model["latency_model_family"] == "ConstantLatency"
    assert model["order_entry_latency_ms"] is not None
    assert model["order_response_latency_ms"] is not None
    assert model["order_entry_latency_ms"] + model["order_response_latency_ms"] == pytest.approx(
        model["latency_p99_ms"], rel=1e-6
    )


def test_regime_latency_splits_differ_by_quantile() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    fast = build_latency_model_from_summary(regime="fast", summary=summary)
    normal = build_latency_model_from_summary(regime="normal", summary=summary)
    stress = build_latency_model_from_summary(regime="stress", summary=summary)
    extreme = build_latency_model_from_summary(regime="extreme", summary=summary)
    assert fast["order_entry_latency_ms"] < normal["order_entry_latency_ms"]
    assert normal["order_entry_latency_ms"] < stress["order_entry_latency_ms"]
    assert stress["order_entry_latency_ms"] < extreme["order_entry_latency_ms"]


def test_latency_regime_artifacts_pass_realism_validation() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    regime_dir = _REPO / "reports" / "latency_baselines" / "live_r01_chicago"
    for regime in ("fast", "normal", "stress", "extreme"):
        path = regime_dir / f"latency_model_{regime}.json"
        if not path.is_file():
            pytest.skip(f"missing {path.name}")
        model = json.loads(path.read_text(encoding="utf-8"))
        reasons = validate_hftbacktest_latency_model(model)
        assert reasons == [], f"{path.name}: {reasons}"
        assert model["latency_value_or_sample_hash"].startswith("sha256:")
        assert model["native_latency_probe_artifact_hash"].startswith("sha256:")
        assert model["native_latency_probe_status"] == "provided"


def test_enrich_latency_model_probe_evidence_from_summary() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    model = build_latency_model_from_summary(regime="stress", summary=summary)
    enriched = enrich_latency_model_probe_evidence(model, chi404_summary=summary_path)
    assert enriched["native_latency_probe_artifact"] == "runtime/latency_reports/latency_summary.json"
    assert enriched["native_latency_probe_provenance"] == "hft3_native_cpp_rithmic_latency_probe"
    reasons = validate_hftbacktest_latency_model(enriched)
    assert reasons == []
