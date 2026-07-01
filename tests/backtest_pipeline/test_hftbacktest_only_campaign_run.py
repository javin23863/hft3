from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest


@pytest.fixture(autouse=True)
def _restore_hftbacktest_modules():
    module_names = ("hftbacktest", "hftbacktest.data", "hftbacktest.types")
    original = {name: sys.modules.get(name) for name in module_names}
    yield
    for name, module in original.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


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
    hft_types.BUY_EVENT = 1 << 10
    hft_types.SELL_EVENT = 1 << 11
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
        side = (1 << 10) if index == 0 else (1 << 11)
        events[index]["ev"] = 10 | side | (1 << 8) | (1 << 9)
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


def test_campaign_runner_processes_manifest_without_eager_jsonl_load(tmp_path: Path, monkeypatch) -> None:
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

    original_read_text = Path.read_text

    def fail_manifest_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == manifest:
            raise AssertionError("campaign manifest must be streamed, not read eagerly")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_manifest_read_text)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        dry_run=True,
        workers=1,
    )

    assert summary["row_count"] == 2
    assert summary["max_tasks_per_child"] == 0
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
    assert {payload["strategy_id"] for payload in payloads} == {"hypothesis_limit_order"}
    assert {payload["adapter_status"] for payload in payloads} == {
        "available",
        "missing_uniform_hbt_adapter",
    }
    assert all("docs/project/HFTBACKTEST_ONLY_PIPELINE_PLAN.md" in payload["authority_refs"] for payload in payloads)
    assert all(payload["product_metadata_source"] == "config/hftbacktest/cme_lake_product_metadata.yaml" for payload in payloads)
    assert all(payload["metadata_policy"] == "explicit_per_symbol_contract_tick_lot_contract_required" for payload in payloads)
    assert {payload["status"] for payload in payloads} == {"dry_run", "blocked_before_hbt"}
    assert {payload["dry_run"] for payload in payloads} == {True}
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
        "strategy_params": {"quantity": 1.0, "max_steps": 1, "holding_period_bars": 7},
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
    assert payload["strategy_id"] == "hypothesis_limit_order"
    assert payload["dry_run"] is True
    assert payload["strategy_params"] == {"quantity": 1.0, "max_steps": 1, "holding_period_bars": 7}
    assert payload["strategy_params"]["max_steps"] == 1
    assert payload["strategy_params"]["holding_period_bars"] == 7
    assert payload["parameter_proposal_status"] == "declared_pre_hbt"
    assert payload["objective_evaluations"] == 0
    assert payload["optimizer_claim"] is False
    assert payload["tick_size"] == 0.25
    assert payload["lot_size"] == 1.0
    assert payload["contract_size"] == 50.0


def test_campaign_runner_forces_model_specific_hbt_strategy_for_paid_rows(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    row["strategy_params"] = {"quantity": 1.0, "max_steps": 1}
    blocked_row = _campaign_row(tmp_path, blocker_code="pipeline_blocker:missing_uniform_hbt_adapter")
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(
        "\n".join(json.dumps(candidate) for candidate in (row, blocked_row)) + "\n",
        encoding="utf-8",
    )
    captured_strategy_ids: list[str] = []

    def fake_run(config: object, **_kwargs: object) -> dict[str, object]:
        captured_strategy_ids.append(config.strategy_id)
        return {"status": "completed", "fail_closed_reasons": []}

    monkeypatch.setattr(module, "run_hftbacktest_only", fake_run)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=tmp_path / "runs",
        strategy_id="smoke_limit_order",
        dry_run=False,
        workers=1,
    )

    assert summary["row_count"] == 2
    assert summary["strategy_id"] == "hypothesis_limit_order"
    assert summary["requested_strategy_id"] == "smoke_limit_order"
    assert summary["strategy_override_ignored"] is True
    assert summary["status_counts"] == {"blocked_before_hbt": 1, "completed": 1}
    assert summary["blocker_counts"] == {"pipeline_blocker:missing_uniform_hbt_adapter": 1}
    assert captured_strategy_ids == ["hypothesis_limit_order"]
    receipts = sorted(Path(summary["out_root"]).glob("*/campaign_row_result.json"))
    assert len(receipts) == 2
    payloads = [json.loads(receipt.read_text(encoding="utf-8")) for receipt in receipts]
    assert {payload["canonical_model_id"] for payload in payloads} == {
        "SPREAD_BLOWOUT_RECOMPRESSION"
    }
    assert {payload["strategy_id"] for payload in payloads} == {"hypothesis_limit_order"}
    assert {payload["dry_run"] for payload in payloads} == {False}
    assert any(payload["status"] == "completed" and payload["hbt_run_id"] for payload in payloads)
    assert any(
        payload["status"] == "blocked_before_hbt"
        and payload["blocker_code"] == "pipeline_blocker:missing_uniform_hbt_adapter"
        for payload in payloads
    )
    assert any(payload["strategy_params"] == {"quantity": 1.0, "max_steps": 1} for payload in payloads)


def test_campaign_runner_pool_kwargs_enable_worker_recycling() -> None:
    module = _load_runner_module()

    kwargs = module._process_pool_kwargs(217, 256)

    assert kwargs["max_workers"] == 217
    assert kwargs["max_tasks_per_child"] == 256
    assert kwargs["mp_context"].get_start_method() == "spawn"
    assert module._process_pool_kwargs(217, 0) == {"max_workers": 217}
    with np.testing.assert_raises_regex(ValueError, "max_tasks_per_child must be >= 0"):
        module._process_pool_kwargs(217, -1)
    with np.testing.assert_raises(ValueError):
        module._nonnegative_int("", "max_tasks_per_child")


def test_campaign_runner_cli_passes_worker_recycling_limit(tmp_path: Path, monkeypatch) -> None:
    module = _load_runner_module()
    captured: dict[str, object] = {}

    def fake_run_campaign(**kwargs: object) -> dict[str, object]:
        captured.update(kwargs)
        return {"failed_count": 0}

    monkeypatch.setattr(module, "run_campaign", fake_run_campaign)

    exit_code = module.main(
        [
            "--campaign-manifest",
            str(tmp_path / "campaign.jsonl"),
            "--out-root",
            str(tmp_path / "runs"),
            "--workers",
            "217",
            "--max-tasks-per-child",
            "256",
            "--resume",
        ]
    )

    assert exit_code == 0
    assert captured["workers"] == 217
    assert captured["max_tasks_per_child"] == 256
    assert captured["resume"] is True


def test_campaign_runner_cli_rejects_negative_worker_recycling_limit(tmp_path: Path) -> None:
    module = _load_runner_module()

    try:
        module.main(
            [
                "--campaign-manifest",
                str(tmp_path / "campaign.jsonl"),
                "--max-tasks-per-child",
                "-1",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("negative worker recycling limit must fail closed")


def test_campaign_runner_cli_rejects_strategy_override(tmp_path: Path) -> None:
    module = _load_runner_module()

    try:
        module.main(
            [
                "--campaign-manifest",
                str(tmp_path / "campaign.jsonl"),
                "--strategy-id",
                "smoke_limit_order",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("full campaign CLI must not accept strategy overrides")


def test_campaign_runner_multiworker_recycles_and_resumes_existing_receipts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    rows = [
        _campaign_row(tmp_path),
        _campaign_row(tmp_path, blocker_code="pipeline_blocker:missing_uniform_hbt_adapter"),
    ]
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    out_root = tmp_path / "runs"
    existing_receipt = {
        "status": "dry_run",
        "blocker_code": "",
        "strategy_id": "hypothesis_limit_order",
        "strategy_surface_version": module.MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION,
        "dry_run": True,
        "canonical_model_id": rows[0]["canonical_model_id"],
    }
    existing_dir = out_root / "hbt_campaign_test" / "unit_admissible"
    existing_dir.mkdir(parents=True)
    (existing_dir / "campaign_row_result.json").write_text(
        json.dumps(existing_receipt),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeProcessPoolExecutor:
        def __init__(self, **kwargs: object) -> None:
            captured["executor_kwargs"] = kwargs

        def __enter__(self) -> "FakeProcessPoolExecutor":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def submit(self, fn: object, task: object) -> object:
            future = module.concurrent.futures.Future()
            future.set_result(fn(task))
            return future

    def fail_if_called(*_args: object, **_kwargs: object) -> dict[str, object]:
        raise AssertionError("resume must not rerun existing row receipts")

    monkeypatch.setattr(module.concurrent.futures, "ProcessPoolExecutor", FakeProcessPoolExecutor)
    monkeypatch.setattr(module, "run_hftbacktest_only", fail_if_called)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=True,
        workers=217,
        max_tasks_per_child=256,
        resume=True,
    )

    executor_kwargs = captured["executor_kwargs"]
    assert executor_kwargs["max_workers"] == 217
    assert executor_kwargs["max_tasks_per_child"] == 256
    assert executor_kwargs["mp_context"].get_start_method() == "spawn"
    assert summary["row_count"] == 2
    assert summary["workers"] == 217
    assert summary["max_tasks_per_child"] == 256
    assert summary["status_counts"] == {"blocked_before_hbt": 1, "dry_run": 1}


def test_campaign_runner_resume_reruns_stale_receipts_without_strategy_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_root = tmp_path / "runs"
    existing_dir = out_root / "hbt_campaign_test" / "unit_admissible"
    existing_dir.mkdir(parents=True)
    (existing_dir / "campaign_row_result.json").write_text(
        json.dumps({"status": "dry_run", "blocker_code": ""}),
        encoding="utf-8",
    )
    captured_strategy_ids: list[str] = []

    def fake_run(config: object, **_kwargs: object) -> dict[str, object]:
        captured_strategy_ids.append(config.strategy_id)
        return {"status": "completed", "fail_closed_reasons": []}

    monkeypatch.setattr(module, "run_hftbacktest_only", fake_run)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=False,
        workers=1,
        resume=True,
    )

    assert summary["status_counts"] == {"completed": 1}
    assert captured_strategy_ids == ["hypothesis_limit_order"]
    receipt = json.loads((existing_dir / "campaign_row_result.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["strategy_id"] == "hypothesis_limit_order"
    assert receipt["strategy_surface_version"] == module.MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION
    assert receipt["dry_run"] is False
    assert receipt["hbt_run_id"] == "unit_admissible"


def test_campaign_runner_paid_resume_reruns_dry_run_receipts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _install_fake_hftbacktest()
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_root = tmp_path / "runs"

    dry_summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=True,
        workers=1,
    )
    assert dry_summary["status_counts"] == {"dry_run": 1}
    receipt_path = next(Path(dry_summary["out_root"]).glob("*/campaign_row_result.json"))
    dry_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert dry_receipt["strategy_id"] == "hypothesis_limit_order"
    assert dry_receipt["strategy_surface_version"] == module.MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION
    assert dry_receipt["status"] == "dry_run"
    assert dry_receipt["dry_run"] is True
    captured_strategy_ids: list[str] = []

    def fake_run(config: object, **_kwargs: object) -> dict[str, object]:
        captured_strategy_ids.append(config.strategy_id)
        return {"status": "completed", "fail_closed_reasons": []}

    monkeypatch.setattr(module, "run_hftbacktest_only", fake_run)

    paid_summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=False,
        workers=1,
        resume=True,
    )

    assert paid_summary["status_counts"] == {"completed": 1}
    assert captured_strategy_ids == ["hypothesis_limit_order"]
    paid_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert paid_receipt["status"] == "completed"
    assert paid_receipt["strategy_id"] == "hypothesis_limit_order"
    assert paid_receipt["strategy_surface_version"] == module.MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION
    assert paid_receipt["dry_run"] is False
    assert paid_receipt["hbt_run_id"] == "unit_admissible"


def test_campaign_runner_resume_reruns_stale_receipts_without_surface_version(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_root = tmp_path / "runs"
    existing_dir = out_root / "hbt_campaign_test" / "unit_admissible"
    existing_dir.mkdir(parents=True)
    (existing_dir / "campaign_row_result.json").write_text(
        json.dumps(
            {
                "status": "pipeline_blocker",
                "blocker_code": "pipeline_blocker:no_hbt_order_submitted",
                "strategy_id": "hypothesis_limit_order",
                "dry_run": False,
            }
        ),
        encoding="utf-8",
    )
    captured_strategy_ids: list[str] = []

    def fake_run(config: object, **_kwargs: object) -> dict[str, object]:
        captured_strategy_ids.append(config.strategy_id)
        return {"status": "completed", "fail_closed_reasons": []}

    monkeypatch.setattr(module, "run_hftbacktest_only", fake_run)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=False,
        workers=1,
        resume=True,
    )

    assert summary["status_counts"] == {"completed": 1}
    assert captured_strategy_ids == ["hypothesis_limit_order"]
    receipt = json.loads((existing_dir / "campaign_row_result.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["strategy_id"] == "hypothesis_limit_order"
    assert receipt["strategy_surface_version"] == module.MODEL_SPECIFIC_STRATEGY_SURFACE_VERSION


def test_campaign_runner_resume_reruns_stale_receipts_with_noncanonical_strategy_id(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_runner_module()
    _write_valid_npz(tmp_path / "data.npz")
    _write_valid_npz(tmp_path / "snapshot.npz")
    row = _campaign_row(tmp_path)
    manifest = tmp_path / "campaign.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    out_root = tmp_path / "runs"
    existing_dir = out_root / "hbt_campaign_test" / "unit_admissible"
    existing_dir.mkdir(parents=True)
    (existing_dir / "campaign_row_result.json").write_text(
        json.dumps({"status": "completed", "blocker_code": "", "strategy_id": "smoke_limit_order"}),
        encoding="utf-8",
    )
    captured_strategy_ids: list[str] = []

    def fake_run(config: object, **_kwargs: object) -> dict[str, object]:
        captured_strategy_ids.append(config.strategy_id)
        return {"status": "completed", "fail_closed_reasons": []}

    monkeypatch.setattr(module, "run_hftbacktest_only", fake_run)

    summary = module.run_campaign(
        manifest_path=manifest,
        out_root=out_root,
        dry_run=False,
        workers=1,
        resume=True,
    )

    assert summary["status_counts"] == {"completed": 1}
    assert captured_strategy_ids == ["hypothesis_limit_order"]
    receipt = json.loads((existing_dir / "campaign_row_result.json").read_text(encoding="utf-8"))
    assert receipt["status"] == "completed"
    assert receipt["strategy_id"] == "hypothesis_limit_order"
    assert receipt["hbt_run_id"] == "unit_admissible"


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


def test_campaign_runner_preserves_prefixed_blocker_codes() -> None:
    module = _load_runner_module()

    assert (
        module._fail_closed_blocker(
            "pipeline_blocker",
            ["pipeline_blocker:no_hbt_order_submitted"],
        )
        == "pipeline_blocker:no_hbt_order_submitted"
    )
    assert (
        module._fail_closed_blocker(
            "data_invalid",
            ["data_blocker:TIMESTAMP_UNITS_UNPROVEN"],
        )
        == "data_blocker:TIMESTAMP_UNITS_UNPROVEN"
    )
    assert (
        module._fail_closed_blocker(
            "authority_missing",
            ["authority_missing"],
        )
        == "authority_missing"
    )
    assert (
        module._fail_closed_blocker(
            "authority_missing",
            ["authority_missing:canonical_model_id_missing"],
        )
        == "authority_missing:canonical_model_id_missing"
    )
