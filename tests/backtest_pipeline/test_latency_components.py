"""Tests for HftBacktest three-component latency helpers."""
from __future__ import annotations

import hashlib
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
    load_cc_summaries,
    merge_component_bands_from_cc_summaries,
    resolve_new_send_to_ack_ms,
)
from backtest_pipeline.src.chi404_latency import (  # noqa: E402
    build_latency_model_from_summary,
    enrich_latency_model_probe_evidence,
    resolve_latency_model,
)
from backtest_pipeline.src.hftbacktest_realism import validate_hftbacktest_latency_model  # noqa: E402


def _band_with_source_provenance(name: str, band: dict) -> dict:
    out = dict(band)
    out["source_artifact"] = f"reports/latency_baselines/{out.get('source_run_id', name)}_summary.json"
    out["source_artifact_hash"] = f"sha256:{'a' * 64}"
    return out


def _measured_band(component: str, p99_us: float, digest: str = "a", count: int = 200) -> dict:
    return {
        "measurement_status": "MEASURED",
        "hftbacktest_component": component,
        "sample_count": count,
        "distribution_us": {"count": count, "p99_us": p99_us},
        "source_run_id": "cc_fixture",
        "source_artifact": "reports/latency_baselines/cc_fixture_summary.json",
        "source_artifact_hash": f"sha256:{digest * 64}",
    }


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


def test_merge_component_bands_from_cc2_cc3_summary(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    cc2 = {
        "run_id": "cc2_feed_fixture",
        "metrics": {
            "feed_latency_us": {"count": 1000, "p50_us": 2400.0, "p99_us": 8700.0},
        },
    }
    (baselines / "cc2_feed_20260618T000001Z_summary.json").write_text(json.dumps(cc2), encoding="utf-8")
    cc3 = {
        "run_id": "cc3_new_decomp_fixture",
        "metrics": {
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
    assert merged["feed_latency_us"]["sample_count"] == 1000
    assert merged["feed_latency_us"]["source_run_id"] == "cc2_feed_fixture"
    assert merged["new_send_to_exchange_us"]["distribution_us"]["p99_us"] == pytest.approx(3600.0)
    assert merged["cancel_exchange_to_ack_us"]["measurement_status"] == "UNMEASURED"
    assert "cc4_cancel_20260618T000001Z" in merged["cancel_exchange_to_ack_us"]["note"]
    assert merged["cancel_exchange_to_ack_us"]["source_artifact"].endswith("cc4_cancel_20260618T000001Z_summary.json")
    assert merged["cancel_exchange_to_ack_us"]["source_artifact_hash"].startswith("sha256:")
    assert merged["cancel_send_to_exchange_us"]["measurement_status"] == "OPEN"
    assert merged["feed_latency_us"]["source_artifact_hash"].startswith("sha256:")


def test_cc3_feed_metric_alone_does_not_measure_feed_latency(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    cc3 = {
        "run_id": "cc3_feed_not_authority",
        "metrics": {
            "feed_latency_us": {"count": 1000, "p50_us": 2400.0, "p99_us": 8700.0},
            "new_send_to_exchange_us": {"count": 200, "p50_us": 2900.0, "p99_us": 3600.0},
            "new_exchange_to_ack_us": {"count": 200, "p50_us": 370.0, "p99_us": 1800.0},
        },
    }
    (baselines / "cc3_new_decomp_20260618T000010Z_summary.json").write_text(json.dumps(cc3), encoding="utf-8")

    merged = merge_component_bands_from_cc_summaries(tmp_path, default_component_bands())

    assert merged["feed_latency_us"]["measurement_status"] == "OPEN"
    assert merged["new_send_to_exchange_us"]["measurement_status"] == "MEASURED"


def test_load_cc_summaries_overwrites_self_asserted_source_hash(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    path = baselines / "cc2_feed_20260618T000011Z_summary.json"
    payload = {
        "run_id": "cc2_feed_fixture",
        "source_artifact": "reports/latency_baselines/stale_summary.json",
        "source_artifact_hash": f"sha256:{'0' * 64}",
        "metrics": {"feed_latency_us": {"count": 1000, "p99_us": 8700.0}},
    }
    raw = json.dumps(payload)
    path.write_text(raw, encoding="utf-8")

    loaded = load_cc_summaries(tmp_path)

    assert loaded["cc2"]["source_artifact"] == "reports/latency_baselines/cc2_feed_20260618T000011Z_summary.json"
    assert loaded["cc2"]["source_artifact_hash"] == f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def test_merge_component_bands_measures_cc4_only_at_minimum(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    cc4 = {
        "run_id": "cc4_cancel_20260618T000002Z",
        "metrics": {
            "cancel_send_to_exchange_us": {"count": 200, "p50_us": 720.0, "p99_us": 1680.0},
            "cancel_exchange_to_ack_us": {"count": 200, "p50_us": 940.0, "p99_us": 2040.0},
        },
    }
    (baselines / "cc4_cancel_20260618T000002Z_summary.json").write_text(json.dumps(cc4), encoding="utf-8")

    merged = merge_component_bands_from_cc_summaries(tmp_path, default_component_bands())

    assert merged["cancel_send_to_exchange_us"]["measurement_status"] == "MEASURED"
    assert merged["cancel_send_to_exchange_us"]["sample_count"] == 200
    assert merged["cancel_send_to_exchange_us"]["source_artifact_hash"].startswith("sha256:")
    assert merged["cancel_exchange_to_ack_us"]["measurement_status"] == "MEASURED"


def test_merge_component_bands_rejects_below_per_band_minimums(tmp_path: Path) -> None:
    baselines = tmp_path / "reports" / "latency_baselines"
    baselines.mkdir(parents=True)
    cc2 = {
        "run_id": "cc2_feed_short",
        "metrics": {"feed_latency_us": {"count": 999, "p99_us": 8700.0}},
    }
    cc3 = {
        "run_id": "cc3_new_short",
        "metrics": {
            "new_send_to_exchange_us": {"count": 199, "p99_us": 3600.0},
            "new_exchange_to_ack_us": {"count": 199, "p99_us": 1800.0},
        },
    }
    cc4 = {
        "run_id": "cc4_cancel_short",
        "metrics": {
            "cancel_send_to_exchange_us": {"count": 199, "p99_us": 1680.0},
            "cancel_exchange_to_ack_us": {"count": 199, "p99_us": 2040.0},
        },
    }
    (baselines / "cc2_feed_20260618T000003Z_summary.json").write_text(json.dumps(cc2), encoding="utf-8")
    (baselines / "cc3_new_decomp_20260618T000003Z_summary.json").write_text(json.dumps(cc3), encoding="utf-8")
    (baselines / "cc4_cancel_20260618T000003Z_summary.json").write_text(json.dumps(cc4), encoding="utf-8")

    merged = merge_component_bands_from_cc_summaries(tmp_path, default_component_bands())

    assert merged["feed_latency_us"]["measurement_status"] == "OPEN"
    assert merged["new_send_to_exchange_us"]["measurement_status"] == "OPEN"
    assert merged["new_exchange_to_ack_us"]["measurement_status"] == "OPEN"
    assert merged["cancel_send_to_exchange_us"]["measurement_status"] == "OPEN"
    assert merged["cancel_exchange_to_ack_us"]["measurement_status"] == "UNMEASURED"


def test_enrich_latency_model_from_component_bands() -> None:
    bands = {
        "feed_latency_us": {
            "measurement_status": "MEASURED",
            "source_run_id": "cc2_fixture",
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
    assert model["feed_latency_source"] == "cc2_fixture"
    assert "latency_proxy_status" not in model


def test_enrich_latency_model_keeps_fake_component_bands_measured_partial() -> None:
    bands = {
        "feed_latency_us": _measured_band("feed", 8739.0, "a", count=1000),
        "new_send_to_exchange_us": _measured_band("order_entry", 3595.0, "b"),
        "new_exchange_to_ack_us": _measured_band("order_response", 1793.0, "c"),
        "cancel_send_to_exchange_us": _measured_band("order_entry", 1680.0, "d"),
        "cancel_exchange_to_ack_us": _measured_band("order_response", 2040.0, "e"),
    }
    model = enrich_latency_model_from_component_bands(
        {"latency_model_family": "ConstantLatency", "feed_latency_ms": None},
        bands,
        regime="stress",
    )

    assert model["latency_proxy_status"] == "measured_partial"
    assert set(model["latency_component_bands"]) == set(bands)
    assert model["latency_component_bands"]["cancel_exchange_to_ack_us"]["source_artifact_hash"].startswith("sha256:")


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


def test_latency_regime_artifacts_need_component_band_evidence_for_realism_validation() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    regime_dir = _REPO / "reports" / "latency_baselines" / "live_r01_chicago"
    for regime in ("fast", "normal", "stress", "extreme"):
        path = regime_dir / f"latency_model_{regime}.json"
        if not path.is_file():
            pytest.skip(f"missing {path.name}")

        model = resolve_latency_model(regime=regime, chi404_summary=summary_path)
        reasons = validate_hftbacktest_latency_model(model)
        assert set(model["latency_component_bands"]) == {
            "feed_latency_us",
            "new_send_to_exchange_us",
            "new_exchange_to_ack_us",
            "cancel_send_to_exchange_us",
            "cancel_exchange_to_ack_us",
        }
        assert model["latency_proxy_status"] == "measured_partial"
        assert "measured_decomposed_requires_component_band_evidence" not in reasons, f"{path.name}: {reasons}"
        assert reasons == ["measured_partial_latency_cannot_certify"], f"{path.name}: {reasons}"
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
    assert enriched["latency_proxy_status"] == "measured_partial"
    assert reasons == ["measured_partial_latency_cannot_certify"]


def test_probe_evidence_does_not_certify_fake_component_band_sources() -> None:
    summary_path = _REPO / "runtime" / "latency_reports" / "latency_summary.json"
    if not summary_path.is_file():
        pytest.skip("latency_summary.json missing")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    model = build_latency_model_from_summary(regime="stress", summary=summary)
    model["latency_component_bands"] = {
        "feed_latency_us": _measured_band("feed", 8739.0, "a", count=1000),
        "new_send_to_exchange_us": _measured_band("order_entry", 3595.0, "b", count=200),
        "new_exchange_to_ack_us": _measured_band("order_response", 1793.0, "c", count=200),
        "cancel_send_to_exchange_us": _measured_band("order_entry", 1680.0, "d", count=200),
        "cancel_exchange_to_ack_us": _measured_band("order_response", 2040.0, "e", count=200),
    }

    enriched = enrich_latency_model_probe_evidence(model, chi404_summary=summary_path)
    reasons = validate_hftbacktest_latency_model(enriched, repo_root=_REPO)

    assert enriched["latency_proxy_status"] == "measured_partial"
    assert reasons == ["measured_partial_latency_cannot_certify"]
