"""Tests for VectorBT feature-plane contract enforcement."""

from __future__ import annotations

import copy

import pytest

from backtest_pipeline.src.feature_plane import (
    FEATURE_PLANE_STATUS_BAR_STUB,
    FEATURE_PLANE_STATUS_FEATURE_COMPLETE,
    build_feature_plane_payload,
    build_feature_usage_manifest,
    compute_feature_usage_manifest_hash,
    feature_plane_validation_errors,
)
from backtest_pipeline.src.vectorbt_adapter import (
    ScreeningArtifactError,
    compute_screening_artifact_hash,
    filter_candidates,
    validate_screening_artifact,
    validate_screening_artifact_or_raise,
)


def _bar_stub_payload(**overrides):
    return build_feature_plane_payload(
        bar_construction_id="ohlcv_1m_from_npz_or_supplied_array",
        feature_set_id="fs_v1_pilot_unknown",
        feature_set_hash="pilot_requires_feature_manifest_before_screen",
        research_clock="scheduled_event",
        screening_scope="pilot",
        overrides=overrides,
    )


def _bar_stub_payload_raw(**overrides):
    payload = build_feature_plane_payload(
        bar_construction_id="ohlcv_1m_from_npz_or_supplied_array",
        feature_set_id="fs_v1_pilot_unknown",
        feature_set_hash="pilot_requires_feature_manifest_before_screen",
        research_clock="scheduled_event",
        screening_scope="pilot",
    )
    payload.update(overrides)
    return payload


class TestFeatureUsageManifest:
    def test_manifest_separates_catalog_eligibility_from_model_consumption(self):
        manifest = build_feature_usage_manifest(
            bar_construction_id="ohlcv_1m_from_npz_or_supplied_array",
            feature_set_id="fs_v1_pilot_unknown",
            feature_set_hash="pilot_requires_feature_manifest_before_screen",
            research_clock="scheduled_event",
            screening_scope="pilot",
        )
        primary = manifest["primary_fs_v1"]
        assert primary["catalog_eligibility"] == "eligible"
        assert primary["model_consumption"] == "not_used"
        assert primary["evidence_scope"] == "catalog_eligibility_not_model_usage"
        assert "bar_ohlcv_stub" in primary["why_not_used_or_sidelined"]

    def test_bar_stub_classifies_honestly(self):
        payload = _bar_stub_payload()
        assert payload["feature_plane_status"] == FEATURE_PLANE_STATUS_BAR_STUB
        assert payload["model_feature_usage_status"] == "not_observed"
        assert payload["full_product_evidence_status"] == "refused"
        assert payload["context_feature_coverage_status"] == "not_measured"
        assert payload["context_ablation_status"] == "not_measured"


class TestFeaturePlaneValidation:
    def test_rejects_invalid_explicit_status_override(self):
        payload = _bar_stub_payload(feature_plane_status="not_a_real_status")
        assert payload["feature_plane_status"] == FEATURE_PLANE_STATUS_BAR_STUB

    def test_rejects_mislabeled_context_coverage(self):
        payload = _bar_stub_payload_raw()
        payload["context_feature_coverage_status"] = "measured"
        errors = feature_plane_validation_errors(payload)
        assert any("mislabeled_full_product" in err for err in errors)

    def test_rejects_pit_declared_without_feature_complete_status(self):
        payload = _bar_stub_payload_raw()
        payload["model_feature_usage_status"] = "pit_declared"
        errors = feature_plane_validation_errors(payload)
        assert "model_feature_usage_pit_declared_without_feature_complete_status" in errors

    def test_manifest_hash_mismatch_fails(self):
        payload = _bar_stub_payload_raw()
        payload["feature_usage_manifest_hash"] = "deadbeef"
        errors = feature_plane_validation_errors(payload)
        assert "feature_usage_manifest_hash_mismatch" in errors

    def test_accepts_feature_complete_when_all_families_consumed(self):
        consumed_manifest = {
            family: {
                "catalog_eligibility": "eligible",
                "model_consumption": "consumed",
                "evidence_scope": "model_consumption_pit_observed",
                "pit_proof_status": "pass",
            }
            for family in (
                "primary_fs_v1",
                "cross_asset_futures",
                "vix_vvix_sensor",
                "vix_options",
                "cme_options_context",
                "macro_context",
                "continuous_session",
                "latency_state",
            )
        }
        payload = build_feature_plane_payload(
            bar_construction_id="fs_v1_row_loop",
            feature_set_id="fs_v1",
            feature_set_hash="sha256:deadbeef",
            research_clock="scheduled_event",
            screening_scope="screen",
            overrides={
                "feature_plane_status": FEATURE_PLANE_STATUS_FEATURE_COMPLETE,
                "feature_usage_manifest": consumed_manifest,
                "model_feature_usage_status": "pit_declared",
                "context_feature_coverage_status": "consumed",
                "context_ablation_status": "consumed",
                "continuous_clock_status": "consumed",
                "cross_asset_alignment_status": "consumed",
                "vix_sensor_status": "consumed",
                "vix_options_status": "consumed",
                "cme_options_context_status": "consumed",
                "latency_feature_status": "consumed",
            },
        )
        assert feature_plane_validation_errors(payload) == []


class TestVectorbtAdapterIntegration:
    def test_filter_candidates_emits_feature_plane_fields(self, tmp_path):
        result = filter_candidates(
            candidates=[],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        )
        artifact = result.to_dict()
        assert artifact["feature_plane_status"] == FEATURE_PLANE_STATUS_BAR_STUB
        assert artifact["model_feature_usage_status"] == "not_observed"
        assert artifact["full_product_evidence_status"] == "refused"
        assert isinstance(artifact["feature_usage_manifest"], dict)
        assert artifact["feature_usage_manifest_hash"] == compute_feature_usage_manifest_hash(
            artifact["feature_usage_manifest"]
        )
        assert validate_screening_artifact(artifact) == []

    def test_validate_rejects_removed_feature_plane_field(self, tmp_path):
        artifact = filter_candidates(
            candidates=[],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()
        broken = copy.deepcopy(artifact)
        broken.pop("feature_plane_status")
        broken["screening_artifact_hash"] = compute_screening_artifact_hash(broken)
        with pytest.raises(ScreeningArtifactError, match="feature_plane_status"):
            validate_screening_artifact_or_raise(broken)

    def test_validate_rejects_forged_full_product_claim_on_bar_stub(self, tmp_path):
        artifact = filter_candidates(
            candidates=[],
            parsed=None,
            event_id="NO_DATA",
            repo_root=tmp_path,
            data_loader=lambda *_: None,
            param_grid={
                "signal_threshold": [0.15],
                "holding_period_bars": [15],
                "stop_loss_pct": [None],
                "take_profit_pct": [None],
            },
        ).to_dict()
        forged = copy.deepcopy(artifact)
        forged["full_product_evidence_status"] = "allowed"
        forged["screening_artifact_hash"] = compute_screening_artifact_hash(forged)
        with pytest.raises(ScreeningArtifactError, match="full_product"):
            validate_screening_artifact_or_raise(forged)
