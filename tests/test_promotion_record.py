"""Phase 11 promotion record tests.

Covers the 27 new spec fields (model_id, candidate_id, experiment_id,
config_hash, run_id, dataset_id, feature_set_id, latency_profile,
execution_assumptions, data_resolution, model_combination,
alpha_components, defensive_components, hybrid_components,
allowed_symbols, allowed_instruments, allowed_order_types,
risk_limits_reference, capital_allocation_reference,
kill_switch_reference, report_path, artifact_path, plus the
decision fields promotion_status, promotion_reason, passed_gates,
failed_gates, quarantined_warnings, and the four metric dicts).
"""
from __future__ import annotations

import json
import multiprocessing
import time
from pathlib import Path

import pytest

from hft3.validation.certification_registry import (
    GENESIS_HASH,
    PromotionRecord,
    PROMOTION_STATUSES,
    audit_log_path,
    list_promotion_models,
    load_latest_promotion,
    load_audit_log,
    save_promotion,
)
from hft3.validation.registry_errors import RegistrySchemaError


def _full_record(**overrides) -> PromotionRecord:
    base = dict(
        registry_id="reg-uuid-1",
        model_id="HYP_5",
        candidate_id="HYP_5__thr=0.6",
        experiment_id="exp-cpi-2024",
        run_id="RUN-2026-06-02T000000Z",
        dataset_id="databento_es_mbo_v1",
        feature_set_id="core_64_v1",
        config_hash="abc1234567890def",
        git_commit="a88321c4629ad22cb5f453cc5f691878d7474d95",
        timestamp="2026-06-02T15:30:00Z",
        promotion_status="PROMOTED",
        promotion_reason="sharpe=1.2>0.5; max_dd=-0.07>-0.10",
        passed_gates=["T0", "T1", "T2", "T3", "T4"],
        failed_gates=[],
        quarantined_warnings=["regime_consistency: borderline"],
        backtest_metrics={"sharpe": 1.2, "max_dd": -0.07, "cagr": 0.18},
        robustness_metrics={"monte_carlo.sharpe_p05": 0.6, "bonferroni_penalty": 1.2},
        walk_forward_metrics={"pearson": 0.4, "spearman": 0.35},
        walk_forward_correlation_metrics={
            "double_wf_pearson": 0.32, "method": "WF1+WF2"
        },
        latency_profile={"decision_to_send_us": 80, "send_to_ack_us": 200},
        execution_assumptions={"fill_model": "queue_position_aware"},
        data_resolution="L3_MBO",
        model_combination={"alpha_ids": ["HYP_5"], "defensive_ids": ["regime_filter"]},
        alpha_components=["HYP_5"],
        defensive_components=["regime_filter"],
        hybrid_components=[],
        allowed_symbols=["ES", "MES"],
        allowed_instruments=["ES", "MES"],
        allowed_order_types=["limit", "post_only"],
        risk_limits_reference="configs/risk/limits.yaml",
        capital_allocation_reference="configs/risk/capital.yaml",
        kill_switch_reference="configs/risk/kill_switch.yaml",
        report_path="artifacts/runs/RUN-1/report.md",
        artifact_path="artifacts/runs/RUN-1/manifest.json",
    )
    base.update(overrides)
    return PromotionRecord(**base)


# ---------- schema round-trip ----------


def test_promotion_record_has_27_spec_fields() -> None:
    """Verify the dataclass carries all 27 spec fields. The exact count is
    not sacred but the spec lists this many; we add a few extras
    (registry_id, model_id, etc.) and assert at least 27 are present."""
    rec = _full_record()
    d = rec.to_dict()
    spec_fields = {
        "registry_id", "model_id", "candidate_id", "experiment_id",
        "run_id", "dataset_id", "feature_set_id", "config_hash",
        "git_commit", "timestamp", "promotion_status", "promotion_reason",
        "passed_gates", "failed_gates", "quarantined_warnings",
        "backtest_metrics", "robustness_metrics", "walk_forward_metrics",
        "walk_forward_correlation_metrics", "latency_profile",
        "execution_assumptions", "data_resolution", "model_combination",
        "alpha_components", "defensive_components", "hybrid_components",
        "allowed_symbols", "allowed_instruments", "allowed_order_types",
        "risk_limits_reference", "capital_allocation_reference",
        "kill_switch_reference", "report_path", "artifact_path",
    }
    missing = spec_fields - d.keys()
    assert not missing, f"missing spec fields: {missing}"
    # Plus: passed_gates and failed_gates may overlap
    assert len(d) >= 30  # 27 spec + a few extras


def test_promotion_record_round_trip() -> None:
    rec = _full_record()
    d = rec.to_dict()
    rec2 = PromotionRecord.from_dict(d)
    assert rec2.model_id == rec.model_id
    assert rec2.backtest_metrics == rec.backtest_metrics
    assert rec2.passed_gates == rec.passed_gates


def test_promotion_record_validation_rejects_bad_status() -> None:
    rec = _full_record(promotion_status="PURPLE")
    with pytest.raises(RegistrySchemaError):
        rec.validate()


def test_promotion_record_validation_rejects_bad_timestamp() -> None:
    rec = _full_record(timestamp="not-a-date")
    with pytest.raises(RegistrySchemaError):
        rec.validate()


def test_promotion_record_validation_rejects_non_hex_config_hash() -> None:
    rec = _full_record(config_hash="not-hex!")
    with pytest.raises(RegistrySchemaError):
        rec.validate()


def test_promotion_status_enum() -> None:
    assert PROMOTION_STATUSES == frozenset({"PROMOTED", "REJECTED", "QUARANTINED"})


# ---------- append + load ----------


def test_save_promotion_appends_to_audit_log(tmp_path: Path) -> None:
    rec = _full_record()
    persisted = save_promotion(rec, tmp_path)
    assert persisted["record_type"] == "promotion"
    assert persisted["record_seq"] == 1
    assert persisted["prev_hash"] == GENESIS_HASH
    # Audit log now exists
    log = audit_log_path(tmp_path)
    assert log.is_file()
    records = load_audit_log(tmp_path)
    assert len(records) == 1
    assert records[0]["model_id"] == "HYP_5"


def test_save_promotion_chains_with_certification(tmp_path: Path) -> None:
    """A certification record and a promotion record share the same
    JSONL log and the chain links them."""
    from hft3.validation.certification_registry import (
        CertificationRecord,
        save_registry,
    )
    save_registry(
        CertificationRecord(latest_certification_status="GREEN"),
        tmp_path,
    )
    save_promotion(_full_record(), tmp_path)
    records = load_audit_log(tmp_path)
    assert len(records) == 2
    # Chain: cert (seq=1, no record_type) -> promotion (seq=2)
    assert "record_type" not in records[0]  # legacy cert record
    assert records[1]["record_type"] == "promotion"
    assert records[1]["prev_hash"] == records[0]["self_hash"]


def test_load_latest_promotion_for_model(tmp_path: Path) -> None:
    save_promotion(_full_record(model_id="HYP_5"), tmp_path)
    save_promotion(
        _full_record(model_id="HYP_1", promotion_status="REJECTED"),
        tmp_path,
    )
    # Another record for HYP_5 (later one wins)
    save_promotion(
        _full_record(
            model_id="HYP_5",
            promotion_status="QUARANTINED",
            promotion_reason="second run: not as good",
        ),
        tmp_path,
    )
    latest_5 = load_latest_promotion("HYP_5", tmp_path)
    assert latest_5 is not None
    assert latest_5.promotion_status == "QUARANTINED"
    latest_1 = load_latest_promotion("HYP_1", tmp_path)
    assert latest_1.promotion_status == "REJECTED"
    # No record for HYP_999
    assert load_latest_promotion("HYP_999", tmp_path) is None


def test_list_promotion_models(tmp_path: Path) -> None:
    save_promotion(_full_record(model_id="HYP_5"), tmp_path)
    save_promotion(_full_record(model_id="HYP_1"), tmp_path)
    save_promotion(_full_record(model_id="HYP_5"), tmp_path)  # dup
    assert list_promotion_models(tmp_path) == ["HYP_1", "HYP_5"]


def test_promotion_does_not_touch_legacy_json(tmp_path: Path) -> None:
    """A promotion record must not write the legacy single-JSON file.
    The legacy file is reserved for `CertificationRecord` only."""
    from hft3.validation.certification_registry import (
        DEFAULT_REGISTRY_REL,
        registry_path,
    )
    save_promotion(_full_record(), tmp_path)
    assert not registry_path(tmp_path).is_file()


def test_save_promotion_validation_failure_no_partial_write(tmp_path: Path) -> None:
    save_promotion(_full_record(model_id="HYP_5"), tmp_path)
    pre = audit_log_path(tmp_path).read_text(encoding="utf-8")
    bad = _full_record(model_id="HYP_5", promotion_status="PURPLE")
    with pytest.raises(RegistrySchemaError):
        save_promotion(bad, tmp_path)
    post = audit_log_path(tmp_path).read_text(encoding="utf-8")
    assert pre == post


# ---------- helpers ----------


def tmp_path_for_test():
    """Local helper to avoid pytest import in top-of-file."""
    import tempfile
    return Path(tempfile.mkdtemp())
