from __future__ import annotations

import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from backtest_pipeline.src import hftbacktest_realism as hbt4
from backtest_pipeline.src.fee_model import FeeModel
from backtest_pipeline.src.vectorbt_adapter import compute_screening_artifact_hash


NATIVE_CPP_LATENCY_EVIDENCE_HASH = f"sha256:{'a' * 64}"
NATIVE_CPP_LATENCY_EVIDENCE = (
    "reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"
    f"#{NATIVE_CPP_LATENCY_EVIDENCE_HASH}"
)


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def _event_contract() -> tuple[np.dtype, dict[str, int]]:
    event_dtype = np.dtype(
        [
            ("ev", np.uint32),
            ("exch_ts", np.int64),
            ("local_ts", np.int64),
            ("px", np.float64),
            ("qty", np.float64),
            ("order_id", np.int64),
            ("ival", np.int64),
            ("fval", np.float64),
        ]
    )
    constants = {
        "EXCH_EVENT": 1 << 8,
        "LOCAL_EVENT": 1 << 9,
        "DEPTH_EVENT": 1,
    }
    return event_dtype, constants


def _event_row(
    constants: dict[str, int],
    *,
    exch_ts: int,
    local_ts: int,
    px: float,
    qty: float,
) -> dict[str, object]:
    return {
        "ev": constants["DEPTH_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
        "exch_ts": exch_ts,
        "local_ts": local_ts,
        "px": px,
        "qty": qty,
        "order_id": 0,
        "ival": 0,
        "fval": 0.0,
    }


def _write_valid_l2_npz(path: Path) -> Path:
    event_dtype, constants = _event_contract()
    rows = [
        _event_row(constants, exch_ts=1_000_000_000, local_ts=1_000_000_100, px=5000.00, qty=5.0),
        _event_row(constants, exch_ts=1_000_000_500, local_ts=1_000_000_600, px=5000.25, qty=4.0),
        _event_row(constants, exch_ts=1_000_001_000, local_ts=1_000_001_100, px=5000.00, qty=6.0),
    ]
    events = np.zeros(len(rows), dtype=event_dtype)
    for index, row in enumerate(rows):
        for field, value in row.items():
            events[index][field] = value
    np.savez_compressed(path, data=events, timestamp_units="nanoseconds")
    return path


def _screening_artifact(
    *,
    candidate_id: str = "cand_hbt4",
    include_intent: bool = True,
) -> dict:
    candidate = {
        "candidate_id": candidate_id,
        "hypothesis_id": "HYP_5",
        "model_id": "HYP_5",
        "symbol": "MES",
        "param_values": {"signal_threshold": 0.15},
        "research_clock": "event_window_pilot",
        "opportunity_type_or_event_type": "CPI",
        "parameter_values": {"signal_threshold": 0.15},
        "parameter_values_hash": "sha256:parameter-values",
        "trials_budget_tier": "pilot",
        "in_sample_metrics": {"sharpe": 1.2, "net_pnl": 125.0},
        "out_of_sample_metrics": {"sharpe": 1.0, "net_pnl": 80.0},
        "walk_forward_metrics": {
            "fold_matrix": [["2018-2020", "2021"], ["2019-2021", "2022"]],
            "fold_train_test_dates": [
                {"train": ["2018-01-01", "2020-12-31"], "test": ["2021-01-01", "2021-12-31"]},
                {"train": ["2019-01-01", "2021-12-31"], "test": ["2022-01-01", "2022-12-31"]},
            ],
            "fold_metrics": [{"sharpe": 1.0}, {"sharpe": 1.1}],
            "walk_forward_efficiency": 0.72,
            "fold_dispersion": 0.08,
            "is_oos_gap": 0.12,
            "oos_decay": 0.18,
        },
        "wfc_metrics": {
            "metric_in_sample": [1.2, 1.0, 0.9],
            "metric_out_of_sample": [1.0, 0.86, 0.78],
            "pearson": 0.64,
            "spearman": 0.58,
            "scatter_data": [{"is": 1.2, "oos": 1.0}],
            "quadrant_counts": {"high_is_high_oos": 2, "high_is_low_oos": 0},
            "high_is_high_oos_region": {"threshold": 0.8, "count": 2},
            "rejection_reason": "not_rejected",
        },
        "surface_stability_metrics": {"plateau_score": 0.81},
        "robustness_gate_scope": "pilot",
        "wfc_status": "pass",
        "dsr_status": "pass",
        "pbo_status": "pass",
        "cscv_status": "pass",
        "robustness_artifact_staleness": "fresh",
        "trade_count": 32,
        "gross_return": 0.042,
        "total_fees": 12.0,
        "total_slippage": 4.0,
        "net_return": 0.031,
        "net_pnl": 80.0,
        "expectancy_per_trade": 2.5,
        "profit_factor": 1.35,
        "sharpe": 1.0,
        "sortino": 1.4,
        "max_drawdown": 0.012,
        "turnover": 7.0,
        "bootstrap_ci_or_not_run": {"status": "pass", "lower": 0.01, "upper": 0.05},
        "dsr_or_not_run": {"status": "pass", "dsr_pass": True, "dsr_cdf": 0.96},
        "pbo_or_not_run": {"status": "pass", "pbo_pass": True, "pbo": 0.12, "maximum_pbo": 0.2},
        "cscv_count_or_not_run": {"status": "pass", "n_partitions": 16, "n_configs": 8},
        "screening_status": "pass",
        "replay_eligibility_status": "eligible",
        "rejection_reason_or_null": None,
    }
    if include_intent:
        candidate["hbt4_order_intent"] = {
            "side": "BUY",
            "quantity": 1.0,
            "price_mode": "passive_best_bid_or_ask",
            "max_feed_steps": 3,
        }
    artifact = {
        "run_id": "vbt_handoff_hbt4",
        "created_at_utc": "2026-06-16T00:00:00+00:00",
        "screening_backend": "vectorbt",
        "vectorbt_version": "1.0.0",
        "vectorbt_engine": "rust",
        "engine_parity_status": "rust_available",
        "rust_engine_required_for_scope": True,
        "rust_engine_available": True,
        "license_review": "pilot_license_review_recorded",
        "screening_scope": "pilot",
        "research_clock": "event_window_pilot",
        "candidate_ids": [candidate_id],
        "candidate_reasons": {candidate_id: "queued_for_vectorbt_screen"},
        "promoted_ids": [candidate_id],
        "promoted_reasons": {candidate_id: "all_gates_passed"},
        "rejected_ids": [],
        "rejected_reasons": {},
        "no_lookahead_signal_shift_proof": "close-derived signals shifted one executable bar",
        "promoted": [candidate],
        "rejected": [],
    }
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    return artifact


def _constant_latency_model() -> dict:
    return {
        "native_latency_probe_artifact": NATIVE_CPP_LATENCY_EVIDENCE,
        "native_latency_probe_artifact_hash": NATIVE_CPP_LATENCY_EVIDENCE_HASH,
        "native_latency_probe_status": "provided",
        "native_latency_probe_provenance": "hft3_native_cpp_rithmic_latency_probe",
        "native_latency_probe_host": "CHI404",
        "latency_model_family": "ConstantLatency",
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
        "feed_latency_ms": 0.24,
        "order_entry_latency_ms": 3.128,
        "order_response_latency_ms": 3.128,
    }


def _valid_l2_fill_queue_model() -> dict:
    fee = FeeModel(product="MES").get_fee_per_contract()
    return {
        "exchange_model": "NoPartialFillExchange",
        "exchange_model_source": "asset.no_partial_fill_exchange",
        "queue_model": "LogProbQueueModel2",
        "queue_model_source": "asset.log_prob_queue_model2",
        "fill_model_scope": "l2_mbp",
        "partial_fill_policy": "no_partial_fill",
        "time_in_force_policy": "post_only_cancel_remaining",
        "maker_fee": fee,
        "taker_fee": fee,
        "tick_size": 0.25,
        "lot_size": 1.0,
        "minimum_order_qty": 1.0,
        "market_impact_mode": "external_charge",
        "market_impact_charge_model": "depth_scaled_external_slippage_charge",
        "market_impact_charge_units": "ticks_per_contract",
        "market_impact_charge_value": 0.25,
        "market_impact_evidence_source": "reports/hftbacktest/market_depth_mes_20260616.json",
        "liquidity_taking_max_depth_ratio": 0.15,
        "orders_intended": 1,
        "orders_submitted": 1,
        "orders_acknowledged": 1,
        "orders_cancelled": 1,
        "fills_count": 0,
        "partial_fills_count": 0,
        "unfilled_count": 1,
        "fill_rate": 0.0,
        "avg_queue_position_or_not_available": 2.0,
    }


def _install_recording_hftbacktest(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    class RecordingBacktestAsset:
        def __init__(self) -> None:
            calls.append("BacktestAsset")

        def data(self, _path: str) -> "RecordingBacktestAsset":
            calls.append("asset.data")
            return self

        def tick_size(self, _value: float) -> "RecordingBacktestAsset":
            calls.append("asset.tick_size")
            return self

        def lot_size(self, _value: float) -> "RecordingBacktestAsset":
            calls.append("asset.lot_size")
            return self

        def constant_order_latency(self, *_args: object) -> "RecordingBacktestAsset":
            calls.append("asset.constant_order_latency")
            return self

        def no_partial_fill_exchange(self) -> "RecordingBacktestAsset":
            calls.append("asset.no_partial_fill_exchange")
            return self

        def log_prob_queue_model2(self) -> "RecordingBacktestAsset":
            calls.append("asset.log_prob_queue_model2")
            return self

        def trading_qty_fee_model(self, *_args: object) -> "RecordingBacktestAsset":
            calls.append("asset.trading_qty_fee_model")
            return self

    class RecordingHashMapMarketDepthBacktest:
        def __init__(self, assets: list[RecordingBacktestAsset]) -> None:
            assert len(assets) == 1
            calls.append("HashMapMarketDepthBacktest")
            self.feed_waits = 0
            self.last_order_id = 9001

        def depth(self, *_args: object) -> object:
            calls.append("depth")
            return SimpleNamespace(best_bid=5000.0, best_ask=5000.25, tick_size=0.25)

        def wait_next_feed(self, *_args: object) -> int:
            calls.append("wait_next_feed")
            self.feed_waits += 1
            return 2 if self.feed_waits <= 2 else 1

        def submit_buy_order(self, *_args: object) -> int:
            calls.append("submit_buy_or_sell_order")
            self.last_order_id = int(_args[1])
            return 0

        def submit_sell_order(self, *_args: object) -> int:
            calls.append("submit_buy_or_sell_order")
            self.last_order_id = int(_args[1])
            return 0

        def wait_order_response(self, *_args: object) -> int:
            calls.append("wait_order_response")
            return 0

        def orders(self, *_args: object) -> dict[int, dict[str, object]]:
            calls.append("orders")
            return {
                self.last_order_id: {
                    "status": 1,
                    "qty": 1.0,
                    "leaves_qty": 1.0,
                    "exec_qty": 0.0,
                    "price": 5000.0,
                    "exec_price": 0.0,
                    "exch_timestamp": 1_000_000_000,
                    "local_timestamp": 1_000_000_100,
                }
            }

        def state_values(self, *_args: object) -> dict[str, float]:
            calls.append("state_values")
            return {"position": 0.0, "balance": 0.0, "fee": 0.0}

        def cancel(self, *_args: object) -> int:
            calls.append("cancel")
            return 0

        def clear_inactive_orders(self, *_args: object) -> None:
            calls.append("clear_inactive_orders")

    fake_data = ModuleType("hftbacktest.data")
    fake_data.validate_event_order = lambda _events: None  # type: ignore[attr-defined]
    fake_types = ModuleType("hftbacktest.types")
    event_dtype, constants = _event_contract()
    fake_types.event_dtype = event_dtype  # type: ignore[attr-defined]
    fake_types.EXCH_EVENT = constants["EXCH_EVENT"]  # type: ignore[attr-defined]
    fake_types.LOCAL_EVENT = constants["LOCAL_EVENT"]  # type: ignore[attr-defined]
    fake_types.DEPTH_EVENT = constants["DEPTH_EVENT"]  # type: ignore[attr-defined]
    fake_types.ADD_ORDER_EVENT = 10  # type: ignore[attr-defined]
    fake_types.CANCEL_ORDER_EVENT = 11  # type: ignore[attr-defined]
    fake_types.MODIFY_ORDER_EVENT = 12  # type: ignore[attr-defined]
    fake_types.FILL_EVENT = 13  # type: ignore[attr-defined]
    fake_order = ModuleType("hftbacktest.order")
    fake_order.GTC = 0  # type: ignore[attr-defined]
    fake_order.LIMIT = 0  # type: ignore[attr-defined]
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.BacktestAsset = RecordingBacktestAsset  # type: ignore[attr-defined]
    fake_pkg.HashMapMarketDepthBacktest = RecordingHashMapMarketDepthBacktest  # type: ignore[attr-defined]
    fake_pkg.data = fake_data  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    fake_pkg.order = fake_order  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "hftbacktest", fake_pkg)
    monkeypatch.setitem(sys.modules, "hftbacktest.data", fake_data)
    monkeypatch.setitem(sys.modules, "hftbacktest.types", fake_types)
    monkeypatch.setitem(sys.modules, "hftbacktest.order", fake_order)
    builder_module = sys.modules.get("backtest_pipeline.src.hft_backtest_builder")
    if builder_module is not None:
        monkeypatch.setattr(builder_module, "BacktestAsset", RecordingBacktestAsset, raising=False)
        monkeypatch.setattr(
            builder_module,
            "HashMapMarketDepthBacktest",
            RecordingHashMapMarketDepthBacktest,
            raising=False,
        )
        monkeypatch.setattr(builder_module, "event_dtype", event_dtype, raising=False)
        monkeypatch.setattr(builder_module, "DEPTH_EVENT", constants["DEPTH_EVENT"], raising=False)
        monkeypatch.setattr(builder_module, "ADD_ORDER_EVENT", fake_types.ADD_ORDER_EVENT, raising=False)
        monkeypatch.setattr(builder_module, "CANCEL_ORDER_EVENT", fake_types.CANCEL_ORDER_EVENT, raising=False)
        monkeypatch.setattr(builder_module, "MODIFY_ORDER_EVENT", fake_types.MODIFY_ORDER_EVENT, raising=False)
        monkeypatch.setattr(builder_module, "FILL_EVENT", fake_types.FILL_EVENT, raising=False)
    return calls


@pytest.fixture()
def hbt4_contract(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    event_dtype, constants = _event_contract()
    monkeypatch.setattr(hbt4, "_expected_event_dtype", lambda: event_dtype)
    monkeypatch.setattr(hbt4, "_event_constants", lambda: constants)
    monkeypatch.setattr(hbt4, "_repo_commit", lambda _root: "hft3sha")
    monkeypatch.setattr(hbt4, "_repo_dirty", lambda _root: False)
    monkeypatch.setattr(
        hbt4,
        "detect_hftbacktest_installation",
        lambda: {
            "available": True,
            "python_package_name": "hftbacktest",
            "python_package_version": "2.4.2",
            "installed_module_path": "site-packages/hftbacktest",
        },
    )
    return _install_recording_hftbacktest(monkeypatch)


def _write_valid_inputs(tmp_path: Path, *, include_intent: bool) -> tuple[Path, Path, Path, Path]:
    screening_path = _write_json(
        tmp_path / "screening_artifact.json",
        _screening_artifact(include_intent=include_intent),
    )
    data_path = _write_valid_l2_npz(tmp_path / "valid_l2_hbt4.npz")
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", _valid_l2_fill_queue_model())
    return screening_path, data_path, latency_path, fill_queue_path


def test_hbt4_valid_order_intent_writes_official_replay_and_can_pass(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    screening_path, data_path, latency_path, fill_queue_path = _write_valid_inputs(
        tmp_path,
        include_intent=True,
    )
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_pass"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_pass",
    )

    official_replay_path = out_dir / "official_replay.json"
    assert official_replay_path.is_file(), "HBT-4 must write official_replay.json after official HftBacktest execution"
    official_replay = json.loads(official_replay_path.read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert official_replay["official_hftbacktest_replay_status"] == "pass"
    assert official_replay["accelerated_mode"] is False
    assert summary["official_hftbacktest_replay_status"] == "pass"
    assert summary["accelerated_mode"] is False
    assert summary["accuracy_tradeoff_declared"] is False
    assert summary["queue_position_modeled"] is True
    assert summary["order_response_latency_modeled"] is True
    assert summary["full_replay_comparison_hash_or_not_run"] == official_replay["official_replay_artifact_hash"]
    assert summary["certification_allowed"] is True
    assert summary["fail_closed_reasons"] == []
    assert summary["replay_realism_status"] == "pass"

    for expected_call in (
        "BacktestAsset",
        "HashMapMarketDepthBacktest",
        "wait_next_feed",
        "submit_buy_or_sell_order",
        "wait_order_response",
        "orders",
        "state_values",
        "cancel",
    ):
        assert expected_call in official_replay["api_calls"], f"missing official HftBacktest API call: {expected_call}"
        assert expected_call in hbt4_contract


def test_hbt4_non_hash_native_evidence_cannot_leave_certification_allowed(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    screening_path, data_path, latency_path, fill_queue_path = _write_valid_inputs(
        tmp_path,
        include_intent=True,
    )
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_non_hash_native_evidence"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[
            "reports/latency_baselines/order_ack_campaign_20260611T072116Z_summary.json"
        ],
        run_id="hbt4_non_hash_native_evidence",
    )
    summary = payload["replay_summary"]

    assert summary["official_hftbacktest_replay_status"] == "pass"
    assert "pass_requires_hash_backed_native_cpp_hot_path_evidence" in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] == "fail"
    assert summary["certification_allowed"] is False
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt4_non_rust_vectorbt_handoff_cannot_pass(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    artifact = _screening_artifact(include_intent=True)
    artifact["vectorbt_engine"] = "numba"
    artifact["engine_parity_status"] = "rust_unavailable_pilot_only"
    artifact["rust_engine_required_for_scope"] = False
    artifact["rust_engine_available"] = False
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_json(tmp_path / "screening_artifact.json", artifact)
    data_path = _write_valid_l2_npz(tmp_path / "valid_l2_hbt4.npz")
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", _valid_l2_fill_queue_model())
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_non_rust_vectorbt"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_non_rust_vectorbt",
    )

    summary = payload["replay_summary"]

    assert summary["official_hftbacktest_replay_status"] == "pass"
    assert summary["replay_realism_status"] != "pass"
    assert "screening_artifact_hbt_pass_requires_rust_vectorbt" in summary["fail_closed_reasons"]
    assert "screening_artifact_hbt_pass_requires_rust_engine_available" in summary["fail_closed_reasons"]
    assert "submit_buy_or_sell_order" in hbt4_contract


@pytest.mark.parametrize(
    ("fill_queue_overrides", "expected_reason"),
    [
        (
            {
                "exchange_model": "PartialFillExchange",
                "exchange_model_source": "asset.partial_fill_exchange",
                "partial_fill_policy": "partial_fill",
            },
            "official_replay_unsupported_exchange_model",
        ),
        (
            {
                "queue_model": "RiskAverseQueueModel",
                "queue_model_source": "asset.risk_adverse_queue_model",
            },
            "official_replay_unsupported_queue_model",
        ),
        (
            {
                "maker_fee": -0.0002,
                "taker_fee": 0.0007,
            },
            "official_replay_unsupported_fee_model",
        ),
        (
            {"time_in_force_policy": "gtc_leave_resting"},
            "official_replay_unsupported_time_in_force_policy",
        ),
        (
            {"minimum_order_qty": 2.0},
            "official_replay_order_qty_below_minimum",
        ),
    ],
)
def test_hbt4_unsupported_official_replay_contract_fails_closed_before_submit(
    tmp_path: Path,
    hbt4_contract: list[str],
    fill_queue_overrides: dict[str, object],
    expected_reason: str,
) -> None:
    screening_path, data_path, latency_path, _fill_queue_path = _write_valid_inputs(
        tmp_path,
        include_intent=True,
    )
    fill_queue_model = _valid_l2_fill_queue_model()
    fill_queue_model.update(fill_queue_overrides)
    fill_queue_path = _write_json(tmp_path / "unsupported_fill_queue_model.json", fill_queue_model)
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_contract_mismatch"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_contract_mismatch",
    )

    official_replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert official_replay["official_hftbacktest_replay_status"] == "not_run"
    assert expected_reason in official_replay["official_replay_contract_validation_reasons"]
    assert expected_reason in summary["fail_closed_reasons"]
    assert summary["replay_realism_status"] != "pass"
    assert "submit_buy_or_sell_order" not in hbt4_contract
    assert "asset.no_partial_fill_exchange" not in hbt4_contract


def test_hbt4_stale_robustness_handoff_fails_closed_even_with_official_replay(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    artifact = _screening_artifact(include_intent=True)
    artifact["promoted"][0]["robustness_artifact_staleness"] = "stale"
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_json(tmp_path / "screening_artifact.json", artifact)
    data_path = _write_valid_l2_npz(tmp_path / "valid_l2_hbt4.npz")
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", _valid_l2_fill_queue_model())
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_stale_robustness"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_stale_robustness",
    )

    official_replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]

    assert official_replay["official_hftbacktest_replay_status"] == "pass"
    assert summary["replay_realism_status"] != "pass"
    assert (
        "screening_artifact_replay_ineligible:robustness_artifact_stale_or_invalid"
        in summary["fail_closed_reasons"]
    )
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt4_status_only_robustness_evidence_fails_closed(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    artifact = _screening_artifact(include_intent=True)
    candidate = artifact["promoted"][0]
    candidate["walk_forward_metrics"] = {"status": "pass"}
    candidate["wfc_metrics"] = {"status": "pass"}
    candidate["dsr_or_not_run"] = "pass"
    candidate["pbo_or_not_run"] = {"status": "pass"}
    artifact["screening_artifact_hash"] = compute_screening_artifact_hash(artifact)
    screening_path = _write_json(tmp_path / "screening_artifact.json", artifact)
    data_path = _write_valid_l2_npz(tmp_path / "valid_l2_hbt4.npz")
    latency_path = _write_json(tmp_path / "latency_model.json", _constant_latency_model())
    fill_queue_path = _write_json(tmp_path / "fill_queue_model.json", _valid_l2_fill_queue_model())
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_status_only_robustness"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_status_only_robustness",
    )

    official_replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    summary = payload["replay_summary"]
    reasons = set(summary["fail_closed_reasons"])

    assert official_replay["official_hftbacktest_replay_status"] == "pass"
    assert summary["replay_realism_status"] != "pass"
    assert "screening_artifact_replay_ineligible:walk_forward_metrics_missing:fold_matrix" in reasons
    assert "screening_artifact_replay_ineligible:wfc_metrics_missing:metric_in_sample" in reasons
    assert "screening_artifact_replay_ineligible:dsr_evidence_malformed" in reasons
    assert "screening_artifact_replay_ineligible:pbo_evidence_missing:pbo_pass" in reasons
    assert "submit_buy_or_sell_order" in hbt4_contract


def test_hbt4_missing_order_intent_fails_closed_without_official_pass(
    tmp_path: Path,
    hbt4_contract: list[str],
) -> None:
    screening_path, data_path, latency_path, fill_queue_path = _write_valid_inputs(
        tmp_path,
        include_intent=False,
    )
    out_dir = tmp_path / "research_cards" / "hftbacktest_realism" / "hbt4_missing_intent"

    payload = hbt4.write_hftbacktest_realism_artifacts(
        repo_root=tmp_path,
        out_dir=out_dir,
        screening_artifact_path=screening_path,
        data_npz_path=data_path,
        latency_model_path=latency_path,
        fill_queue_model_path=fill_queue_path,
        upstream_ref="v2.4.2",
        native_hot_path_evidence=[NATIVE_CPP_LATENCY_EVIDENCE],
        run_id="hbt4_missing_intent",
    )

    summary = payload["replay_summary"]
    official_replay_path = out_dir / "official_replay.json"
    official_replay = (
        json.loads(official_replay_path.read_text(encoding="utf-8"))
        if official_replay_path.is_file()
        else {}
    )

    assert summary["replay_realism_status"] != "pass"
    assert (
        "hbt4_order_intent_missing" in summary["fail_closed_reasons"]
        or official_replay.get("official_hftbacktest_replay_status") == "not_run"
    ), "missing hbt4_order_intent must fail closed or mark official replay not_run"
    assert "submit_buy_or_sell_order" not in hbt4_contract
