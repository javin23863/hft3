from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np


def _load_runner_module():
    repo = Path(__file__).resolve().parents[2]
    script = repo / "scripts" / "run_hftbacktest_only_campaign.py"
    spec = importlib.util.spec_from_file_location("run_hbt_only_campaign_cli", script)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _install_fake_hftbacktest() -> None:
    hft = ModuleType("hftbacktest")
    hft_data = ModuleType("hftbacktest.data")
    hft_types = ModuleType("hftbacktest.types")
    hft_types.EXCH_EVENT = 1 << 8
    hft_types.LOCAL_EVENT = 1 << 9
    hft_types.ADD_ORDER_EVENT = 10
    hft_types.DEPTH_EVENT = 1
    hft_types.TRADE_EVENT = 2
    hft_types.CANCEL_ORDER_EVENT = 11
    hft_types.MODIFY_ORDER_EVENT = 12
    hft_types.FILL_EVENT = 13
    hft_types.event_dtype = np.dtype(
        [
            ("ev", np.uint32),
            ("exch_ts", np.int64),
            ("local_ts", np.int64),
            ("px", np.float64),
            ("qty", np.float64),
            ("order_id", np.uint64),
            ("ival", np.int64),
            ("fval", np.float64),
        ]
    )

    def validate_event_order(_events):
        return None

    hft_data.validate_event_order = validate_event_order
    hft.types = hft_types
    sys.modules["hftbacktest"] = hft
    sys.modules["hftbacktest.data"] = hft_data
    sys.modules["hftbacktest.types"] = hft_types


def _write_valid_npz(path: Path) -> Path:
    dtype = np.dtype(
        [
            ("ev", np.uint32),
            ("exch_ts", np.int64),
            ("local_ts", np.int64),
            ("px", np.float64),
            ("qty", np.float64),
            ("order_id", np.uint64),
            ("ival", np.int64),
            ("fval", np.float64),
        ]
    )
    events = np.zeros(2, dtype=dtype)
    for index in range(2):
        events[index]["ev"] = 10 | (1 << 8) | (1 << 9)
        events[index]["exch_ts"] = 1_000_000_000 + index * 100
        events[index]["local_ts"] = 1_000_000_100 + index * 100
        events[index]["px"] = 5000.0 + index * 0.25
        events[index]["qty"] = 1.0
        events[index]["order_id"] = 1000 + index
    np.savez_compressed(path, data=events, timestamp_units="nanoseconds")
    return path


def _campaign_row(tmp_path: Path, *, blocker_code: str = "") -> dict[str, object]:
    return {
        "campaign_id": "hbt_campaign_test",
        "unit_id": "unit_admissible" if not blocker_code else "unit_blocked",
        "canonical_model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
        "legacy_aliases": ["HYP_5"],
        "registry_hash": "a" * 64,
        "source_npz": str(tmp_path / "data.npz"),
        "source_npz_sha256": "b" * 64,
        "symbol": "ES.v.0",
        "contract": "ES.v.0",
        "event_id": "CPI_2024_09_11_TIGHT",
        "event_window": {},
        "initial_snapshot": str(tmp_path / "snapshot.npz"),
        "initial_snapshot_sha256": "c" * 64,
        "prepared_manifest": str(tmp_path / "prepared_manifest.json"),
        "tick_size": 0.25,
        "lot_size": 1.0,
        "contract_size": 50.0,
        "product_metadata_source": "config/hftbacktest/cme_lake_product_metadata.yaml",
        "metadata_policy": "explicit_per_symbol_contract_tick_lot_contract_required",
        "admissibility_status": "admissible" if not blocker_code else "pipeline_blocker",
        "blocker_code": blocker_code,
        "blocker_detail": "canonical_model_id=SPREAD_BLOWOUT_RECOMPRESSION" if blocker_code else "",
        "authority_refs": [
            "docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md",
            "config/hftbacktest/cme_lake_product_metadata.yaml",
        ],
        "adapter_status": "available" if not blocker_code else "missing_uniform_hbt_adapter",
        "hbt_run_status": "not_started",
        "hbt_run_id": "",
        "promotion_decision_path": "",
    }


def test_campaign_runner_processes_every_row_as_hbt_or_blocker(tmp_path: Path) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    manifest = tmp_path / "campaign.jsonl"
    rows = [
        _campaign_row(tmp_path),
        _campaign_row(tmp_path, blocker_code="pipeline_blocker:missing_uniform_hbt_adapter"),
    ]
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        dry_run=True,
        workers=1,
    )

    assert summary["row_count"] == 2
    assert summary["status_counts"] == {"blocked_before_hbt": 1, "dry_run": 1}
    assert summary["blocker_counts"] == {"pipeline_blocker:missing_uniform_hbt_adapter": 1}
    run_root = Path(summary["out_root"])
    receipts = sorted(run_root.glob("*/campaign_row_result.json"))
    assert len(receipts) == 2
    payloads = [json.loads(path.read_text(encoding="utf-8")) for path in receipts]
    assert {payload["canonical_model_id"] for payload in payloads} == {
        "SPREAD_BLOWOUT_RECOMPRESSION"
    }
    assert {payload["registry_hash"] for payload in payloads} == {"a" * 64}
    assert {payload["source_npz_sha256"] for payload in payloads} == {"b" * 64}
    assert {payload["initial_snapshot_sha256"] for payload in payloads} == {"c" * 64}
    assert {payload["adapter_status"] for payload in payloads} == {
        "available",
        "missing_uniform_hbt_adapter",
    }
    assert all("docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md" in payload["authority_refs"] for payload in payloads)
    assert all(payload["product_metadata_source"] == "config/hftbacktest/cme_lake_product_metadata.yaml" for payload in payloads)
    assert all(payload["metadata_policy"] == "explicit_per_symbol_contract_tick_lot_contract_required" for payload in payloads)
    assert {payload["status"] for payload in payloads} == {"dry_run", "blocked_before_hbt"}
    assert "model_" + "rejected" not in json.dumps(payloads)


def test_campaign_runner_accepts_parameter_surface_rows(tmp_path: Path) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    surface_row = {
        **_campaign_row(tmp_path),
        "surface_unit_id": "surface_unit_abc",
        "parameter_family": "grid",
        "parameter_hash": "d" * 64,
        "strategy_params": {"quantity": 1.0, "max_steps": 1},
        "parameter_proposal_status": "declared_pre_hbt",
        "objective_evaluations": 0,
        "optimizer_claim": False,
    }
    manifest = tmp_path / "parameter_surface.jsonl"
    manifest.write_text(json.dumps(surface_row) + "\n", encoding="utf-8")

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        dry_run=True,
        workers=1,
    )

    assert summary["row_count"] == 1
    assert summary["status_counts"] == {"dry_run": 1}
    receipt = next(Path(summary["out_root"]).glob("*/campaign_row_result.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["surface_unit_id"] == "surface_unit_abc"
    assert payload["parameter_family"] == "grid"
    assert payload["parameter_hash"] == "d" * 64
    assert payload["strategy_params"] == {"quantity": 1.0, "max_steps": 1}
    assert payload["parameter_proposal_status"] == "declared_pre_hbt"
    assert payload["objective_evaluations"] == 0
    assert payload["optimizer_claim"] is False
    assert payload["tick_size"] == 0.25
    assert payload["lot_size"] == 1.0
    assert payload["contract_size"] == 50.0


def test_campaign_runner_blocks_missing_product_metadata_authority(tmp_path: Path) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    row["product_metadata_source"] = ""
    row["metadata_policy"] = ""
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        dry_run=True,
        workers=1,
    )

    assert summary["status_counts"] == {"blocked_before_hbt": 1}
    assert summary["blocker_counts"] == {"authority_missing": 1}
    receipt = next(Path(summary["out_root"]).glob("*/campaign_row_result.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["blocker_detail"] == "missing_hbt_run_metadata:product_metadata_source,metadata_policy"


def test_campaign_runner_augments_preblocked_rows_with_metadata_blockers(tmp_path: Path) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path, blocker_code="authority_missing")
    row["blocker_detail"] = "instrument_metadata_missing:contract_size"
    row["product_metadata_source"] = ""
    row["metadata_policy"] = ""
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        dry_run=True,
        workers=1,
    )

    assert summary["status_counts"] == {"blocked_before_hbt": 1}
    receipt = next(Path(summary["out_root"]).glob("*/campaign_row_result.json"))
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert "instrument_metadata_missing:contract_size" in payload["blocker_detail"]
    assert "missing_hbt_run_metadata:product_metadata_source,metadata_policy" in payload["blocker_detail"]
