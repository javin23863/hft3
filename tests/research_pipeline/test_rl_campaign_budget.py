from __future__ import annotations

import pytest

from research_pipeline.rl_campaign_budget import (
    RL_CAMPAIGN_BUDGET_SCHEMA_VERSION,
    plan_rl_campaign_budget,
    select_stratified_pilot_rows,
)


def test_rl_campaign_budget_inventories_manifest_rows_without_payload_reads() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {
                "symbol": "ES",
                "event_id": "CPI_2025_01",
                "row_count": 1200,
                "feature_names": ["order_book_imbalance", "spread"],
                "store_path": "data/features/ES_CPI_2025_01.npz",
                "content_hash": "hash-es-1",
            },
            {
                "symbol": "ES",
                "event_id": "FOMC_2025_01",
                "row_summary": {"built_rows": 800},
                "features": ["queue_imbalance"],
                "store_path": "data/features/ES_FOMC_2025_01.npz",
                "content_hash": "hash-es-2",
            },
            {
                "symbol": "NQ",
                "event_id": "CPI_2025_01",
                "source_rows": 500,
                "features": {"spread": {}, "order_flow_imbalance": {}},
            },
        ],
        vast_credit_usd=50,
        vast_gpu_hour_rate_usd=2.5,
        budget_reserve_usd=10,
        supported_features=["order_book_imbalance", "spread", "queue_imbalance"],
        required_features=["order_book_imbalance", "hidden_liquidity"],
    )

    assert plan["schema_version"] == RL_CAMPAIGN_BUDGET_SCHEMA_VERSION
    assert plan["status"] == "pilot_plan_ready_full_training_blocked"
    assert plan["read_only"] is True
    assert plan["npz_payloads_read"] is False
    assert plan["training_started"] is False
    assert plan["theoretical_gpu_hours"] == 20.0
    assert plan["usable_gpu_hours"] == 16.0
    assert plan["unsupported_required_features"] == ["hidden_liquidity"]
    assert plan["data_inventory_by_symbol"]["ES"] == {
        "manifest_row_count": 2,
        "event_ids": ["CPI_2025_01", "FOMC_2025_01"],
        "total_rows": 2000,
        "missing_row_count_entries": 0,
        "non_source_row_count_entries": 2,
        "row_count_basis_counts": {"ambiguous_rows": 1, "built_rows": 1},
        "features": ["order_book_imbalance", "queue_imbalance", "spread"],
        "paths": [
            "data/features/ES_CPI_2025_01.npz",
            "data/features/ES_FOMC_2025_01.npz",
        ],
        "hashes": ["hash-es-1", "hash-es-2"],
    }
    assert plan["data_inventory_by_symbol"]["NQ"]["total_rows"] == 500
    assert plan["stage_statuses"]["stratified_pilot"]["status"] == "planned"
    assert plan["stage_statuses"]["full_training"]["status"] == "blocked"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "unsupported_required_features",
        "inventory_row_count_basis_not_source",
        "measured_throughput_missing",
    ]


def test_rl_campaign_budget_blocks_full_training_without_measured_throughput() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {
                "symbol": "ES",
                "event_id": "CPI_2025_01",
                "row_count": 100,
                "feature_names": ["order_book_imbalance", "spread"],
            }
        ],
        vast_credit_usd=12,
        vast_gpu_hour_rate_usd=3,
        budget_reserve_usd=3,
        supported_features=["order_book_imbalance", "spread"],
        required_features=["order_book_imbalance"],
    )

    assert plan["usable_gpu_hours"] == 3.0
    assert plan["stage_statuses"]["stratified_pilot"]["status"] == "planned"
    assert plan["stage_statuses"]["full_training"]["status"] == "blocked"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "inventory_row_count_basis_not_source",
        "measured_throughput_missing"
    ]


def test_rl_campaign_budget_plans_full_training_when_requirements_and_throughput_exist() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "source_rows": 100},
            {"symbol": "NQ", "event_id": "CPI_2025_01", "source_rows": 75},
        ],
        vast_credit_usd=25,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=5,
        supported_features=["order_book_imbalance", "spread"],
        required_features=["spread", "order_book_imbalance"],
        measured_throughput_rows_per_gpu_hour=100,
    )

    assert plan["status"] == "full_training_plan_ready"
    assert plan["theoretical_gpu_hours"] == 5.0
    assert plan["usable_gpu_hours"] == 4.0
    assert plan["estimated_trainable_rows"] == 400
    assert plan["known_inventory_rows"] == 175
    assert plan["measured_throughput_row_basis"] == "manifest_source_rows"
    assert plan["estimated_full_inventory_gpu_hours"] == 1.75
    assert plan["estimated_full_inventory_cost_usd"] == 8.75
    assert plan["estimated_full_inventory_covered"] is True
    assert plan["stage_statuses"]["full_training"]["source_inventory_complete"] is True
    assert plan["stage_statuses"]["full_training"]["status"] == "planned"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == []
    assert plan["stage_statuses"]["downstream_validation"]["status"] == (
        "blocked_downstream_validation_required"
    )


def test_rl_campaign_budget_blocks_full_training_when_budget_cannot_cover_inventory() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "source_rows": 700},
            {"symbol": "NQ", "event_id": "CPI_2025_01", "source_rows": 700},
        ],
        vast_credit_usd=10,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=5,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert plan["usable_gpu_hours"] == 1.0
    assert plan["estimated_trainable_rows"] == 1000
    assert plan["known_inventory_rows"] == 1400
    assert plan["estimated_full_inventory_gpu_hours"] == 1.4
    assert plan["estimated_full_inventory_cost_usd"] == 7.0
    assert plan["estimated_full_inventory_covered"] is False
    assert plan["stage_statuses"]["full_training"]["status"] == "blocked"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "budget_insufficient_for_full_inventory"
    ]


def test_rl_campaign_budget_prefers_source_rows_over_built_rows_for_inventory() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {
                "symbol": "ES",
                "event_id": "CPI_2025_01",
                "row_count": 10,
                "row_summary": {"source_rows": 1000, "built_rows": 10},
            }
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert plan["known_inventory_rows"] == 1000
    assert plan["data_inventory_by_symbol"]["ES"]["total_rows"] == 1000
    assert plan["estimated_full_inventory_gpu_hours"] == 1.0
    assert plan["stage_statuses"]["full_training"]["status"] == "planned"


def test_rl_campaign_budget_blocks_full_training_on_ambiguous_top_level_row_count() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "row_count": 100},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert plan["known_inventory_rows"] == 100
    assert plan["non_source_row_count_entries"] == 1
    assert plan["stage_statuses"]["full_training"]["source_inventory_complete"] is False
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "inventory_row_count_basis_not_source"
    ]


def test_rl_campaign_budget_blocks_non_source_row_throughput_basis() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "source_rows": 100},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
        measured_throughput_row_basis="trained_rows",
    )

    assert plan["measured_throughput_row_basis"] == "trained_rows"
    assert plan["estimated_trainable_rows"] is None
    assert plan["estimated_full_inventory_covered"] is None
    assert plan["stage_statuses"]["full_training"]["status"] == "blocked"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "measured_throughput_row_basis_mismatch"
    ]


def test_rl_campaign_budget_blocks_full_training_when_source_rows_are_missing() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "feature_names": ["spread"]},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert plan["known_inventory_rows"] == 0
    assert plan["missing_row_count_entries"] == 1
    assert plan["stage_statuses"]["full_training"]["status"] == "blocked"
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "inventory_source_rows_zero",
        "inventory_source_row_counts_missing"
    ]


def test_rl_campaign_budget_blocks_pilot_when_no_row_counts_are_selectable() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "feature_names": ["spread"]},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        pilot_target_rows=100,
    )

    assert plan["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["selection"]["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["failure_reasons"] == [
        "inventory_source_rows_zero",
        "no_manifest_rows_with_row_counts"
    ]


def test_rl_campaign_budget_blocks_zero_source_rows() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "source_rows": 0},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
        pilot_target_rows=100,
    )

    assert plan["known_inventory_rows"] == 0
    assert plan["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["failure_reasons"] == [
        "inventory_source_rows_zero",
        "no_positive_manifest_rows"
    ]
    assert plan["stage_statuses"]["full_training"]["failure_reasons"] == [
        "inventory_source_rows_zero"
    ]


def test_rl_campaign_budget_blocks_zero_source_rows_without_pilot_target() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "CPI_2025_01", "source_rows": 0},
        ],
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert plan["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["status"] == "blocked"
    assert plan["stage_statuses"]["stratified_pilot"]["failure_reasons"] == [
        "inventory_source_rows_zero"
    ]


def test_rl_campaign_budget_fingerprints_source_row_manifest() -> None:
    rows = [
        {"symbol": "ES", "event_id": "A", "source_rows": 10, "store_path": "A.npz"},
        {"symbol": "NQ", "event_id": "B", "source_rows": 20, "store_path": "B.npz"},
    ]
    first = plan_rl_campaign_budget(
        feature_manifest_rows=rows,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )
    second = plan_rl_campaign_budget(
        feature_manifest_rows=list(reversed(rows)),
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert first["manifest_source_row_fingerprint"] == second["manifest_source_row_fingerprint"]
    assert first["manifest_source_row_fingerprint"]["entry_count"] == 2
    assert first["manifest_source_row_fingerprint"]["source_row_count"] == 30


def test_rl_campaign_budget_fingerprint_binds_feature_index_hash() -> None:
    base = {
        ("ES", "A"): {
            "source_rows": 10,
            "store_path": "A.npz",
            "content_hash": "content-a",
            "feature_index_hash": "feature-hash-a",
        }
    }
    drifted = {
        ("ES", "A"): {
            "source_rows": 10,
            "store_path": "A.npz",
            "content_hash": "content-a",
            "feature_index_hash": "feature-hash-b",
        }
    }

    first = plan_rl_campaign_budget(
        feature_manifest_rows=base,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )
    second = plan_rl_campaign_budget(
        feature_manifest_rows=drifted,
        vast_credit_usd=20,
        vast_gpu_hour_rate_usd=5,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=["spread"],
        measured_throughput_rows_per_gpu_hour=1000,
    )

    assert first["known_inventory_rows"] == second["known_inventory_rows"]
    assert (
        first["manifest_source_row_fingerprint"]["sha256"]
        != second["manifest_source_row_fingerprint"]["sha256"]
    )


def test_rl_campaign_budget_accepts_tuple_key_manifest_mapping() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows={
            ("ES", "CPI_2025_01"): {"row_count": 10},
            ("ES", "FOMC_2025_01"): {"row_count": 15},
        },
        vast_credit_usd=4,
        vast_gpu_hour_rate_usd=2,
        budget_reserve_usd=0,
        supported_features=["spread"],
        required_features=[],
        measured_throughput_rows_per_gpu_hour=10,
    )

    assert plan["data_inventory_by_symbol"]["ES"]["event_ids"] == [
        "CPI_2025_01",
        "FOMC_2025_01",
    ]
    assert plan["data_inventory_by_symbol"]["ES"]["total_rows"] == 25


def test_rl_campaign_budget_rejects_invalid_budget_inputs() -> None:
    with pytest.raises(ValueError, match="vast_gpu_hour_rate_usd must be positive"):
        plan_rl_campaign_budget(
            feature_manifest_rows=[],
            vast_credit_usd=1,
            vast_gpu_hour_rate_usd=0,
            budget_reserve_usd=0,
            supported_features=[],
            required_features=[],
        )

    with pytest.raises(ValueError, match="measured_throughput_rows_per_gpu_hour must be positive"):
        plan_rl_campaign_budget(
            feature_manifest_rows=[],
            vast_credit_usd=1,
            vast_gpu_hour_rate_usd=1,
            budget_reserve_usd=0,
            supported_features=[],
            required_features=[],
            measured_throughput_rows_per_gpu_hour=0,
        )


def test_stratified_pilot_selection_covers_each_symbol_before_filling_target() -> None:
    selection = select_stratified_pilot_rows(
        [
            {"symbol": "ES", "event_id": "ES_BIG", "n_rows": 100, "store_path": "ES_BIG.npz"},
            {"symbol": "ES", "event_id": "ES_SMALL", "n_rows": 20, "store_path": "ES_SMALL.npz"},
            {"symbol": "NQ", "event_id": "NQ_BIG", "n_rows": 90, "store_path": "NQ_BIG.npz"},
            {"symbol": "RTY", "event_id": "RTY_SMALL", "n_rows": 30, "store_path": "RTY_SMALL.npz"},
        ],
        target_rows=180,
    )

    assert selection["status"] == "planned"
    assert selection["target_rows"] == 180
    assert selection["selected_rows"] >= 180
    assert selection["selected_symbols"] == ["ES", "NQ", "RTY"]
    assert set(selection["selected_rows_by_symbol"]) == {"ES", "NQ", "RTY"}
    assert all(value > 0 for value in selection["selected_rows_by_symbol"].values())
    assert [row["event_id"] for row in selection["selected_manifest_rows"]][:3] == [
        "ES_BIG",
        "NQ_BIG",
        "RTY_SMALL",
    ]


def test_rl_campaign_budget_embeds_optional_pilot_selection() -> None:
    plan = plan_rl_campaign_budget(
        feature_manifest_rows=[
            {"symbol": "ES", "event_id": "ES_BIG", "n_rows": 100},
            {"symbol": "NQ", "event_id": "NQ_BIG", "n_rows": 90},
        ],
        vast_credit_usd=10,
        vast_gpu_hour_rate_usd=2,
        budget_reserve_usd=1,
        supported_features=["spread"],
        required_features=["spread"],
        pilot_target_rows=150,
    )

    selection = plan["stage_statuses"]["stratified_pilot"]["selection"]
    assert selection["status"] == "planned"
    assert selection["selected_symbols"] == ["ES", "NQ"]
    assert selection["selected_rows"] >= 150
