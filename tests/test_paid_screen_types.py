"""Tests for the paid-screen typed structures."""
import pytest
import hashlib
import json
from backtest_pipeline.src.paid_screen_types import (
    PaidScreenUnit, WorkerContext, UnitScreeningResult, BatchingKey,
)


class TestPaidScreenUnit:
    def test_from_jsonl_row(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
               "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
               "event_type": "CPI", "thesis": "test thesis"}
        u = PaidScreenUnit.from_jsonl_row(row)
        assert u.unit_id == "u1"
        assert u.model_id == "HYP_5"
        assert u.hyp_id == 5
        assert u.thesis == "test thesis"

    def test_from_jsonl_row_missing_optional_fields(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT"}
        u = PaidScreenUnit.from_jsonl_row(row)
        assert u.hyp_id is None
        assert u.event_type == ""
        assert u.thesis == ""
        assert u.feature_set_id is None
        assert u.research_clock == "scheduled_event"
        assert u.context_set_id == "target_only"
        assert u.declared_context_sets == ("target_only",)
        assert u.ablation_group_id is None
        assert u.negative_control_policy is None

    def test_from_jsonl_row_defaults_declared_context_sets_for_context(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "context_set_id": "target_plus_cross_asset"}
        u = PaidScreenUnit.from_jsonl_row(row)
        assert u.context_set_id == "target_plus_cross_asset"
        assert u.declared_context_sets == ("target_only", "target_plus_cross_asset")

    def test_from_jsonl_row_parses_context_metadata(self):
        row = {
            "unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
            "event_id": "CPI_2024_09_11_TIGHT",
            "research_clock": "context_feature_uplift",
            "allowed_context_set_id_or_null": "target_plus_cross_asset",
            "declared_context_sets": ["target_only", "target_plus_cross_asset"],
            "ablation_group_id": "HYP_5|MES|CPI",
            "negative_control_policy": {"required": True},
        }
        u = PaidScreenUnit.from_jsonl_row(row)
        assert u.research_clock == "context_feature_uplift"
        assert u.context_set_id == "target_plus_cross_asset"
        assert u.declared_context_sets == ("target_only", "target_plus_cross_asset")
        assert u.ablation_group_id == "HYP_5|MES|CPI"
        assert u.negative_control_policy == {"required": True}

    def test_from_jsonl_row_parses_declared_context_sets_csv(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "declared_context_sets": "target_only, target_plus_macro"}
        u = PaidScreenUnit.from_jsonl_row(row)
        assert u.declared_context_sets == ("target_only", "target_plus_macro")

    def test_rejects_unknown_research_clock(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "research_clock": "surprise_clock"}
        with pytest.raises(ValueError, match="research_clock_invalid"):
            PaidScreenUnit.from_jsonl_row(row)

    def test_rejects_unknown_context_set_id(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "context_set_id": "target_plus_magic"}
        with pytest.raises(ValueError, match="context_set_id_invalid"):
            PaidScreenUnit.from_jsonl_row(row)

    def test_rejects_unknown_declared_context_set(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "declared_context_sets": ["target_only", "target_plus_magic"]}
        with pytest.raises(ValueError, match="context_set_id_invalid"):
            PaidScreenUnit.from_jsonl_row(row)

    def test_rejects_declared_context_sets_missing_current_context(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI_2024_09_11_TIGHT",
               "context_set_id": "target_plus_macro",
               "declared_context_sets": ["target_only"]}
        with pytest.raises(ValueError, match="declared_context_sets_missing_context_set_id"):
            PaidScreenUnit.from_jsonl_row(row)

    def test_identity_hash_stable(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
               "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
               "event_type": "CPI"}
        u1 = PaidScreenUnit.from_jsonl_row(row)
        u2 = PaidScreenUnit.from_jsonl_row(row)
        assert u1.identity_hash() == u2.identity_hash()

    def test_identity_hash_preserves_legacy_default_payload(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
               "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
               "event_type": "CPI"}
        u = PaidScreenUnit.from_jsonl_row(row)
        legacy_payload = json.dumps({
            "model_id": "HYP_5",
            "symbol": "MES.v.0",
            "event_id": "CPI_2024_09_11_TIGHT",
            "hyp_id": 5,
            "feature_set_id": None,
        }, sort_keys=True)
        assert u.identity_hash() == hashlib.sha256(legacy_payload.encode()).hexdigest()[:16]

    def test_identity_hash_differs_on_model(self):
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI"}
        row2 = {"unit_id": "u2", "model_id": "HYP_6", "hyp_id": 6,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI"}
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() != u2.identity_hash()

    def test_identity_hash_differs_on_symbol(self):
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI"}
        row2 = {"unit_id": "u2", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "ES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI"}
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() != u2.identity_hash()

    def test_identity_hash_differs_on_event(self):
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI"}
        row2 = {"unit_id": "u2", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "NFP_2024_10_04_TIGHT",
                "event_type": "NFP"}
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() != u2.identity_hash()

    def test_identity_hash_differs_on_research_clock(self):
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI", "research_clock": "scheduled_event"}
        row2 = dict(row1, research_clock="context_feature_uplift")
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() != u2.identity_hash()

    def test_identity_hash_differs_on_context_set_id(self):
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI", "context_set_id": "target_only"}
        row2 = dict(row1, context_set_id="target_plus_cross_asset")
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() != u2.identity_hash()

    def test_thesis_not_used_for_identity(self):
        """Thesis is metadata — different thesis, same model = same identity hash."""
        row1 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI", "thesis": "alpha"}
        row2 = {"unit_id": "u1", "model_id": "HYP_5", "hyp_id": 5,
                "symbol": "MES.v.0", "event_id": "CPI_2024_09_11_TIGHT",
                "event_type": "CPI", "thesis": "beta"}
        u1 = PaidScreenUnit.from_jsonl_row(row1)
        u2 = PaidScreenUnit.from_jsonl_row(row2)
        assert u1.identity_hash() == u2.identity_hash()

    def test_frozen_dataclass(self):
        row = {"unit_id": "u1", "model_id": "HYP_5", "symbol": "MES.v.0",
               "event_id": "CPI", "event_type": "CPI"}
        u = PaidScreenUnit.from_jsonl_row(row)
        with pytest.raises(AttributeError):
            u.model_id = "HYP_6"


class TestWorkerContext:
    def test_creation(self):
        ctx = WorkerContext(
            repo_root="/repo", git_commit="abc123",
            screening_scope="paid-compute",
            vectorbt_engine="rust", vectorbt_version="1.0.0",
            rust_runtime_proof=True,
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        assert ctx.repo_root == "/repo"
        assert ctx.rust_runtime_proof is True

    def test_frozen(self):
        ctx = WorkerContext(
            repo_root="/repo", git_commit="abc123",
            screening_scope="paid-compute",
            vectorbt_engine="rust", vectorbt_version="1.0.0",
            rust_runtime_proof=True,
            events_csv_hash="eh", lake_manifest_hash="lh",
        )
        with pytest.raises(AttributeError):
            ctx.git_commit = "changed"


class TestUnitScreeningResult:
    def test_defaults(self):
        r = UnitScreeningResult(unit_id="u1", status="OK")
        assert r.unit_id == "u1"
        assert r.status == "OK"
        assert r.screening_artifact_path is None
        assert r.error is None
        assert r.elapsed_seconds == 0.0
        assert r.promoted_ids == []
        assert r.rejected_ids == []

    def test_mutable(self):
        r = UnitScreeningResult(unit_id="u1", status="OK")
        r.promoted_ids.append("c1")
        r.elapsed_seconds = 1.5
        assert r.promoted_ids == ["c1"]
        assert r.elapsed_seconds == 1.5


class TestBatchingKey:
    def _make_key(self, **overrides):
        defaults = dict(
            symbol="MES.v.0", event_id="CPI_2024_09_11_TIGHT",
            event_type="CPI", data_manifest_hash="hash1",
            lake_manifest_hash="hash2", events_csv_hash="hash3",
            bar_construction_id="ohlcv_1m", feature_set_id="fs_v1",
            feature_set_hash="fhash", research_clock="scheduled_event",
            context_set_id="target_only",
            split_scheme_id="wf_2018_2024", fees_model_id="cme_fees_v1",
            slippage_model_id="slip_v1", signal_implementation_hash="sighash",
            model_registry_hash="reg_hash",
        )
        defaults.update(overrides)
        return BatchingKey(**defaults)

    def test_equal_keys_batch_together(self):
        k1 = self._make_key()
        k2 = self._make_key()
        assert k1 == k2

    def test_different_symbol_does_not_batch(self):
        k1 = self._make_key(symbol="MES.v.0")
        k2 = self._make_key(symbol="ES.v.0")
        assert k1 != k2

    def test_different_event_id_does_not_batch(self):
        k1 = self._make_key(event_id="CPI_2024_09_11_TIGHT")
        k2 = self._make_key(event_id="NFP_2024_10_04_TIGHT")
        assert k1 != k2

    def test_different_data_hash_does_not_batch(self):
        k1 = self._make_key(data_manifest_hash="hash_a")
        k2 = self._make_key(data_manifest_hash="hash_b")
        assert k1 != k2

    def test_different_research_clock_does_not_batch(self):
        k1 = self._make_key(research_clock="scheduled_event")
        k2 = self._make_key(research_clock="continuous_intraday")
        assert k1 != k2

    def test_different_context_set_does_not_batch(self):
        k1 = self._make_key(context_set_id="target_only")
        k2 = self._make_key(context_set_id="target_plus_cross_asset")
        assert k1 != k2
        assert k1.feature_cache_key() != k2.feature_cache_key()
        assert k1.signal_cache_key("HYP_5") != k2.signal_cache_key("HYP_5")

    def test_different_fees_model_does_not_batch(self):
        k1 = self._make_key(fees_model_id="fees_v1")
        k2 = self._make_key(fees_model_id="fees_v2")
        assert k1 != k2

    def test_different_split_scheme_does_not_batch(self):
        k1 = self._make_key(split_scheme_id="wf_2018_2024")
        k2 = self._make_key(split_scheme_id="wf_2019_2025")
        assert k1 != k2

    def test_different_signal_impl_does_not_batch(self):
        k1 = self._make_key(signal_implementation_hash="impl_v1")
        k2 = self._make_key(signal_implementation_hash="impl_v2")
        assert k1 != k2

    def test_different_feature_set_hash_does_not_batch(self):
        k1 = self._make_key(feature_set_hash="fhash_a")
        k2 = self._make_key(feature_set_hash="fhash_b")
        assert k1 != k2

    def test_different_model_registry_does_not_batch(self):
        k1 = self._make_key(model_registry_hash="reg_v1")
        k2 = self._make_key(model_registry_hash="reg_v2")
        assert k1 != k2

    def test_cache_key_stable(self):
        k1 = self._make_key()
        k2 = self._make_key()
        assert k1.cache_key() == k2.cache_key()

    def test_cache_key_differs_on_data_hash(self):
        k1 = self._make_key(data_manifest_hash="hash_a")
        k2 = self._make_key(data_manifest_hash="hash_b")
        assert k1.cache_key() != k2.cache_key()

    def test_cache_key_differs_on_symbol(self):
        k1 = self._make_key(symbol="MES.v.0")
        k2 = self._make_key(symbol="ES.v.0")
        assert k1.cache_key() != k2.cache_key()

    def test_feature_cache_key_includes_feature_set(self):
        k1 = self._make_key(feature_set_id="fs_v1")
        k2 = self._make_key(feature_set_id="fs_v2")
        assert k1.feature_cache_key() != k2.feature_cache_key()

    def test_feature_cache_key_includes_feature_hash(self):
        k1 = self._make_key(feature_set_hash="fhash_a")
        k2 = self._make_key(feature_set_hash="fhash_b")
        assert k1.feature_cache_key() != k2.feature_cache_key()

    def test_signal_cache_key_includes_model(self):
        k = self._make_key()
        assert k.signal_cache_key("HYP_5") != k.signal_cache_key("HYP_6")

    def test_signal_cache_key_includes_signal_impl(self):
        k1 = self._make_key(signal_implementation_hash="impl_a")
        k2 = self._make_key(signal_implementation_hash="impl_b")
        assert k1.signal_cache_key("HYP_5") != k2.signal_cache_key("HYP_5")

    def test_vbt_result_cache_key_includes_params_and_engine(self):
        k = self._make_key()
        key1 = k.vbt_result_cache_key("HYP_5", "chunk_a", "1.0.0", "rust")
        key2 = k.vbt_result_cache_key("HYP_5", "chunk_b", "1.0.0", "rust")
        key3 = k.vbt_result_cache_key("HYP_5", "chunk_a", "1.0.0", "numba")
        key4 = k.vbt_result_cache_key("HYP_6", "chunk_a", "1.0.0", "rust")
        assert key1 != key2
        assert key1 != key3
        assert key1 != key4

    def test_all_cache_keys_deterministic(self):
        k = self._make_key()
        assert k.cache_key() == k.cache_key()
        assert k.feature_cache_key() == k.feature_cache_key()
        assert k.signal_cache_key("HYP_5") == k.signal_cache_key("HYP_5")
        assert k.vbt_result_cache_key("HYP_5", "c", "1.0", "rust") == k.vbt_result_cache_key("HYP_5", "c", "1.0", "rust")

    def test_frozen(self):
        k = self._make_key()
        with pytest.raises(AttributeError):
            k.symbol = "ES.v.0"

    def test_all_15_fields_present(self):
        """BatchingKey must include all fields that can change semantics."""
        k = self._make_key()
        # The 15 required fields from the spec
        assert hasattr(k, "symbol")
        assert hasattr(k, "event_id")
        assert hasattr(k, "event_type")
        assert hasattr(k, "data_manifest_hash")
        assert hasattr(k, "lake_manifest_hash")
        assert hasattr(k, "events_csv_hash")
        assert hasattr(k, "bar_construction_id")
        assert hasattr(k, "feature_set_id")
        assert hasattr(k, "feature_set_hash")
        assert hasattr(k, "research_clock")
        assert hasattr(k, "context_set_id")
        assert hasattr(k, "split_scheme_id")
        assert hasattr(k, "fees_model_id")
        assert hasattr(k, "slippage_model_id")
        assert hasattr(k, "signal_implementation_hash")
        assert hasattr(k, "model_registry_hash")
