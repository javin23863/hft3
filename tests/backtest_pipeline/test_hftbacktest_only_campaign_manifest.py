from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from pathlib import Path

import pytest

import backtest_pipeline.src.hftbacktest_only_campaign_manifest as manifest_module
from backtest_pipeline.src.hftbacktest_only_campaign_manifest import (
    REQUIRED_ROW_FIELDS,
    REQUIRED_PARAMETER_SURFACE_ROW_FIELDS,
    HftBacktestOnlyCampaignManifestError,
    build_campaign_manifest_rows,
    build_parameter_surface_rows,
    campaign_manifest_summary,
    iter_parameter_surface_rows,
    parameter_surface_summary,
    stream_campaign_manifest,
    stream_first_eligible_canary_manifest,
    stream_parameter_surface_manifest,
    validate_campaign_manifest_rows,
    validate_parameter_surface_rows,
    write_campaign_manifest,
    write_parameter_surface_manifest,
)
from features_engine.src.model_registry import resolve_model_id


_TEST_ADAPTERS_AVAILABLE = {
    "SPREAD_BLOWOUT_RECOMPRESSION": "available",
    "QUEUE_TOXICITY_HAWKES": "available",
}


def _write_registry(path: Path) -> Path:
    path.write_text(
        """
models:
  SPREAD_BLOWOUT_RECOMPRESSION:
    kind: hypothesis
    legacy_id: HYP_5
    display_name: Spread blowout/recompression
    class: SpreadBlowoutRecompression
  QUEUE_TOXICITY_HAWKES:
    kind: pdf_structural
    legacy_id: PDF_MODEL_11
    display_name: Queue toxicity Hawkes
    class: HawkesToxicFlowModel
  RL_EXECUTION_POLICY:
    kind: reinforcement_learning
    legacy_id: RL_EXECUTION
    display_name: Reinforcement Learning Execution Policy
    class: RLExecutionPolicyArtifact
    rl_status: offline_rl_research_only
    promotion_status: blocked_downstream_validation_required
""".lstrip(),
        encoding="utf-8",
    )
    return path


def _write_prepared_unit(root: Path) -> Path:
    prepared_dir = root / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized")
    snapshot.write_bytes(b"hbt-initial-snapshot")
    manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "symbol": "MES",
        "contract": "MESU4",
        "event_id": "CPI_2024_09_11_TIGHT",
        "trade_date": "2024-09-11",
        "start_ts_ns": 1,
        "cutoff_ts_ns": 2,
        "end_ts_ns": 3,
        "normalized_npz": str(normalized),
        "initial_snapshot": str(snapshot),
        "tick_size": 0.25,
        "lot_size": 1.0,
        "contract_size": 5.0,
        "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
        "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
        "authority_refs": [
            "config/hftbacktest/cme_lake_product_metadata.yaml",
            "product-authority:MES",
        ],
    }
    manifest_path = prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _write_second_prepared_unit(root: Path) -> Path:
    prepared_dir = root / "MES" / "2024-09-12"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized-second")
    snapshot.write_bytes(b"hbt-initial-snapshot-second")
    manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "symbol": "MES",
        "contract": "MESU4",
        "event_id": "PPI_2024_09_12_TIGHT",
        "trade_date": "2024-09-12",
        "start_ts_ns": 4,
        "cutoff_ts_ns": 5,
        "end_ts_ns": 6,
        "normalized_npz": str(normalized),
        "initial_snapshot": str(snapshot),
        "tick_size": 0.25,
        "lot_size": 1.0,
        "contract_size": 5.0,
        "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
        "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
        "authority_refs": [
            "config/hftbacktest/cme_lake_product_metadata.yaml",
            "product-authority:MES",
        ],
    }
    manifest_path = prepared_dir / "PPI_2024_09_12_TIGHT_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _write_prepared_unit_with_bytes(
    root: Path,
    *,
    normalized_bytes: bytes = b"hbt-normalized",
    snapshot_bytes: bytes = b"hbt-initial-snapshot",
) -> Path:
    prepared_dir = root / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(normalized_bytes)
    snapshot.write_bytes(snapshot_bytes)
    manifest = {
        "schema_version": "hft3_hbt_only_lake_prepare_v1",
        "symbol": "MES",
        "contract": "MESU4",
        "event_id": "CPI_2024_09_11_TIGHT",
        "trade_date": "2024-09-11",
        "normalized_npz": str(normalized),
        "initial_snapshot": str(snapshot),
        "tick_size": 0.25,
        "lot_size": 1.0,
        "contract_size": 5.0,
        "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
        "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
        "authority_refs": [
            "config/hftbacktest/cme_lake_product_metadata.yaml",
            "product-authority:MES",
        ],
    }
    manifest_path = prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest_path


def _parameter_set(
    parameter_family: str,
    strategy_params: dict[str, object],
    **overrides: object,
) -> dict[str, object]:
    spec: dict[str, object] = {
        "schema_version": manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_SCHEMA_VERSION,
        "source": manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_SOURCE,
        "authority_refs": list(manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_AUTHORITY_REFS),
        "parameter_family": parameter_family,
        "strategy_params": strategy_params,
    }
    spec.update(overrides)
    return spec


def _parameter_sets() -> list[dict[str, object]]:
    return [
        _parameter_set(
            "grid",
            {"entry_threshold": 0.25, "max_position": 1},
        ),
        _parameter_set(
            "bayesian-prior",
            {"entry_threshold": 0.5, "max_position": 1},
            objective_evaluations=0,
            optimizer_claim=False,
        ),
        _parameter_set(
            "evolutionary-prior",
            {"entry_threshold": 0.75, "max_position": 2},
        ),
    ]


def _parameter_sets_payload(parameter_sets: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_SCHEMA_VERSION,
        "source": manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_SOURCE,
        "authority_refs": list(manifest_module.HBT_SELF_LEARNING_PARAMETER_SET_AUTHORITY_REFS),
        "parameter_proposal_status": "declared_pre_hbt",
        "objective_evaluations": 0,
        "optimizer_claim": False,
        "parameter_set_count": len(parameter_sets),
        "parameter_sets": parameter_sets,
    }


def test_self_learning_parameter_export_requires_envelope_authority_refs() -> None:
    payload = _parameter_sets_payload(_parameter_sets()[:1])
    payload["authority_refs"] = []

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_missing_self_learning_authority_refs",
    ):
        manifest_module.normalize_self_learning_parameter_sets_payload(payload)


def test_self_learning_parameter_export_requires_pre_hbt_envelope_status() -> None:
    payload = _parameter_sets_payload(_parameter_sets()[:1])
    payload["parameter_proposal_status"] = "optimized_after_hbt"

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_invalid_self_learning_proposal_status",
    ):
        manifest_module.normalize_self_learning_parameter_sets_payload(payload)


def test_self_learning_parameter_export_requires_zero_envelope_objective_evaluations() -> None:
    payload = _parameter_sets_payload(_parameter_sets()[:1])
    payload["objective_evaluations"] = 1

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_pre_hbt_objective_evaluations_must_be_zero",
    ):
        manifest_module.normalize_self_learning_parameter_sets_payload(payload)


def test_self_learning_parameter_export_rejects_envelope_optimizer_claim() -> None:
    payload = _parameter_sets_payload(_parameter_sets()[:1])
    payload["optimizer_claim"] = True

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_self_learning_optimizer_claim_must_be_false",
    ):
        manifest_module.normalize_self_learning_parameter_sets_payload(payload)


def test_self_learning_parameter_export_requires_matching_parameter_set_count() -> None:
    payload = _parameter_sets_payload(_parameter_sets()[:1])
    payload["parameter_set_count"] = 2

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_self_learning_parameter_set_count_mismatch",
    ):
        manifest_module.normalize_self_learning_parameter_sets_payload(payload)


def test_canonical_slug_resolution_accepts_descriptive_slugs() -> None:
    assert resolve_model_id("SPREAD_BLOWOUT_RECOMPRESSION") == "SPREAD_BLOWOUT_RECOMPRESSION"


def test_cli_campaign_input_rejects_legacy_model_ids() -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "run_hftbacktest_only.py"
    spec = importlib.util.spec_from_file_location("run_hftbacktest_only_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    assert module._canonical_model_identity("SPREAD_BLOWOUT_RECOMPRESSION") == (
        "SPREAD_BLOWOUT_RECOMPRESSION",
        ("HYP_5",),
    )
    with pytest.raises(SystemExit, match="canonical descriptive slug"):
        module._canonical_model_identity("HYP_5")
    with pytest.raises(SystemExit, match="required"):
        module._canonical_model_identity(None)


def test_manifest_uses_canonical_slugs_and_legacy_aliases_only(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert [row["canonical_model_id"] for row in rows] == [
        "SPREAD_BLOWOUT_RECOMPRESSION",
        "QUEUE_TOXICITY_HAWKES",
        "RL_EXECUTION_POLICY",
    ]
    assert rows[0]["legacy_aliases"] == ["HYP_5"]
    assert rows[1]["legacy_aliases"] == ["PDF_MODEL_11"]
    assert rows[2]["legacy_aliases"] == ["RL_EXECUTION"]
    assert all(field in rows[0] for field in REQUIRED_ROW_FIELDS)
    assert rows[0]["admissibility_status"] == "admissible"
    assert rows[0]["blocker_code"] == ""
    assert rows[0]["source_npz_sha256"] == hashlib.sha256(b"hbt-normalized").hexdigest()
    assert rows[0]["tick_size"] == 0.25
    assert rows[0]["lot_size"] == 1.0
    assert rows[0]["contract_size"] == 5.0
    assert rows[0]["product_metadata_source"] == "config/hftbacktest/cme_lake_product_metadata.yaml"
    assert "product-authority:MES" in rows[0]["authority_refs"]
    assert "HYP_5" not in {row["canonical_model_id"] for row in rows}
    assert "PDF_MODEL_11" not in {row["canonical_model_id"] for row in rows}

    summary = campaign_manifest_summary(rows)
    assert summary["canonical_model_count"] == 3
    assert summary["source_npz_count"] == 1
    assert summary["manifest_order"] == "registry_file_order_then_prepared_manifest_path"
    assert "expected_base_rows" not in summary
    assert "hbt_jobs_started" not in summary


def test_stream_campaign_manifest_writes_pre_execution_summary_and_checkpoint(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    summary = stream_campaign_manifest(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
        out_path=tmp_path / "campaign_manifest.jsonl",
        summary_path=tmp_path / "campaign_pre_execution_summary.json",
        checkpoint_path=tmp_path / "campaign_manifest.checkpoint.json",
        checkpoint_every_rows=2,
        parameter_surface_config_status="pipeline_blocker:parameter_sets_config_missing",
        parameter_sets_json=tmp_path / "parameter_sets.json",
    )

    assert summary["canonical_model_count"] == 3
    assert summary["prepared_unit_count"] == 1
    assert summary["executable_unit_count"] == 1
    assert summary["blocker_unit_count"] == 0
    assert summary["expected_base_rows"] == 3
    assert summary["emitted_base_rows"] == 3
    assert summary["row_count_matches_expected"] is True
    assert summary["adapter_status_counts"] == {
        "available": 2,
        "missing_uniform_hbt_adapter": 1,
    }
    assert summary["model_applicability_counts"] == {
        "adapter_status:available": 2,
        "adapter_status:missing_uniform_hbt_adapter": 1,
    }
    assert summary["instrument_applicability_counts"] == {
        "hbt_executable_prepared_unit": 1,
    }
    assert summary["manual_filter_used"] is False
    assert summary["vectorbt_dependency"] is False
    assert summary["stage_a_dependency"] is False
    assert summary["screening_artifact_dependency"] is False
    assert summary["hbt_jobs_started"] == 0
    assert summary["parameter_surface_config_status"] == (
        "pipeline_blocker:parameter_sets_config_missing"
    )
    assert (tmp_path / "campaign_manifest.jsonl").read_text(encoding="utf-8").count("\n") == 3
    checkpoint = json.loads(
        (tmp_path / "campaign_manifest.checkpoint.json").read_text(encoding="utf-8")
    )
    assert checkpoint["partial"] is False
    assert checkpoint["emitted_base_rows"] == 3


def test_stream_campaign_manifest_fails_closed_on_row_count_mismatch(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    _write_second_prepared_unit(prepared_root)
    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    one_prepared_for_each_model = [rows[0], rows[2], rows[4]]

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="campaign_manifest_row_count_mismatch",
    ):
        write_campaign_manifest(
            one_prepared_for_each_model,
            out_path=tmp_path / "campaign_manifest.jsonl",
            expected_canonical_model_ids=[
                "SPREAD_BLOWOUT_RECOMPRESSION",
                "QUEUE_TOXICITY_HAWKES",
                "RL_EXECUTION_POLICY",
            ],
            prepared_unit_count=2,
        )
    assert not (tmp_path / "campaign_manifest.jsonl").exists()
    assert not (tmp_path / "campaign_manifest.jsonl.tmp").exists()
    assert not (tmp_path / "campaign_manifest.jsonl.checkpoint.json").exists()


def test_first_eligible_canary_uses_manifest_order_without_manual_filter(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    base_path = tmp_path / "campaign_manifest.jsonl"
    stream_campaign_manifest(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
        out_path=base_path,
        parameter_surface_config_status="pipeline_blocker:parameter_sets_config_missing",
    )
    assert not (tmp_path / "campaign_manifest.jsonl.checkpoint.json").exists()

    summary = stream_first_eligible_canary_manifest(
        (json.loads(line) for line in base_path.read_text(encoding="utf-8").splitlines()),
        out_path=tmp_path / "canary_manifest.jsonl",
        count=2,
        summary_path=tmp_path / "canary_summary.json",
        source_manifest=base_path,
        parameter_surface_status="base_only",
        parameter_surface_config_status="pipeline_blocker:parameter_sets_config_missing",
    )

    canary_rows = [
        json.loads(line)
        for line in (tmp_path / "canary_manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [row["canonical_model_id"] for row in canary_rows] == [
        "SPREAD_BLOWOUT_RECOMPRESSION",
        "QUEUE_TOXICITY_HAWKES",
    ]
    assert summary["selector"] == "first_n_manifest_order_after_readiness_predicates"
    assert summary["manual_filter_used"] is False
    assert summary["hbt_jobs_started"] == 0
    assert summary["eligibility_counts"]["eligible"] == 2


def test_manifest_omissions_fail_closed(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="campaign_manifest_missing_canonical_model_ids:",
    ):
        validate_campaign_manifest_rows(
            rows[:1],
            expected_canonical_model_ids=[
                "SPREAD_BLOWOUT_RECOMPRESSION",
                "QUEUE_TOXICITY_HAWKES",
                "RL_EXECUTION_POLICY",
            ],
        )


def test_adapter_failure_is_pipeline_blocker_not_model_rejection(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model={
            "SPREAD_BLOWOUT_RECOMPRESSION": "available",
            "QUEUE_TOXICITY_HAWKES": "missing_uniform_hbt_adapter",
        },
    )

    blocked = next(row for row in rows if row["canonical_model_id"] == "QUEUE_TOXICITY_HAWKES")
    assert blocked["admissibility_status"] == "pipeline_blocker"
    assert blocked["blocker_code"] == "pipeline_blocker:missing_uniform_hbt_adapter"
    assert "model_" + "rejected" not in json.dumps(blocked)
    assert "model_" + "untradable" not in json.dumps(blocked)


def test_prepared_blocker_manifest_enters_campaign_not_model_rejection(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_dir = tmp_path / "prepared" / "NQ" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    source = prepared_dir / "source_lake.npz"
    source.write_bytes(b"source-lake")
    source_hash = hashlib.sha256(b"source-lake").hexdigest()
    (prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_only_lake_prepare_v1",
                "status": "blocked",
                "symbol": "NQ",
                "contract": "NQU4",
                "event_id": "CPI_2024_09_11_TIGHT",
                "trade_date": "2024-09-11",
                "source_npz": str(source),
                "source_npz_sha256": source_hash,
                "normalized_npz": "",
                "initial_snapshot": "",
                "blocker_code": "authority_missing",
                "blocker_detail": "instrument_metadata_missing:contract_size",
            }
        ),
        encoding="utf-8",
    )

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "prepared",
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert len(rows) == 3
    assert {row["source_npz"] for row in rows} == {str(source)}
    assert {row["source_npz_sha256"] for row in rows} == {source_hash}
    assert {row["admissibility_status"] for row in rows} == {"authority_missing"}
    assert {row["blocker_code"] for row in rows} == {"authority_missing"}
    assert all("instrument_metadata_missing:contract_size" in row["blocker_detail"] for row in rows)
    assert all("product_metadata_source" in row["blocker_detail"] for row in rows)
    assert all("metadata_policy" in row["blocker_detail"] for row in rows)
    assert all("product_authority_refs" in row["blocker_detail"] for row in rows)
    assert "model_" + "rejected" not in json.dumps(rows)


def test_missing_product_metadata_authority_blocks_prepared_unit(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_dir = tmp_path / "prepared" / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized")
    snapshot.write_bytes(b"hbt-initial-snapshot")
    (prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_only_lake_prepare_v1",
                "symbol": "MES",
                "contract": "MESU4",
                "event_id": "CPI_2024_09_11_TIGHT",
                "trade_date": "2024-09-11",
                "normalized_npz": str(normalized),
                "initial_snapshot": str(snapshot),
                "tick_size": 0.25,
                "lot_size": 1.0,
                "contract_size": 5.0,
            }
        ),
        encoding="utf-8",
    )

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "prepared",
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert {row["admissibility_status"] for row in rows} == {"authority_missing"}
    assert {row["blocker_code"] for row in rows} == {"authority_missing"}
    assert all("product_metadata_source" in row["blocker_detail"] for row in rows)
    assert all("metadata_policy" in row["blocker_detail"] for row in rows)
    assert all("product_authority_refs" in row["blocker_detail"] for row in rows)


def test_whitespace_product_authority_ref_is_missing_authority(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_dir = tmp_path / "prepared" / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized")
    snapshot.write_bytes(b"hbt-initial-snapshot")
    (prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_only_lake_prepare_v1",
                "symbol": "MES",
                "contract": "MESU4",
                "event_id": "CPI_2024_09_11_TIGHT",
                "trade_date": "2024-09-11",
                "normalized_npz": str(normalized),
                "initial_snapshot": str(snapshot),
                "tick_size": 0.25,
                "lot_size": 1.0,
                "contract_size": 5.0,
                "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
                "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
                "authority_refs": " ",
            }
        ),
        encoding="utf-8",
    )

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "prepared",
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert {row["blocker_code"] for row in rows} == {"authority_missing"}
    assert all("product_authority_refs" in row["blocker_detail"] for row in rows)
    assert all(" " not in row["authority_refs"] for row in rows)


def test_invalid_adapter_status_override_fails_closed(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="campaign_manifest_invalid_adapter_status",
    ):
        build_campaign_manifest_rows(
            campaign_id="hbt_campaign_test",
            prepared_root=prepared_root,
            registry_path=registry,
            adapter_status_by_model={
                "SPREAD_BLOWOUT_RECOMPRESSION": "available",
                "QUEUE_TOXICITY_HAWKES": "available-ish",
            },
        )


def test_reinforcement_learning_registry_entries_are_pipeline_blockers_until_adapter_exists(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    blocked = next(row for row in rows if row["canonical_model_id"] == "RL_EXECUTION_POLICY")
    assert blocked["legacy_aliases"] == ["RL_EXECUTION"]
    assert blocked["admissibility_status"] == "pipeline_blocker"
    assert blocked["blocker_code"] == "pipeline_blocker:missing_uniform_hbt_adapter"
    assert "model_" + "rejected" not in json.dumps(blocked)
    assert "model_" + "untradable" not in json.dumps(blocked)


def test_manifest_infers_adapter_status_from_uniform_hbt_order_adapter(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "model_registry.yaml"
    registry.write_text(
        """
models:
  BOOK_PRESSURE:
    kind: pdf_structural
    legacy_id: PDF_MODEL_1
    display_name: Limit Order Book Pressure
    class: BookPressureModel
  VPIN_TOXICITY:
    kind: pdf_structural
    legacy_id: PDF_MODEL_3
    display_name: VPIN Flow Toxicity
    class: VPINToxicityModel
""".lstrip(),
        encoding="utf-8",
    )
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
    )

    assert len(rows) == 2
    assert rows[0]["canonical_model_id"] == "BOOK_PRESSURE"
    assert rows[0]["adapter_status"] == "available"
    assert rows[0]["admissibility_status"] == "admissible"
    assert rows[0]["blocker_code"] == ""
    assert rows[1]["canonical_model_id"] == "VPIN_TOXICITY"
    assert rows[1]["adapter_status"] == "missing_uniform_hbt_adapter"
    assert rows[1]["admissibility_status"] == "pipeline_blocker"
    assert rows[1]["blocker_code"] == "pipeline_blocker:missing_uniform_hbt_adapter"


def test_unit_id_uses_source_hash_not_absolute_source_path(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    left_root = tmp_path / "left" / "prepared"
    right_root = tmp_path / "right" / "prepared"
    _write_prepared_unit_with_bytes(left_root)
    _write_prepared_unit_with_bytes(right_root)

    left_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=left_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    right_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=right_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert left_rows[0]["source_npz"] != right_rows[0]["source_npz"]
    assert left_rows[0]["source_npz_sha256"] == right_rows[0]["source_npz_sha256"]
    assert left_rows[0]["unit_id"] == right_rows[0]["unit_id"]


def test_unit_id_changes_when_source_hash_changes_at_same_path(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    manifest_path = _write_prepared_unit_with_bytes(prepared_root)
    first_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    Path(manifest["normalized_npz"]).write_bytes(b"hbt-normalized-changed")
    second_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert first_rows[0]["source_npz"] == second_rows[0]["source_npz"]
    assert first_rows[0]["source_npz_sha256"] != second_rows[0]["source_npz_sha256"]
    assert first_rows[0]["unit_id"] != second_rows[0]["unit_id"]


def test_unit_id_is_stable_across_campaign_ids(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit_with_bytes(prepared_root)

    first_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_first",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    second_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_second",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert first_rows[0]["campaign_id"] != second_rows[0]["campaign_id"]
    assert first_rows[0]["unit_id"] == second_rows[0]["unit_id"]


def test_smoke_manifest_missing_contract_is_authority_blocker(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_dir = tmp_path / "prepared" / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    normalized = prepared_dir / "event_l3.npz"
    snapshot = prepared_dir / "initial_snapshot.npz"
    normalized.write_bytes(b"hbt-normalized")
    snapshot.write_bytes(b"hbt-initial-snapshot")
    (prepared_dir / "CPI_2024_09_11_TIGHT_warmup_30s_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_prepared_smoke_v1",
                "normalized_npz": str(normalized),
                "initial_snapshot": str(snapshot),
            }
        ),
        encoding="utf-8",
    )

    rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "prepared",
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    assert rows[0]["symbol"] == "MES"
    assert rows[0]["event_id"] == "CPI_2024_09_11_TIGHT"
    assert rows[0]["contract"] == ""
    assert rows[0]["admissibility_status"] == "authority_missing"
    assert rows[0]["blocker_code"] == "authority_missing"
    assert "contract" in rows[0]["blocker_detail"]


def test_active_hbt_path_does_not_use_legacy_eligibility_selectors() -> None:
    repo = Path(__file__).resolve().parents[2]
    active_paths = [
        repo / "packages" / "backtest_pipeline" / "src" / "hftbacktest_only_pipeline.py",
        repo / "packages" / "backtest_pipeline" / "src" / "hftbacktest_only_campaign_manifest.py",
        repo / "scripts" / "run_hftbacktest_only.py",
        repo / "scripts" / "run_hftbacktest_only_campaign.py",
        repo / "scripts" / "build_hftbacktest_only_campaign_manifest.py",
        repo / "scripts" / "prepare_hftbacktest_only_from_lake_manifest.py",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in active_paths)

    assert re.search(r"^\s*(from|import)\s+.*vectorbt", source, re.IGNORECASE | re.MULTILINE) is None
    assert "stage_a_survivors" not in source
    assert "screening_artifact.json" not in source
    assert "kind:hypothesis" not in source
    assert "kind:pdf_structural" not in source

def test_parameter_surface_expands_campaign_rows_by_parameter_hash(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets(),
    )
    surface_rows_again = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets(),
    )

    assert surface_rows == surface_rows_again
    assert len(surface_rows) == len(campaign_rows) * 3
    assert all(field in surface_rows[0] for field in REQUIRED_PARAMETER_SURFACE_ROW_FIELDS)
    assert surface_rows[0]["canonical_model_id"] == campaign_rows[0]["canonical_model_id"]
    assert surface_rows[0]["unit_id"] == campaign_rows[0]["unit_id"]
    assert surface_rows[0]["surface_unit_id"].startswith(campaign_rows[0]["unit_id"])
    assert surface_rows[0]["parameter_family"] == "grid"
    assert len(surface_rows[0]["parameter_hash"]) == 64
    assert surface_rows[0]["source_candidate_id"] == ""
    assert surface_rows[0]["parameter_proposal_status"] == "declared_pre_hbt"
    assert surface_rows[0]["objective_evaluations"] == 0
    assert surface_rows[0]["optimizer_claim"] is False
    assert surface_rows[0]["tick_size"] == campaign_rows[0]["tick_size"]
    assert surface_rows[0]["lot_size"] == campaign_rows[0]["lot_size"]
    assert surface_rows[0]["contract_size"] == campaign_rows[0]["contract_size"]
    assert surface_rows[0]["product_metadata_source"] == campaign_rows[0]["product_metadata_source"]
    assert "product-authority:MES" in surface_rows[0]["authority_refs"]
    assert surface_rows[0]["recorder_result_path"] == ""
    assert surface_rows[0]["stats_summary_path"] == ""
    assert surface_rows[0]["promotion_decision_path"] == ""

    blocked_rl_rows = [
        row
        for row in surface_rows
        if row["canonical_model_id"] == "RL_EXECUTION_POLICY"
    ]
    assert len(blocked_rl_rows) == 3
    assert {row["blocker_code"] for row in blocked_rl_rows} == {
        "pipeline_blocker:missing_uniform_hbt_adapter"
    }
    assert "model_" + "rejected" not in json.dumps(blocked_rl_rows)

    summary = parameter_surface_summary(surface_rows)
    assert summary["row_count"] == 9
    assert summary["campaign_unit_count"] == 3
    assert summary["parameter_hash_count"] == 3
    assert summary["parameter_family_counts"] == {
        "bayesian-prior": 3,
        "evolutionary-prior": 3,
        "grid": 3,
    }
    assert summary["objective_evaluation_counts"] == {"0": 9}


def test_parameter_surface_preserves_source_candidate_id(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    parameter_sets = manifest_module.normalize_self_learning_parameter_sets_payload(
        _parameter_sets_payload(
            [
                _parameter_set(
                    "grid",
                    {"entry_threshold": 0.25, "max_position": 1},
                    source_candidate_id="candidate-grid-001",
                )
            ]
        )
    )

    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=parameter_sets,
    )

    assert surface_rows
    assert {row["source_candidate_id"] for row in surface_rows} == {
        "candidate-grid-001"
    }


def test_parameter_hash_canonicalizes_strategy_params(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    first = build_parameter_surface_rows(
        campaign_rows=campaign_rows[:1],
        parameter_sets=[
            _parameter_set("grid", {"b": 2, "a": 1}),
        ],
    )
    second = build_parameter_surface_rows(
        campaign_rows=campaign_rows[:1],
        parameter_sets=[
            _parameter_set("grid", {"a": 1, "b": 2}),
        ],
    )

    assert first[0]["parameter_hash"] == second[0]["parameter_hash"]


def test_parameter_hash_includes_parameter_family(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows[:1],
        parameter_sets=[
            _parameter_set("grid", {"entry_threshold": 0.5}),
            _parameter_set("bayesian-prior", {"entry_threshold": 0.5}),
        ],
    )

    assert surface_rows[0]["parameter_hash"] != surface_rows[1]["parameter_hash"]


def test_model_scoped_parameter_sets_do_not_omit_ready_models(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    parameter_sets = [
        _parameter_set(
            "grid",
            {"signal_threshold": 0.15},
            canonical_model_id="SPREAD_BLOWOUT_RECOMPRESSION",
        )
    ]

    spread_only = [
        row
        for row in campaign_rows
        if row["canonical_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    ]
    surface_rows = build_parameter_surface_rows(
        campaign_rows=spread_only,
        parameter_sets=parameter_sets,
    )
    assert len(surface_rows) == 1
    assert surface_rows[0]["canonical_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert "packages/research_pipeline/parameter_search.py" in surface_rows[0]["authority_refs"]

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="parameter_surface_missing_for_executable_model:QUEUE_TOXICITY_HAWKES",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=parameter_sets,
        )


def test_parameter_surface_rejects_unknown_parameter_family(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="unknown_parameter_family",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=[
                _parameter_set("adaptive-optimizer", {"entry_threshold": 0.5})
            ],
        )


def test_pre_hbt_surface_requires_zero_objective_evaluations(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="pre_hbt_objective_evaluations_must_be_zero",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=[
                _parameter_set(
                    "evolutionary-prior",
                    {"entry_threshold": 0.5},
                    objective_evaluations=1,
                )
            ],
        )


def test_pre_hbt_surface_rejects_fractional_objective_evaluations(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="pre_hbt_objective_evaluations_must_be_zero",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=[
                _parameter_set(
                    "grid",
                    {"entry_threshold": 0.5},
                    objective_evaluations=0.5,
                )
            ],
        )


def test_parameter_hash_rejects_non_finite_strategy_params(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="strategy_params_not_canonical_json",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=[
                _parameter_set("grid", {"entry_threshold": float("nan")})
            ],
        )


def test_parameter_surface_rejects_duplicate_unit_parameter_hash(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="duplicate_unit_parameter_hash",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows[:1],
            parameter_sets=[
                _parameter_set("grid", {"entry_threshold": 0.5}),
                _parameter_set("grid", {"entry_threshold": 0.5}),
            ],
        )


def test_stream_parameter_surface_cleans_tmp_on_duplicate(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets()[:1],
    )
    duplicate_rows = [*surface_rows, dict(surface_rows[0])]
    out_path = tmp_path / "parameter_surface.jsonl"
    checkpoint_path = tmp_path / "parameter_surface.checkpoint.json"

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="duplicate_unit_parameter_hash",
    ):
        stream_parameter_surface_manifest(
            duplicate_rows,
            out_path=out_path,
            checkpoint_path=checkpoint_path,
            checkpoint_every_rows=1,
        )
    assert not out_path.exists()
    assert not (tmp_path / "parameter_surface.jsonl.tmp").exists()
    assert not checkpoint_path.exists()


def test_stream_parameter_surface_keeps_output_on_final_checkpoint_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets()[:1],
    )
    out_path = tmp_path / "parameter_surface.jsonl"
    checkpoint_path = tmp_path / "parameter_surface.checkpoint.json"
    original_write_json_atomic = manifest_module._write_json_atomic

    def fail_final_checkpoint(path: Path, payload: dict[str, object]) -> None:
        if Path(path) == checkpoint_path and payload.get("partial") is False:
            raise RuntimeError("final checkpoint write failed")
        original_write_json_atomic(path, payload)

    monkeypatch.setattr(
        manifest_module,
        "_write_json_atomic",
        fail_final_checkpoint,
    )

    with pytest.raises(RuntimeError, match="final checkpoint write failed"):
        stream_parameter_surface_manifest(
            surface_rows,
            out_path=out_path,
            checkpoint_path=checkpoint_path,
            checkpoint_every_rows=1,
        )
    assert out_path.exists()
    assert not (tmp_path / "parameter_surface.jsonl.tmp").exists()


def test_parameter_surface_rejects_optimizer_claim_before_hbt_evidence(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="optimizer_claim_without_objective_evaluations",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=[
                _parameter_set(
                    "bayesian-prior",
                    {"entry_threshold": 0.5},
                    objective_evaluations=0,
                    optimizer_claim=True,
                )
            ],
        )


def test_parameter_surface_rejects_pre_hbt_economic_decision(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    campaign_rows[0]["admissibility_status"] = "model_" + "rejected"

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="pre_hbt_economic_decision",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=_parameter_sets(),
        )


def test_parameter_surface_promotion_requires_hbt_artifacts(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    campaign_rows[0]["promotion_decision_path"] = "promotion_decision.json"

    with pytest.raises(
        HftBacktestOnlyCampaignManifestError,
        match="promotion_requires_hbt_artifacts",
    ):
        build_parameter_surface_rows(
            campaign_rows=campaign_rows,
            parameter_sets=_parameter_sets(),
        )


def test_write_parameter_surface_manifest_outputs_summary(tmp_path: Path) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )
    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets()[:1],
    )

    summary = write_parameter_surface_manifest(
        surface_rows,
        out_path=tmp_path / "parameter_surface.jsonl",
        summary_path=tmp_path / "parameter_surface_summary.json",
    )

    assert summary["row_count"] == 3
    assert (tmp_path / "parameter_surface.jsonl").read_text(encoding="utf-8").count("\n") == 3
    persisted_summary = json.loads(
        (tmp_path / "parameter_surface_summary.json").read_text(encoding="utf-8")
    )
    assert persisted_summary["parameter_family_counts"] == {"grid": 3}
    validate_parameter_surface_rows(surface_rows)


def test_parameter_surface_preserves_data_blocker_not_model_rejection(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_dir = tmp_path / "prepared" / "MES" / "2024-09-11"
    prepared_dir.mkdir(parents=True)
    snapshot = prepared_dir / "initial_snapshot.npz"
    snapshot.write_bytes(b"hbt-initial-snapshot")
    (prepared_dir / "CPI_2024_09_11_TIGHT_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "hft3_hbt_only_lake_prepare_v1",
                "symbol": "MES",
                "contract": "MESU4",
                "event_id": "CPI_2024_09_11_TIGHT",
                "normalized_npz": str(prepared_dir / "missing_event_l3.npz"),
                "initial_snapshot": str(snapshot),
            }
        ),
        encoding="utf-8",
    )
    campaign_rows = build_campaign_manifest_rows(
        campaign_id="hbt_campaign_test",
        prepared_root=tmp_path / "prepared",
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
    )

    surface_rows = build_parameter_surface_rows(
        campaign_rows=campaign_rows,
        parameter_sets=_parameter_sets()[:1],
    )

    assert {row["admissibility_status"] for row in surface_rows} == {"data_blocker"}
    assert {row["blocker_code"] for row in surface_rows} == {
        "data_blocker:source_npz_missing"
    }
    assert "model_" + "rejected" not in json.dumps(surface_rows)


def test_cli_writes_parameter_surface_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "build_hftbacktest_only_campaign_manifest.py"
    spec = importlib.util.spec_from_file_location("build_hbt_campaign_manifest_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    parameter_sets_path = tmp_path / "parameter_sets.json"
    parameter_sets_path.write_text(
        json.dumps(_parameter_sets_payload(_parameter_sets()[:1])),
        encoding="utf-8",
    )

    assert module.main(
        [
            "--campaign-id",
            "hbt_campaign_test",
            "--prepared-root",
            str(prepared_root),
            "--model-registry",
            str(registry),
            "--adapter-status-json",
            str(_write_adapter_status_json(tmp_path)),
            "--out",
            str(tmp_path / "campaign_manifest.jsonl"),
            "--summary-out",
            str(tmp_path / "campaign_manifest_summary.json"),
            "--parameter-sets-json",
            str(parameter_sets_path),
            "--parameter-surface-out",
            str(tmp_path / "parameter_surface.jsonl"),
            "--parameter-surface-summary-out",
            str(tmp_path / "parameter_surface_summary.json"),
            "--canary-out",
            str(tmp_path / "canary_manifest.jsonl"),
            "--canary-count",
            "2",
            "--canary-summary-out",
            str(tmp_path / "canary_manifest_summary.json"),
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["campaign_manifest"]["row_count"] == 3
    assert output["parameter_surface"]["row_count"] == 3
    surface_summary = json.loads(
        (tmp_path / "parameter_surface_summary.json").read_text(encoding="utf-8")
    )
    assert surface_summary["parameter_family_counts"] == {"grid": 3}
    canary_summary = output["canary_manifest"]
    assert canary_summary["emitted_count"] == 2
    assert canary_summary["source_manifest"].endswith("parameter_surface.jsonl")
    assert canary_summary["parameter_surface_status"] == "parameter_surface_expanded"
    assert canary_summary["parameter_surface_config_status"] == "parameter_config_present"


def test_cli_rejects_non_self_learning_parameter_sets(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "build_hftbacktest_only_campaign_manifest.py"
    spec = importlib.util.spec_from_file_location("build_hbt_campaign_manifest_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    parameter_sets_path = tmp_path / "parameter_sets.json"
    parameter_sets_path.write_text(
        json.dumps({"parameter_sets": [{"parameter_family": "grid", "strategy_params": {}}]}),
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="self-learning export"):
        module.main(
            [
                "--campaign-id",
                "hbt_campaign_test",
                "--prepared-root",
                str(prepared_root),
                "--model-registry",
                str(registry),
                "--adapter-status-json",
                str(_write_adapter_status_json(tmp_path)),
                "--out",
                str(tmp_path / "campaign_manifest.jsonl"),
                "--parameter-sets-json",
                str(parameter_sets_path),
                "--parameter-surface-out",
                str(tmp_path / "parameter_surface.jsonl"),
            ]
        )


def test_cli_reports_invalid_default_parameter_sets_as_config_blocker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "build_hftbacktest_only_campaign_manifest.py"
    spec = importlib.util.spec_from_file_location("build_hbt_campaign_manifest_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    invalid_parameter_sets = tmp_path / "invalid_parameter_sets.json"
    invalid_parameter_sets.write_text(
        json.dumps({"parameter_sets": [{"parameter_family": "grid", "strategy_params": {}}]}),
        encoding="utf-8",
    )
    module.DEFAULT_PARAMETER_SETS = invalid_parameter_sets

    assert module.main(
        [
            "--campaign-id",
            "hbt_campaign_test",
            "--prepared-root",
            str(prepared_root),
            "--model-registry",
            str(registry),
            "--adapter-status-json",
            str(_write_adapter_status_json(tmp_path)),
            "--out",
            str(tmp_path / "campaign_manifest.jsonl"),
            "--summary-out",
            str(tmp_path / "campaign_manifest_summary.json"),
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["campaign_manifest"]["parameter_surface_config_status"].startswith(
        "pipeline_blocker:parameter_sets_config_invalid:"
    )


def test_stream_parameter_surface_manifest_reads_base_rows_without_materializing(
    tmp_path: Path,
) -> None:
    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    base_path = tmp_path / "campaign_manifest.jsonl"
    stream_campaign_manifest(
        campaign_id="hbt_campaign_test",
        prepared_root=prepared_root,
        registry_path=registry,
        adapter_status_by_model=_TEST_ADAPTERS_AVAILABLE,
        out_path=base_path,
    )

    summary = stream_parameter_surface_manifest(
        iter_parameter_surface_rows(
            campaign_rows=(json.loads(line) for line in base_path.read_text(encoding="utf-8").splitlines()),
            parameter_sets=_parameter_sets()[:1],
        ),
        out_path=tmp_path / "parameter_surface.jsonl",
        summary_path=tmp_path / "parameter_surface_summary.json",
        checkpoint_every_rows=2,
    )

    assert summary["row_count"] == 3
    assert summary["campaign_unit_count"] == 3
    assert summary["parameter_family_counts"] == {"grid": 3}
    assert (tmp_path / "parameter_surface.jsonl").read_text(encoding="utf-8").count("\n") == 3


def test_cli_missing_default_parameter_sets_is_config_blocker_not_grid_invention(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "build_hftbacktest_only_campaign_manifest.py"
    spec = importlib.util.spec_from_file_location("build_hbt_campaign_manifest_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    registry = _write_registry(tmp_path / "model_registry.yaml")
    prepared_root = tmp_path / "prepared"
    _write_prepared_unit(prepared_root)
    missing_parameter_sets = tmp_path / "missing_parameter_sets.json"
    module.DEFAULT_PARAMETER_SETS = missing_parameter_sets

    assert module.main(
        [
            "--campaign-id",
            "hbt_campaign_test",
            "--prepared-root",
            str(prepared_root),
            "--model-registry",
            str(registry),
            "--adapter-status-json",
            str(_write_adapter_status_json(tmp_path)),
            "--out",
            str(tmp_path / "campaign_manifest.jsonl"),
            "--summary-out",
            str(tmp_path / "campaign_manifest_summary.json"),
            "--canary-out",
            str(tmp_path / "canary_manifest.jsonl"),
            "--canary-count",
            "2",
            "--canary-summary-out",
            str(tmp_path / "canary_manifest_summary.json"),
        ]
    ) == 0

    output = json.loads(capsys.readouterr().out)
    summary = output["campaign_manifest"]
    assert summary["row_count"] == 3
    assert summary["parameter_surface_status"] == "base_only"
    assert summary["parameter_surface_config_status"] == (
        "pipeline_blocker:parameter_sets_config_missing"
    )
    canary_summary = output["canary_manifest"]
    assert canary_summary["emitted_count"] == 2
    assert canary_summary["hbt_jobs_started"] == 0
    assert canary_summary["manual_filter_used"] is False
    assert (tmp_path / "canary_manifest.jsonl").read_text(encoding="utf-8").count("\n") == 2
    assert not (tmp_path / "parameter_surface.jsonl").exists()


def _write_adapter_status_json(tmp_path: Path) -> Path:
    path = tmp_path / "adapter_status.json"
    path.write_text(json.dumps(_TEST_ADAPTERS_AVAILABLE), encoding="utf-8")
    return path
