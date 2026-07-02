from __future__ import annotations

from datetime import datetime, timezone
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest

from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyPipelineError,
    HftBacktestOnlyPrepareConfig,
    HftBacktestOnlyRunConfig,
    _save_npz_atomic,
    prepare_hftbacktest_only_l3_from_lake,
    run_hftbacktest_only,
    validate_hftbacktest_only_input,
    write_promotion_decision,
)
from backtest_pipeline.src.ontology_gate import validate_artifact_schema


def _event_contract() -> tuple[np.dtype, dict[str, int]]:
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
    constants = {
        "EXCH_EVENT": 1 << 8,
        "LOCAL_EVENT": 1 << 9,
        "ADD_ORDER_EVENT": 10,
        "DEPTH_EVENT": 1,
        "TRADE_EVENT": 2,
        "CANCEL_ORDER_EVENT": 11,
        "MODIFY_ORDER_EVENT": 12,
        "FILL_EVENT": 13,
        "BUY_EVENT": 1 << 10,
        "SELL_EVENT": 1 << 11,
    }
    return dtype, constants


def _write_valid_l3_npz(
    path: Path,
    *,
    base_exch_ts: int = 1_000_000_000,
    include_timestamp_units: bool = True,
    include_trade_event: bool = False,
    include_side_flags: bool = True,
    ambiguous_side_flags: bool = False,
) -> Path:
    dtype, constants = _event_contract()
    if ambiguous_side_flags:
        buy_add_event = constants["ADD_ORDER_EVENT"] | constants["BUY_EVENT"] | constants["SELL_EVENT"]
        sell_add_event = buy_add_event
    else:
        buy_add_event = constants["ADD_ORDER_EVENT"] | constants["BUY_EVENT"] if include_side_flags else constants["ADD_ORDER_EVENT"]
        sell_add_event = constants["ADD_ORDER_EVENT"] | constants["SELL_EVENT"] if include_side_flags else constants["ADD_ORDER_EVENT"]
    rows = [
        {
            "ev": buy_add_event | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
            "exch_ts": base_exch_ts,
            "local_ts": base_exch_ts + 100,
            "px": 5000.0,
            "qty": 1.0,
            "order_id": 1001,
            "ival": 0,
            "fval": 0.0,
        },
        {
            "ev": sell_add_event | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
            "exch_ts": base_exch_ts + 500,
            "local_ts": base_exch_ts + 600,
            "px": 5000.25,
            "qty": 1.0,
            "order_id": 1002,
            "ival": 0,
            "fval": 0.0,
        },
    ]
    if include_trade_event:
        rows.append(
            {
                "ev": constants["TRADE_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"],
                "exch_ts": base_exch_ts + 750,
                "local_ts": base_exch_ts + 850,
                "px": 5000.25,
                "qty": 1.0,
                "order_id": 1002,
                "ival": 0,
                "fval": 0.0,
            }
        )
    events = np.zeros(len(rows), dtype=dtype)
    for index, row in enumerate(rows):
        for field, value in row.items():
            events[index][field] = value
    payload = {"data": events}
    if include_timestamp_units:
        payload["timestamp_units"] = "nanoseconds"
    np.savez_compressed(path, **payload)
    return path


def _write_invalid_npz(path: Path) -> Path:
    dtype, constants = _event_contract()
    events = np.zeros(1, dtype=dtype)
    events[0]["ev"] = (
        constants["ADD_ORDER_EVENT"]
        | constants["BUY_EVENT"]
        | constants["EXCH_EVENT"]
        | constants["LOCAL_EVENT"]
    )
    events[0]["exch_ts"] = 1_000_000_000
    events[0]["local_ts"] = 999_999_999
    events[0]["order_id"] = 1001
    np.savez_compressed(path, data=events)
    return path


def _config(
    tmp_path: Path,
    data_path: Path,
    snapshot_path: Path,
    *,
    strategy_id: str = "smoke_limit_order",
    strategy_params: dict[str, object] | None = None,
    canonical_model_id: str = "SPREAD_BLOWOUT_RECOMPRESSION",
    legacy_aliases: tuple[str, ...] = ("HYP_5",),
    event_window: dict[str, object] | None = None,
) -> HftBacktestOnlyRunConfig:
    return HftBacktestOnlyRunConfig(
        run_id="hbt_only_test",
        symbol="MES",
        contract="MESH6",
        event_id="CPI_2024_09_11_TIGHT",
        normalized_npz=data_path,
        initial_snapshot=snapshot_path,
        strategy_id=strategy_id,
        strategy_params=strategy_params or {"side": "BUY", "quantity": 1.0, "max_steps": 2},
        canonical_model_id=canonical_model_id,
        legacy_aliases=legacy_aliases,
        authority_refs=("test:model_registry",),
        tick_size=0.25,
        lot_size=1.0,
        contract_size=5.0,
        maker_fee=0.47,
        taker_fee=0.47,
        entry_latency_ns=100_000,
        response_latency_ns=100_000,
        event_window=event_window or {},
    )


def _install_fake_hftbacktest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    passive_fill_after_elapse: bool = False,
    wait_response_code: int = 0,
    successful_elapses: int = 2,
) -> None:
    dtype, constants = _event_contract()

    class RecordingAsset:
        def data(self, _path: str) -> "RecordingAsset":
            return self

        def initial_snapshot(self, _path: str) -> "RecordingAsset":
            return self

        def linear_asset(self, _value: float) -> "RecordingAsset":
            return self

        def tick_size(self, _value: float) -> "RecordingAsset":
            return self

        def lot_size(self, _value: float) -> "RecordingAsset":
            return self

        def constant_order_latency(self, _entry: int, _response: int) -> "RecordingAsset":
            return self

        def no_partial_fill_exchange(self) -> "RecordingAsset":
            return self

        def l3_fifo_queue_model(self) -> "RecordingAsset":
            return self

        def trading_qty_fee_model(self, _maker: float, _taker: float) -> "RecordingAsset":
            return self

    class RecordingOrderDict:
        def __init__(self, order_id: int, *, filled: bool) -> None:
            self.order_id = order_id
            self.filled = filled

        def get(self, order_id: int) -> dict[str, object] | None:
            if order_id != self.order_id:
                return None
            exec_qty = 1.0 if self.filled else 0.0
            return {
                "status": 1,
                "qty": 1.0,
                "leaves_qty": 0.0 if self.filled else 1.0,
                "exec_qty": exec_qty,
                "price": 5000.0,
                "exec_price": 5000.0 if self.filled else 0.0,
                "exch_timestamp": 1_000_000_000,
                "local_timestamp": 1_000_000_100,
            }

    class RecordingBacktest:
        def __init__(self, _assets: list[RecordingAsset]) -> None:
            self.steps = 0
            self.order_id = 0
            self.current_timestamp = 0

        def elapse(self, _interval_ns: int) -> int:
            self.steps += 1
            self.current_timestamp += int(_interval_ns)
            return 0 if self.steps <= successful_elapses else 1

        def clear_inactive_orders(self, _asset_no: int) -> None:
            return None

        def depth(self, _asset_no: int) -> object:
            return SimpleNamespace(best_bid=5000.0, best_ask=5000.25, tick_size=0.25)

        def state_values(self, _asset_no: int) -> dict[str, float]:
            if passive_fill_after_elapse and self.order_id and self.steps >= 2:
                return {"position": 1.0, "balance": 12.5, "fee": 0.47}
            return {"position": 0.0, "balance": 0.0, "fee": 0.0}

        def submit_buy_order(self, _asset_no: int, order_id: int, *_args: object) -> int:
            self.order_id = order_id
            return 0

        def submit_sell_order(self, _asset_no: int, order_id: int, *_args: object) -> int:
            self.order_id = order_id
            return 0

        def wait_order_response(self, *_args: object) -> int:
            return wait_response_code

        def orders(self, _asset_no: int) -> RecordingOrderDict:
            filled = passive_fill_after_elapse and self.order_id != 0 and self.steps >= 2
            return RecordingOrderDict(self.order_id, filled=filled)

        def cancel(self, *_args: object) -> int:
            return 0

    fake_data = ModuleType("hftbacktest.data")
    fake_data.validate_event_order = lambda _events: None  # type: ignore[attr-defined]
    fake_types = ModuleType("hftbacktest.types")
    fake_types.event_dtype = dtype  # type: ignore[attr-defined]
    for key, value in constants.items():
        setattr(fake_types, key, value)
    fake_order = ModuleType("hftbacktest.order")
    fake_order.GTC = 0  # type: ignore[attr-defined]
    fake_order.LIMIT = 0  # type: ignore[attr-defined]
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.BacktestAsset = RecordingAsset  # type: ignore[attr-defined]
    fake_pkg.HashMapMarketDepthBacktest = RecordingBacktest  # type: ignore[attr-defined]
    fake_pkg.data = fake_data  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    fake_pkg.order = fake_order  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hftbacktest", fake_pkg)
    monkeypatch.setitem(sys.modules, "hftbacktest.data", fake_data)
    monkeypatch.setitem(sys.modules, "hftbacktest.types", fake_types)
    monkeypatch.setitem(sys.modules, "hftbacktest.order", fake_order)


def test_active_hftbacktest_only_run_writes_outputs_without_vectorbt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    sys.modules.pop("backtest_pipeline.src.vectorbt_adapter", None)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "hbt_only_test"

    result = run_hftbacktest_only(_config(tmp_path, data_path, snapshot_path), out_dir=out_dir)

    assert result["status"] == "completed"
    assert (out_dir / "run_manifest.json").is_file()
    assert (out_dir / "recorder_result.npz").is_file()
    assert (out_dir / "stats_summary.json").is_file()
    assert (out_dir / "promotion_decision.json").is_file()
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    decision = json.loads((out_dir / "promotion_decision.json").read_text(encoding="utf-8"))
    assert manifest["active_path"] == "hftbacktest_only"
    assert manifest["legacy_screening_dependency"] == "forbidden_active_path"
    assert manifest["canonical_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert manifest["legacy_aliases"] == ["HYP_5"]
    assert stats["canonical_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert decision["canonical_model_id"] == "SPREAD_BLOWOUT_RECOMPRESSION"
    assert decision["promotion_allowed"] is False
    assert "backtest_pipeline.src.vectorbt_adapter" not in sys.modules


def test_active_hftbacktest_only_run_records_passive_fill_after_later_elapse(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(
        monkeypatch,
        passive_fill_after_elapse=True,
        wait_response_code=3,
    )
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "hbt_passive_fill"

    result = run_hftbacktest_only(_config(tmp_path, data_path, snapshot_path), out_dir=out_dir)

    assert result["status"] == "completed"
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    recorder = np.load(out_dir / "recorder_result.npz")
    assert stats["fills_count"] == 1
    assert stats["fill_rate"] == 1.0
    assert stats["unfilled_count"] == 0
    assert stats["orders_acknowledged"] == 1
    assert stats["net_pnl"] == 12.5
    assert recorder["fill_quantities"].tolist() == [1.0]


def test_hbt_loop_uses_holding_period_bars_after_explicit_step_params(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch, successful_elapses=10)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")

    def max_replay_step(name: str, strategy_params: dict[str, object]) -> int:
        out_dir = tmp_path / "artifacts" / "hbt_runs" / name
        result = run_hftbacktest_only(
            _config(
                tmp_path,
                data_path,
                snapshot_path,
                strategy_params=strategy_params,
                event_window={"cutoff_ts_ns": 0, "end_ts_ns": 10_000_000_000},
            ),
            out_dir=out_dir,
        )
        assert result["status"] == "completed"
        replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
        return max(int(row["step"]) for row in replay["orders"] if "step" in row)

    assert max_replay_step(
        "holding_period",
        {"side": "BUY", "quantity": 1.0, "holding_period_bars": 4},
    ) == 4
    assert max_replay_step(
        "max_feed_steps",
        {"side": "BUY", "quantity": 1.0, "max_feed_steps": 3, "holding_period_bars": 4},
    ) == 3
    assert max_replay_step(
        "max_steps",
        {"side": "BUY", "quantity": 1.0, "max_steps": 2, "max_feed_steps": 3, "holding_period_bars": 4},
    ) == 2


def test_hypothesis_limit_order_scans_event_window_before_holding_period(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline

    _install_fake_hftbacktest(monkeypatch, successful_elapses=10)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "event_scan_late_signal"

    def late_signal_lookup(_config: object, _params: object):
        def lookup(timestamp_ns: int | None) -> float:
            return 1.0 if int(timestamp_ns or 0) >= 4_000_000_000 else 0.0

        return lookup, {
            "adapter_status": "available",
            "signal_observations": 8,
            "signal_source": "test_late_signal",
            "signal_field": "hypothesis.evaluate",
            "feature_backend": "cpp",
            "signal_min": 0.0,
            "signal_max": 1.0,
            "signal_abs_max": 1.0,
        }, []

    monkeypatch.setattr(pipeline, "_build_model_signal_lookup", late_signal_lookup)
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "SECOND_WAVE_CONTINUATION",
            "quantity": 1.0,
            "holding_period_bars": 1,
            "signal_threshold": 0.5,
        },
        canonical_model_id="SECOND_WAVE_CONTINUATION",
        legacy_aliases=("HYP_1",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 8_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed"
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    submitted = [row for row in replay["orders"] if row["event_type"] == "ORDER_SUBMITTED"]
    assert replay["entry_scan_steps"] == 8
    assert replay["max_loop_steps"] == 9
    assert replay["holding_period_bars"] == 1
    assert replay["strategy_surface_version"] == pipeline.HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION
    assert replay["signal_abs_max"] == 1.0
    assert submitted
    assert submitted[0]["step"] >= 3


def test_hypothesis_limit_order_evaluates_canonical_model_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "hypothesis_signal"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "SECOND_WAVE_CONTINUATION",
            "quantity": 1.0,
            "max_steps": 3,
            "signal_threshold": 0.01,
        },
        canonical_model_id="SECOND_WAVE_CONTINUATION",
        legacy_aliases=("HYP_1",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 3_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed"
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    submitted = [row for row in replay["orders"] if row["event_type"] == "ORDER_SUBMITTED"]
    assert replay["strategy_adapter_status"] == "available"
    assert replay["signal_source"] == "hbt_normalized_mbo_market_state_pipeline"
    assert replay["signal_observations"] > 0
    assert submitted
    assert submitted[0]["side"] in {"BUY", "SELL"}
    assert isinstance(submitted[0]["signal"], float)
    assert stats["canonical_model_id"] == "SECOND_WAVE_CONTINUATION"


def test_signal_below_threshold_fails_closed_without_promotion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "no_signal_order"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "SECOND_WAVE_CONTINUATION",
            "quantity": 1.0,
            "max_steps": 3,
            "signal_threshold": 999.0,
        },
        canonical_model_id="SECOND_WAVE_CONTINUATION",
        legacy_aliases=("HYP_1",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 3_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "pipeline_blocker"
    assert "pipeline_blocker:no_hbt_order_submitted" in result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["orders_submitted"] == 0
    assert replay["no_order_observation"] == "strategy_signal_below_threshold_or_no_directional_order"
    assert not (out_dir / "recorder_result.npz").exists()
    assert not (out_dir / "stats_summary.json").exists()
    assert not (out_dir / "promotion_decision.json").exists()


def test_hypothesis_limit_order_requires_event_window_for_entry_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "missing_event_window"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "SECOND_WAVE_CONTINUATION",
            "quantity": 1.0,
            "holding_period_bars": 5,
            "signal_threshold": 0.01,
        },
        canonical_model_id="SECOND_WAVE_CONTINUATION",
        legacy_aliases=("HYP_1",),
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "pipeline_blocker"
    assert "pipeline_blocker:event_window_required_for_entry_scan" in result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["official_hftbacktest_replay_status"] == "not_run"
    assert replay["strategy_surface_version"] == pipeline.HYPOTHESIS_LIMIT_ORDER_SURFACE_VERSION
    assert replay["signal_observations"] == 0
    assert not (out_dir / "recorder_result.npz").exists()


def test_structural_limit_order_evaluates_canonical_payload_signal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "structural_signal"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "BOOK_PRESSURE",
            "quantity": 1.0,
            "max_steps": 3,
            "signal_threshold": 0.01,
        },
        canonical_model_id="BOOK_PRESSURE",
        legacy_aliases=("PDF_MODEL_1",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 3_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed"
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    submitted = [row for row in replay["orders"] if row["event_type"] == "ORDER_SUBMITTED"]
    assert replay["strategy_adapter_status"] == "available"
    assert replay["signal_source"] == "hbt_normalized_mbo_structural_integrator"
    assert set(replay["signal_field"].split(",")) <= {"OFI_zscore", "OFI_smooth", "book_pressure_direction"}
    assert replay["signal_field"]
    assert replay["signal_observations"] > 0
    assert submitted
    assert submitted[0]["side"] in {"BUY", "SELL"}
    assert isinstance(submitted[0]["signal"], float)
    assert stats["canonical_model_id"] == "BOOK_PRESSURE"


def test_missing_uniform_hbt_adapter_is_pipeline_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "missing_adapter"

    def missing_adapter(_model_id: str) -> tuple[str, object, str]:
        raise HftBacktestOnlyPipelineError("pipeline_blocker:missing_uniform_hbt_adapter")

    monkeypatch.setattr(pipeline, "_canonical_signal_adapter", missing_adapter)
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={"model_id": "BOOK_PRESSURE", "quantity": 1.0, "max_steps": 3},
        canonical_model_id="BOOK_PRESSURE",
        legacy_aliases=("PDF_MODEL_1",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 3_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "pipeline_blocker"
    assert "pipeline_blocker:missing_uniform_hbt_adapter" in result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["strategy_adapter_status"] == "missing_uniform_hbt_adapter"
    assert replay["signal_observations"] == 0
    assert replay["signal_source"] == "none"
    assert validate_artifact_schema(replay, artifact_type="replay").valid is True
    assert not (out_dir / "recorder_result.npz").exists()
    assert not (out_dir / "stats_summary.json").exists()
    assert not (out_dir / "promotion_decision.json").exists()


def test_structural_model_without_signal_fields_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz", include_trade_event=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "empty_structural_signal"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "QUANTUM_SPREAD_DEFENSE",
            "quantity": 1.0,
            "max_steps": 3,
        },
        canonical_model_id="QUANTUM_SPREAD_DEFENSE",
        legacy_aliases=("PDF_MODEL_9",),
        event_window={"cutoff_ts_ns": 0, "end_ts_ns": 3_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "pipeline_blocker"
    assert "pipeline_blocker:missing_uniform_hbt_adapter" in result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["strategy_adapter_status"] == "missing_uniform_hbt_adapter"
    assert replay["signal_observations"] == 0
    assert replay["signal_source"] == "none"
    assert not (out_dir / "recorder_result.npz").exists()
    assert not (out_dir / "stats_summary.json").exists()


def test_save_npz_atomic_avoids_multi_dot_with_suffix(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_with_suffix = Path.with_suffix

    def strict_with_suffix(self: Path, suffix: str) -> Path:
        if suffix.count(".") > 1:
            raise ValueError("Invalid suffix")
        return original_with_suffix(self, suffix)

    monkeypatch.setattr(Path, "with_suffix", strict_with_suffix)
    out_path = tmp_path / "prepared.sample.npz"

    _save_npz_atomic(out_path, data=np.array([1, 2, 3], dtype=np.int64))

    with np.load(out_path, allow_pickle=False) as payload:
        assert payload["data"].tolist() == [1, 2, 3]
    assert not (tmp_path / "prepared.sample.npz.tmp").exists()
    assert not (tmp_path / "prepared.sample.npz.tmp.npz").exists()


def test_invalid_data_does_not_write_promotion_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_invalid_npz(tmp_path / "invalid_event.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "invalid"

    result = run_hftbacktest_only(_config(tmp_path, data_path, snapshot_path), out_dir=out_dir)

    assert result["status"] == "data_invalid"
    assert (out_dir / "data_validation.json").is_file()
    assert not (out_dir / "recorder_result.npz").exists()
    assert not (out_dir / "stats_summary.json").exists()
    assert not (out_dir / "promotion_decision.json").exists()
    validation = json.loads((out_dir / "data_validation.json").read_text(encoding="utf-8"))
    assert "TIMESTAMP_UNITS_UNPROVEN" in validation["fail_closed_reasons"]
    assert "NEGATIVE_FEED_LATENCY_UNCORRECTED" in validation["fail_closed_reasons"]


def test_promotion_decision_requires_hbt_outputs(tmp_path: Path) -> None:
    with pytest.raises(HftBacktestOnlyPipelineError, match="promotion_decision_requires"):
        write_promotion_decision(tmp_path)


def test_validation_records_l3_hftbacktest_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot.npz")

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "pass"
    assert validation["data_contract_version"] == "hft3_hbt_l3_side_flags_v1"
    assert validation["dtype_exact_match"] is True
    assert validation["timestamp_units"] == "nanoseconds"
    assert validation["official_validate_event_order_status"] == "pass"
    assert validation["l2_l3_classification"] == "l3_mbo"


def test_validation_rejects_l3_add_rows_without_side_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3_no_side.npz", include_side_flags=False)
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot_no_side.npz", include_side_flags=False)

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "fail"
    assert validation["l2_l3_classification"] == "l3_mbo"
    assert "L3_SIDE_FLAG_MISSING_OR_AMBIGUOUS" in validation["fail_closed_reasons"]
    assert "INITIAL_SNAPSHOT_L3_SIDE_FLAG_MISSING_OR_AMBIGUOUS" in validation["fail_closed_reasons"]


def test_validation_rejects_l3_add_rows_with_ambiguous_side_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3_ambiguous_side.npz", ambiguous_side_flags=True)
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot_ambiguous_side.npz", ambiguous_side_flags=True)

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "fail"
    assert "L3_SIDE_FLAG_MISSING_OR_AMBIGUOUS" in validation["fail_closed_reasons"]
    assert "INITIAL_SNAPSHOT_L3_SIDE_FLAG_MISSING_OR_AMBIGUOUS" in validation["fail_closed_reasons"]


def test_validation_accepts_converted_lake_l3_with_trades_and_inferred_ns(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    data_path = _write_valid_l3_npz(
        tmp_path / "event_l3_lake.npz",
        base_exch_ts=base_ns,
        include_timestamp_units=False,
        include_trade_event=True,
    )
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot.npz", base_exch_ts=base_ns)

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "pass"
    assert validation["timestamp_units"] == "nanoseconds"
    assert validation["l2_l3_classification"] == "l3_mbo"
    assert "TIMESTAMP_UNITS_UNPROVEN" not in validation["fail_closed_reasons"]
    assert "L2_L3_MISMATCH" not in validation["fail_closed_reasons"]


def test_prepare_lake_source_builds_snapshot_and_replay_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    dtype, constants = _event_contract()
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    def row(event_type: int, offset_ns: int, order_id: int, price: float, qty: float = 1.0) -> dict[str, object]:
        ts = base_ns + offset_ns
        ev = event_type | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"]
        if event_type == constants["ADD_ORDER_EVENT"]:
            ev |= constants["BUY_EVENT"] if order_id % 2 else constants["SELL_EVENT"]
        return {
            "ev": ev,
            "exch_ts": ts,
            "local_ts": ts + 100,
            "px": price,
            "qty": qty,
            "order_id": order_id,
            "ival": 0,
            "fval": 0.0,
        }

    source_rows = [
        row(constants["ADD_ORDER_EVENT"], 0, 1001, 5000.0),
        row(constants["MODIFY_ORDER_EVENT"], 100, 1001, 5000.25),
        row(constants["ADD_ORDER_EVENT"], 200, 1002, 5001.0),
        row(constants["CANCEL_ORDER_EVENT"], 300, 1002, 5001.0),
        row(constants["CANCEL_ORDER_EVENT"], 1_000_000_000, 1001, 5000.25),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_100, 1003, 5000.0),
        row(constants["MODIFY_ORDER_EVENT"], 1_000_000_200, 1003, 5000.25),
        row(constants["TRADE_EVENT"], 1_000_000_300, 1003, 5000.25),
        row(constants["FILL_EVENT"], 1_000_000_400, 1003, 5000.25),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_500, 1004, 5001.0),
        row(constants["FILL_EVENT"], 1_000_000_600, 1004, 5001.0),
        row(constants["CANCEL_ORDER_EVENT"], 1_000_000_700, 1004, 5001.0),
        row(constants["TRADE_EVENT"], 1_000_000_800, 9999, 5002.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_900, 0, 5002.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_001_000, 1005, 5003.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_001_100, 1005, 5003.25),
    ]
    source = tmp_path / "lake_source.npz"
    events = np.zeros(len(source_rows), dtype=dtype)
    for index, source_row in enumerate(source_rows):
        for field, value in source_row.items():
            events[index][field] = value
    np.savez_compressed(source, data=events)

    prepared = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="MES",
            contract="MESU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
            replay_mode="added_orders_only",
        )
    )

    with np.load(prepared["initial_snapshot"], allow_pickle=False) as payload:
        snapshot = payload["data"]
    assert len(snapshot) == 1
    assert int(snapshot[0]["order_id"]) == 1001
    assert int(snapshot[0]["ev"]) & 0xFF == constants["ADD_ORDER_EVENT"]
    assert int(snapshot[0]["ev"]) & constants["BUY_EVENT"] == constants["BUY_EVENT"]
    assert int(snapshot[0]["exch_ts"]) == base_ns + 1_000_000_000 - 1
    assert float(snapshot[0]["px"]) == 5000.25

    with np.load(prepared["normalized_npz"], allow_pickle=False) as payload:
        replay = payload["data"]
        assert str(payload["timestamp_units"]) == "nanoseconds"
    assert [(int(row["ev"]) & 0xFF, int(row["order_id"])) for row in replay] == [
        (constants["ADD_ORDER_EVENT"], 1003),
        (constants["MODIFY_ORDER_EVENT"], 1003),
        (constants["TRADE_EVENT"], 1003),
        (constants["ADD_ORDER_EVENT"], 1004),
        (constants["FILL_EVENT"], 1004),
        (constants["ADD_ORDER_EVENT"], 1005),
    ]
    assert prepared["dropped_rows"] == {
        "preexisting_or_unknown_lifecycle": 4,
        "duplicate_add": 1,
        "zero_order_id": 1,
    }

    validation = validate_hftbacktest_only_input(
        _config(tmp_path, Path(prepared["normalized_npz"]), Path(prepared["initial_snapshot"]))
    )
    assert validation["data_validation_status"] == "pass"
    assert validation["l2_l3_classification"] == "l3_mbo"

    reused = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="MES",
            contract="MESU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
            replay_mode="added_orders_only",
        )
    )
    assert reused["reused_existing"] is True


def test_prepare_lake_source_preserves_add_side_through_modify_snapshot(tmp_path: Path) -> None:
    from hftbacktest.types import (
        ADD_ORDER_EVENT,
        BUY_EVENT,
        EXCH_EVENT,
        LOCAL_EVENT,
        MODIFY_ORDER_EVENT,
        SELL_EVENT,
        event_dtype,
    )

    dtype = event_dtype
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    def row(event_type: int, side_event: int, offset_ns: int, order_id: int, price: float) -> dict[str, object]:
        ts = base_ns + offset_ns
        return {
            "ev": event_type | side_event | EXCH_EVENT | LOCAL_EVENT,
            "exch_ts": ts,
            "local_ts": ts + 100,
            "px": price,
            "qty": 1.0,
            "order_id": order_id,
            "ival": 0,
            "fval": 0.0,
        }

    source_rows = [
        row(ADD_ORDER_EVENT, BUY_EVENT, 0, 1001, 5000.0),
        row(MODIFY_ORDER_EVENT, SELL_EVENT, 100, 1001, 5000.25),
        row(ADD_ORDER_EVENT, SELL_EVENT, 1_000_000_100, 1002, 5001.0),
    ]
    source = tmp_path / "side_flip_source.npz"
    events = np.zeros(len(source_rows), dtype=dtype)
    for index, source_row in enumerate(source_rows):
        for field, value in source_row.items():
            events[index][field] = value
    np.savez_compressed(source, data=events)

    prepared = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="MES",
            contract="MESU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
        )
    )

    with np.load(prepared["initial_snapshot"], allow_pickle=False) as payload:
        snapshot = payload["data"]
    assert len(snapshot) == 1
    assert int(snapshot[0]["order_id"]) == 1001
    assert int(snapshot[0]["ev"]) & BUY_EVENT == BUY_EVENT
    assert int(snapshot[0]["ev"]) & SELL_EVENT == 0


def test_prepare_lake_source_default_preserves_full_l3_replay(tmp_path: Path) -> None:
    dtype, constants = _event_contract()
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    def row(event_type: int, offset_ns: int, order_id: int, price: float) -> dict[str, object]:
        ts = base_ns + offset_ns
        ev = event_type | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"]
        if event_type == constants["ADD_ORDER_EVENT"]:
            ev |= constants["BUY_EVENT"] if order_id % 2 else constants["SELL_EVENT"]
        return {
            "ev": ev,
            "exch_ts": ts,
            "local_ts": ts + 100,
            "px": price,
            "qty": 1.0,
            "order_id": order_id,
            "ival": 0,
            "fval": 0.0,
        }

    source_rows = [
        row(constants["ADD_ORDER_EVENT"], 0, 1001, 5000.0),
        row(constants["CANCEL_ORDER_EVENT"], 1_000_000_000, 1001, 5000.0),
        row(constants["TRADE_EVENT"], 1_000_000_100, 9999, 5001.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_200, 0, 5001.25),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_300, 1002, 5002.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_400, 1002, 5002.25),
    ]
    source = tmp_path / "full_replay_source.npz"
    events = np.zeros(len(source_rows), dtype=dtype)
    for index, source_row in enumerate(source_rows):
        for field, value in source_row.items():
            events[index][field] = value
    np.savez_compressed(source, data=events)

    prepared = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="NQ",
            contract="NQU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
        )
    )

    with np.load(prepared["normalized_npz"], allow_pickle=False) as payload:
        replay = payload["data"]
    assert [(int(row["ev"]) & 0xFF, int(row["order_id"])) for row in replay] == [
        (constants["CANCEL_ORDER_EVENT"], 1001),
        (constants["TRADE_EVENT"], 9999),
        (constants["ADD_ORDER_EVENT"], 0),
        (constants["ADD_ORDER_EVENT"], 1002),
        (constants["ADD_ORDER_EVENT"], 1002),
    ]
    assert prepared["status"] == "hbt_full_l3_event_replay_prepared"
    assert prepared["replay_mode"] == "full_l3_event_replay"
    assert prepared["dropped_rows"] == {
        "preexisting_or_unknown_lifecycle": 0,
        "duplicate_add": 0,
        "zero_order_id": 0,
    }
    assert "Not full-market replay" not in prepared["caveat"]


def test_prepare_lake_source_keeps_partial_trade_order_active(tmp_path: Path) -> None:
    dtype, constants = _event_contract()
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)

    def row(event_type: int, offset_ns: int, order_id: int, price: float, qty: float) -> dict[str, object]:
        ts = base_ns + offset_ns
        ev = event_type | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"]
        if event_type == constants["ADD_ORDER_EVENT"]:
            ev |= constants["BUY_EVENT"] if order_id % 2 else constants["SELL_EVENT"]
        return {
            "ev": ev,
            "exch_ts": ts,
            "local_ts": ts + 100,
            "px": price,
            "qty": qty,
            "order_id": order_id,
            "ival": 0,
            "fval": 0.0,
        }

    source_rows = [
        row(constants["ADD_ORDER_EVENT"], 0, 1001, 5000.0, 1.0),
        row(constants["CANCEL_ORDER_EVENT"], 100, 1001, 5000.0, 1.0),
        row(constants["ADD_ORDER_EVENT"], 1_000_000_000, 2001, 5001.0, 5.0),
        row(constants["TRADE_EVENT"], 1_000_000_100, 2001, 5001.0, 2.0),
        row(constants["MODIFY_ORDER_EVENT"], 1_000_000_200, 2001, 5001.25, 3.0),
        row(constants["CANCEL_ORDER_EVENT"], 1_000_000_300, 2001, 5001.25, 3.0),
    ]
    source = tmp_path / "partial_trade_source.npz"
    events = np.zeros(len(source_rows), dtype=dtype)
    for index, source_row in enumerate(source_rows):
        for field, value in source_row.items():
            events[index][field] = value
    np.savez_compressed(source, data=events)

    prepared = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="MES",
            contract="MESU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
        )
    )

    with np.load(prepared["normalized_npz"], allow_pickle=False) as payload:
        replay = payload["data"]
    assert [(int(row["ev"]) & 0xFF, int(row["order_id"])) for row in replay] == [
        (constants["ADD_ORDER_EVENT"], 2001),
        (constants["TRADE_EVENT"], 2001),
        (constants["MODIFY_ORDER_EVENT"], 2001),
        (constants["CANCEL_ORDER_EVENT"], 2001),
    ]
    assert prepared["dropped_rows"]["preexisting_or_unknown_lifecycle"] == 0


def test_validation_allows_current_year_epoch_data_before_validation_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline

    _install_fake_hftbacktest(monkeypatch)
    now_ns = int(datetime(2026, 6, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    june_2026_ns = int(datetime(2026, 6, 1, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    monkeypatch.setattr(pipeline.time, "time_ns", lambda: now_ns)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3_2026.npz", base_exch_ts=june_2026_ns)
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot_2026.npz", base_exch_ts=june_2026_ns)

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "pass"
    assert "FUTURE_DATA_AFTER_VALIDATION_CLOCK" not in validation["fail_closed_reasons"]


def test_validation_blocks_epoch_data_after_validation_clock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline

    _install_fake_hftbacktest(monkeypatch)
    now_ns = int(datetime(2026, 6, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    future_ns = now_ns + pipeline._FUTURE_DATA_GRACE_NS + 1_000_000_000
    monkeypatch.setattr(pipeline.time, "time_ns", lambda: now_ns)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3_future.npz", base_exch_ts=future_ns)
    snapshot_path = _write_valid_l3_npz(tmp_path / "snapshot_future.npz", base_exch_ts=now_ns)

    validation = validate_hftbacktest_only_input(_config(tmp_path, data_path, snapshot_path))

    assert validation["data_validation_status"] == "fail"
    assert "FUTURE_DATA_AFTER_VALIDATION_CLOCK" in validation["fail_closed_reasons"]


def _install_exit_leg_fake_hftbacktest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mids: list[float],
    entry_fill_step: int = 1,
    exit_fill_delay: int = 1,
    fee_per_fill: float = 0.0,
    exit_submit_return: int = 0,
) -> None:
    """Fake hftbacktest where the entry fills and the mid then follows `mids`.

    The exit order (any second order id) fills `exit_fill_delay` steps after
    submission at its limit price, so exit-leg stop/take-profit/holding paths
    can be exercised deterministically.
    """
    dtype, constants = _event_contract()

    class RecordingAsset:
        def __getattr__(self, _name: str):
            def _accept(*_args: object, **_kwargs: object) -> "RecordingAsset":
                return self

            return _accept

    class _Orders:
        def __init__(self, rows: dict[int, dict[str, object]]) -> None:
            self._rows = rows

        def get(self, order_id: int) -> dict[str, object] | None:
            return self._rows.get(order_id)

    class ExitLegBacktest:
        def __init__(self, _assets: list[object]) -> None:
            self.steps = -1
            self.current_timestamp = 1_000_000_000
            self.entry_id: int | None = None
            self.entry_price = 0.0
            self.entry_qty = 0.0
            self.exit_id: int | None = None
            self.exit_price = 0.0
            self.exit_qty = 0.0
            self.exit_submit_step: int | None = None
            self.exit_rejected = False
            self.fee = 0.0
            self._fees_charged: set[str] = set()

        def _mid(self) -> float:
            index = min(max(self.steps, 0), len(mids) - 1)
            return mids[index]

        def elapse(self, interval_ns: int) -> int:
            self.steps += 1
            self.current_timestamp += int(interval_ns)
            return 0 if self.steps < len(mids) else 1

        def clear_inactive_orders(self, _asset_no: int) -> None:
            return None

        def depth(self, _asset_no: int) -> object:
            mid = self._mid()
            return SimpleNamespace(best_bid=mid - 0.125, best_ask=mid + 0.125, tick_size=0.25)

        def _entry_filled(self) -> bool:
            return self.entry_id is not None and self.steps >= entry_fill_step

        def _exit_filled(self) -> bool:
            return (
                self.exit_id is not None
                and not self.exit_rejected
                and self.exit_submit_step is not None
                and self.steps >= self.exit_submit_step + exit_fill_delay
            )

        def state_values(self, _asset_no: int) -> dict[str, float]:
            position = 0.0
            balance = 0.0
            if self._entry_filled():
                position += self.entry_qty
                balance -= self.entry_price * self.entry_qty
                if "entry" not in self._fees_charged:
                    self.fee += fee_per_fill
                    self._fees_charged.add("entry")
            if self._exit_filled():
                position -= self.exit_qty
                balance += self.exit_price * self.exit_qty
                if "exit" not in self._fees_charged:
                    self.fee += fee_per_fill
                    self._fees_charged.add("exit")
            return {"position": position, "balance": balance, "fee": self.fee}

        def submit_buy_order(self, _asset_no: int, order_id: int, price: float, qty: float, *_args: object) -> int:
            return self._submit(order_id, price, qty)

        def submit_sell_order(self, _asset_no: int, order_id: int, price: float, qty: float, *_args: object) -> int:
            return self._submit(order_id, price, qty)

        def _submit(self, order_id: int, price: float, qty: float) -> int:
            if self.entry_id is None:
                self.entry_id = order_id
                self.entry_price = float(price)
                self.entry_qty = float(qty)
            else:
                self.exit_id = order_id
                self.exit_price = float(price)
                self.exit_qty = float(qty)
                self.exit_submit_step = self.steps
                if exit_submit_return != 0:
                    # Exchange rejects the flatten: order never becomes live.
                    self.exit_rejected = True
                    self.exit_id = None
                    return exit_submit_return
            return 0

        def wait_order_response(self, *_args: object) -> int:
            return 0

        def orders(self, _asset_no: int) -> _Orders:
            rows: dict[int, dict[str, object]] = {}
            if self.entry_id is not None:
                filled = self._entry_filled()
                rows[self.entry_id] = {
                    "status": 3 if filled else 1,
                    "qty": self.entry_qty,
                    "leaves_qty": 0.0 if filled else self.entry_qty,
                    "exec_qty": self.entry_qty if filled else 0.0,
                    "price": self.entry_price,
                    "exec_price": self.entry_price if filled else 0.0,
                    "exch_timestamp": self.current_timestamp,
                    "local_timestamp": self.current_timestamp,
                }
            if self.exit_id is not None:
                filled = self._exit_filled()
                rows[self.exit_id] = {
                    "status": 3 if filled else 1,
                    "qty": self.exit_qty,
                    "leaves_qty": 0.0 if filled else self.exit_qty,
                    "exec_qty": self.exit_qty if filled else 0.0,
                    "price": self.exit_price,
                    "exec_price": self.exit_price if filled else 0.0,
                    "exch_timestamp": self.current_timestamp,
                    "local_timestamp": self.current_timestamp,
                }
            return _Orders(rows)

        def cancel(self, *_args: object) -> int:
            return 0

    fake_data = ModuleType("hftbacktest.data")
    fake_data.validate_event_order = lambda _events: None  # type: ignore[attr-defined]
    fake_types = ModuleType("hftbacktest.types")
    fake_types.event_dtype = dtype  # type: ignore[attr-defined]
    for key, value in constants.items():
        setattr(fake_types, key, value)
    fake_order = ModuleType("hftbacktest.order")
    fake_order.GTC = 0  # type: ignore[attr-defined]
    fake_order.LIMIT = 0  # type: ignore[attr-defined]
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.BacktestAsset = RecordingAsset  # type: ignore[attr-defined]
    fake_pkg.HashMapMarketDepthBacktest = ExitLegBacktest  # type: ignore[attr-defined]
    fake_pkg.data = fake_data  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    fake_pkg.order = fake_order  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hftbacktest", fake_pkg)
    monkeypatch.setitem(sys.modules, "hftbacktest.data", fake_data)
    monkeypatch.setitem(sys.modules, "hftbacktest.types", fake_types)
    monkeypatch.setitem(sys.modules, "hftbacktest.order", fake_order)


def test_exit_leg_take_profit_produces_realized_pnl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Entry fills at ask 5000.125; mid then rallies past the 0.1%
    # take-profit; exit sells marketable and fills.
    _install_exit_leg_fake_hftbacktest(
        monkeypatch, mids=[5000.0, 5000.0, 5012.0, 5012.0, 5012.0, 5012.0]
    )
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_tp"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "take_profit_pct": 0.1,
            "holding_period_bars": 50,
        },
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["exit_leg_enabled"] is True
    assert replay["exit_reason"] == "take_profit"
    assert replay["closed_quantity"] == 1.0
    assert replay["realized_closed_trade_pnl"] > 0
    assert replay["end_position"] == 0.0
    assert replay["strategy_surface_version"] == "smoke_limit_order_exit_leg_v2"
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["economic_gate_metric"] == "realized_closed_trade_pnl"
    assert stats["economic_result_status"] == "pass"


def test_exit_leg_stop_loss_realizes_negative_pnl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_exit_leg_fake_hftbacktest(
        monkeypatch, mids=[5000.0, 5000.0, 4985.0, 4985.0, 4985.0, 4985.0]
    )
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_sl"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "stop_loss_pct": 0.1,
            "holding_period_bars": 50,
        },
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["exit_reason"] == "stop_loss"
    assert replay["realized_closed_trade_pnl"] < 0
    assert replay["end_position"] == 0.0
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["economic_result_status"] == "observe"


def test_exit_leg_holding_expiry_closes_position(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_exit_leg_fake_hftbacktest(monkeypatch, mids=[5000.0] * 8)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_hold"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "exit_at_holding": True,
            "holding_period_bars": 2,
        },
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["exit_reason"] == "max_holding"
    assert replay["closed_quantity"] == 1.0
    assert replay["end_position"] == 0.0
    # Flat mids: the round trip pays the spread crossing, never profits.
    assert replay["realized_closed_trade_pnl"] <= 0


def test_exit_leg_disabled_keeps_v2_surface_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_exit_leg_fake_hftbacktest(monkeypatch, mids=[5000.0] * 6)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_off"
    config = _config(tmp_path, data_path, snapshot_path)

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["exit_leg_enabled"] is False
    assert replay["strategy_surface_version"] == "smoke_limit_order_single_order_v1"
    assert replay["realized_closed_trade_pnl"] is None
    assert not any(str(row.get("event_type", "")).startswith("EXIT_") for row in replay["orders"])
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["economic_gate_metric"] == "net_pnl_cash"


def test_exit_leg_holding_anchors_at_entry_fill_not_submit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Passive entry fills at step 3 (submit at step 0) inside the
    # holding_period_bars=4 cancel window. The holding exit must anchor at
    # the FILL (step 3 + 4 = step 7), not at submission (step 0 + 4 = 4).
    # Flat mids keep stop/TP quiet so only the holding exit can fire.
    _install_exit_leg_fake_hftbacktest(
        monkeypatch, mids=[5000.0] * 14, entry_fill_step=3
    )
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_anchor"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "exit_at_holding": True,
            "holding_period_bars": 4,
        },
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["exit_reason"] == "max_holding"
    exit_rows = [r for r in replay["orders"] if r.get("event_type") == "EXIT_ORDER_SUBMITTED"]
    assert len(exit_rows) == 1
    # Entry fill observed at step 3 -> earliest holding exit at step 7
    # (submit-anchored would have exited at step 4).
    assert exit_rows[0]["step"] >= 7
    assert replay["closed_quantity"] == 1.0


def test_exit_leg_rejected_exit_submit_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The exchange rejects the closing order. A failed flatten is an
    # engine/order failure, never strategy evidence: the run must fail
    # closed instead of reporting a mechanical pass with the residual
    # position framed as an exit-leg observation.
    _install_exit_leg_fake_hftbacktest(
        monkeypatch, mids=[5000.0] * 8, exit_submit_return=9
    )
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "exit_reject"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "max_steps": 2,
            "exit_at_holding": True,
            "holding_period_bars": 2,
        },
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert "exit_order_submit_failed" in result["fail_closed_reasons"]
    assert result["status"] != "completed"
    # Fail-closed: no stats receipt is produced, so the run can never be
    # indexed as strategy evidence.
    assert not (out_dir / "stats_summary.json").is_file()


def test_instrument_resolution_fills_unset_fields_from_specs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "hbt_resolved"
    config = replace(
        _config(tmp_path, data_path, snapshot_path),
        symbol="MES.v.0",
        tick_size=None,
        contract_size=None,
        maker_fee=None,
        taker_fee=None,
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed"
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["tick_size"] == 0.25
    assert manifest["contract_size"] == 5.0
    # MES all-in non-member per side: 0.25 exchange + 0.25 broker + 0.02 NFA
    assert manifest["maker_fee"] == pytest.approx(0.52)
    assert manifest["taker_fee"] == pytest.approx(0.52)
    assert manifest["instrument_resolution"] == {
        "tick_size": "instrument_specs",
        "contract_size": "instrument_specs",
        "maker_fee": "fee_model",
        "taker_fee": "fee_model",
    }


def test_instrument_resolution_explicit_values_win(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "hbt_explicit"

    result = run_hftbacktest_only(_config(tmp_path, data_path, snapshot_path), out_dir=out_dir)

    assert result["status"] == "completed"
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["contract_size"] == 5.0
    assert manifest["maker_fee"] == 0.47
    assert manifest["instrument_resolution"] == {
        "tick_size": "explicit",
        "contract_size": "explicit",
        "maker_fee": "explicit",
        "taker_fee": "explicit",
    }


def test_instrument_resolution_unknown_symbol_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "hbt_unknown_symbol"
    config = replace(
        _config(tmp_path, data_path, snapshot_path),
        symbol="6E.v.0",
        contract_size=None,
        maker_fee=None,
        taker_fee=None,
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "instrument_spec_missing"
    assert result["fail_closed_reasons"] == ["instrument_spec_missing:6E"]
    assert (out_dir / "run_manifest.json").is_file()
    assert not (out_dir / "stats_summary.json").is_file()
    assert not (out_dir / "promotion_decision.json").is_file()


def test_prepare_resolves_economics_from_instrument_spec(tmp_path: Path) -> None:
    dtype, constants = _event_contract()
    base_ns = int(datetime(2024, 9, 11, 12, 29, tzinfo=timezone.utc).timestamp() * 1_000_000_000)
    rows = []
    for index, offset in enumerate((0, 100, 1_500_000_000)):
        ev = constants["ADD_ORDER_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"]
        ev |= constants["BUY_EVENT"] if index % 2 else constants["SELL_EVENT"]
        rows.append((ev, base_ns + offset, base_ns + offset + 100, 5000.0 + index * 0.25, 1.0, 1001 + index, 0, 0.0))
    events = np.array(rows, dtype=dtype)
    source = tmp_path / "lake_source.npz"
    np.savez_compressed(source, data=events)

    prepared = prepare_hftbacktest_only_l3_from_lake(
        HftBacktestOnlyPrepareConfig(
            source_npz=source,
            symbol="MES.v.0",
            contract="MESU4",
            event_id="CPI_2024_09_11_TIGHT",
            trade_date="2024-09-11",
            out_root=tmp_path / "hbt",
            warmup_seconds=1,
        )
    )

    assert prepared["tick_size"] == 0.25
    assert prepared["contract_size"] == 5.0


def test_prepare_unknown_symbol_fails_closed(tmp_path: Path) -> None:
    dtype, constants = _event_contract()
    ev = constants["ADD_ORDER_EVENT"] | constants["EXCH_EVENT"] | constants["LOCAL_EVENT"] | constants["BUY_EVENT"]
    events = np.array([(ev, 1_000_000_000, 1_000_000_100, 5000.0, 1.0, 1001, 0, 0.0)], dtype=dtype)
    source = tmp_path / "lake_source.npz"
    np.savez_compressed(source, data=events)

    with pytest.raises(HftBacktestOnlyPipelineError, match="instrument_spec_missing:6E"):
        prepare_hftbacktest_only_l3_from_lake(
            HftBacktestOnlyPrepareConfig(
                source_npz=source,
                symbol="6E.v.0",
                contract="6EU4",
                event_id="CPI_2024_09_11_TIGHT",
                trade_date="2024-09-11",
                out_root=tmp_path / "hbt",
                warmup_seconds=1,
            )
        )


def _install_multi_trip_fake_hftbacktest(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mids: list[float],
    fill_delay: int = 1,
    fee_per_fill: float = 0.0,
) -> None:
    """Fake hftbacktest where EVERY submitted order fills fill_delay steps
    after submission at its limit price, so N sequential entry/exit pairs can
    be exercised deterministically."""
    dtype, constants = _event_contract()

    class RecordingAsset:
        def __getattr__(self, _name: str):
            def _accept(*_args: object, **_kwargs: object) -> "RecordingAsset":
                return self

            return _accept

    class _Orders:
        def __init__(self, rows: dict[int, dict[str, object]]) -> None:
            self._rows = rows

        def get(self, order_id: int) -> dict[str, object] | None:
            return self._rows.get(order_id)

    class MultiTripBacktest:
        def __init__(self, _assets: list[object]) -> None:
            self.steps = -1
            self.current_timestamp = 1_000_000_000
            # order_id -> {side, price, qty, submit_step}
            self.book: dict[int, dict[str, float]] = {}
            self.fee = 0.0
            self._fees_charged: set[int] = set()

        def _mid(self) -> float:
            index = min(max(self.steps, 0), len(mids) - 1)
            return mids[index]

        def elapse(self, interval_ns: int) -> int:
            self.steps += 1
            self.current_timestamp += int(interval_ns)
            return 0 if self.steps < len(mids) else 1

        def clear_inactive_orders(self, _asset_no: int) -> None:
            return None

        def depth(self, _asset_no: int) -> object:
            mid = self._mid()
            return SimpleNamespace(best_bid=mid - 0.125, best_ask=mid + 0.125, tick_size=0.25)

        def _filled(self, order_id: int) -> bool:
            row = self.book.get(order_id)
            return row is not None and self.steps >= row["submit_step"] + fill_delay

        def state_values(self, _asset_no: int) -> dict[str, float]:
            position = 0.0
            balance = 0.0
            for order_id, row in self.book.items():
                if not self._filled(order_id):
                    continue
                signed = row["qty"] if row["side"] > 0 else -row["qty"]
                position += signed
                balance -= row["price"] * signed
                if order_id not in self._fees_charged:
                    self.fee += fee_per_fill
                    self._fees_charged.add(order_id)
            return {"position": position, "balance": balance, "fee": self.fee}

        def submit_buy_order(self, _asset_no: int, order_id: int, price: float, qty: float, *_args: object) -> int:
            self.book[order_id] = {"side": 1.0, "price": float(price), "qty": float(qty), "submit_step": self.steps}
            return 0

        def submit_sell_order(self, _asset_no: int, order_id: int, price: float, qty: float, *_args: object) -> int:
            self.book[order_id] = {"side": -1.0, "price": float(price), "qty": float(qty), "submit_step": self.steps}
            return 0

        def wait_order_response(self, *_args: object) -> int:
            return 0

        def orders(self, _asset_no: int) -> _Orders:
            rows: dict[int, dict[str, object]] = {}
            for order_id, row in self.book.items():
                filled = self._filled(order_id)
                rows[order_id] = {
                    "status": 3 if filled else 1,
                    "qty": row["qty"],
                    "leaves_qty": 0.0 if filled else row["qty"],
                    "exec_qty": row["qty"] if filled else 0.0,
                    "price": row["price"],
                    "exec_price": row["price"] if filled else 0.0,
                    "exch_timestamp": self.current_timestamp,
                    "local_timestamp": self.current_timestamp,
                }
            return _Orders(rows)

        def cancel(self, *_args: object) -> int:
            return 0

    fake_data = ModuleType("hftbacktest.data")
    fake_data.validate_event_order = lambda _events: None  # type: ignore[attr-defined]
    fake_types = ModuleType("hftbacktest.types")
    fake_types.event_dtype = dtype  # type: ignore[attr-defined]
    for key, value in constants.items():
        setattr(fake_types, key, value)
    fake_order = ModuleType("hftbacktest.order")
    fake_order.GTC = 0  # type: ignore[attr-defined]
    fake_order.LIMIT = 0  # type: ignore[attr-defined]
    fake_pkg = ModuleType("hftbacktest")
    fake_pkg.__path__ = []  # type: ignore[attr-defined]
    fake_pkg.BacktestAsset = RecordingAsset  # type: ignore[attr-defined]
    fake_pkg.HashMapMarketDepthBacktest = MultiTripBacktest  # type: ignore[attr-defined]
    fake_pkg.data = fake_data  # type: ignore[attr-defined]
    fake_pkg.types = fake_types  # type: ignore[attr-defined]
    fake_pkg.order = fake_order  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hftbacktest", fake_pkg)
    monkeypatch.setitem(sys.modules, "hftbacktest.data", fake_data)
    monkeypatch.setitem(sys.modules, "hftbacktest.types", fake_types)
    monkeypatch.setitem(sys.modules, "hftbacktest.order", fake_order)


def test_multi_round_trips_completes_two_trades(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Two full BUY round trips inside one entry-scan window: entry fills at
    # the ask, mid rallies past take-profit, exit fills, entry scan re-arms.
    mids = [5000.0, 5000.0, 5012.0, 5012.0, 5000.0, 5000.0, 5012.0, 5012.0, 5012.0, 5012.0, 5012.0, 5012.0]
    _install_multi_trip_fake_hftbacktest(monkeypatch, mids=mids)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "multi_trip"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "take_profit_pct": 0.1,
            "holding_period_bars": 50,
            "max_round_trips": 2,
        },
        event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 13_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["max_round_trips"] == 2
    assert replay["strategy_surface_version"] == "smoke_limit_order_multi_trip_v3"
    assert replay["round_trips_completed"] == 2
    trips = replay["round_trips"]
    assert [t["trip"] for t in trips] == [0, 1]
    assert [t["entry_order_id"] for t in trips] == [9001, 9003]
    assert [t["exit_order_id"] for t in trips] == [9002, 9004]
    assert all(t["exit_reason"] == "take_profit" for t in trips)
    assert all(t["closed_quantity"] == 1.0 for t in trips)
    # Each trip: buy at ask 5000.125, sell at bid 5011.875 -> 11.75 points
    # x qty 1 x contract multiplier 5.
    assert trips[0]["gross_realized_pnl"] == pytest.approx(58.75)
    assert trips[1]["gross_realized_pnl"] == pytest.approx(58.75)
    assert replay["closed_quantity_total"] == 2.0
    assert replay["realized_closed_trade_pnl"] == pytest.approx(117.5)
    assert replay["orders_submitted"] == 2
    assert replay["fill_rate"] == 1.0
    assert replay["end_position"] == 0.0
    stats = json.loads((out_dir / "stats_summary.json").read_text(encoding="utf-8"))
    assert stats["round_trips_completed"] == 2
    assert stats["closed_quantity_total"] == 2.0
    assert stats["economic_gate_metric"] == "realized_closed_trade_pnl"


def test_multi_round_trips_stops_at_max(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Same tape as the two-trip test but max_round_trips=1: exactly one trade,
    # legacy single-trip receipt fields intact (v3 parity under the new code).
    mids = [5000.0, 5000.0, 5012.0, 5012.0, 5000.0, 5000.0, 5012.0, 5012.0, 5012.0, 5012.0, 5012.0, 5012.0]
    _install_multi_trip_fake_hftbacktest(monkeypatch, mids=mids)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "multi_trip_capped"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={
            "side": "BUY",
            "quantity": 1.0,
            "take_profit_pct": 0.1,
            "holding_period_bars": 50,
            "max_round_trips": 1,
        },
        event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 13_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["round_trips_completed"] == 1
    assert replay["orders_submitted"] == 1
    assert replay["exit_reason"] == "take_profit"
    assert replay["closed_quantity"] == 1.0
    assert replay["closed_quantity_total"] == 1.0
    assert replay["realized_closed_trade_pnl"] == pytest.approx(58.75)
    assert replay["strategy_surface_version"] == "smoke_limit_order_exit_leg_v2"


def test_max_round_trips_without_exit_leg_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_multi_trip_fake_hftbacktest(monkeypatch, mids=[5000.0] * 6)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "multi_trip_no_exit"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_params={"side": "BUY", "quantity": 1.0, "max_round_trips": 3},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] != "completed"
    assert "max_round_trips_requires_exit_leg" in result["fail_closed_reasons"]
    assert not (out_dir / "stats_summary.json").is_file()


def test_surface_version_labels_multi_trip() -> None:
    from backtest_pipeline.src.hftbacktest_only_pipeline import _strategy_surface_version

    exit_params = {"exit_at_holding": True}
    assert (
        _strategy_surface_version("hypothesis_limit_order", {**exit_params, "max_round_trips": 3})
        == "hypothesis_limit_order_event_scan_v4_multi_trip"
    )
    assert (
        _strategy_surface_version("hypothesis_limit_order", {**exit_params, "max_round_trips": 1})
        == "hypothesis_limit_order_event_scan_v3_exit_leg"
    )
    assert (
        _strategy_surface_version("hypothesis_limit_order", exit_params)
        == "hypothesis_limit_order_event_scan_v3_exit_leg"
    )
    assert (
        _strategy_surface_version("smoke_limit_order", {**exit_params, "max_round_trips": 2})
        == "smoke_limit_order_multi_trip_v3"
    )


def test_cross_asset_model_without_leader_ingestion_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lane cannot feed leader features yet; a cross-asset model must fail
    # closed naming the missing leader tapes, never report a 0.0 signal as
    # strategy_signal_below_threshold (the 2026-07-02 canary lie).
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "cross_asset_blocked"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "ES_MES_LEAD_LAG",
            "quantity": 1.0,
            "signal_threshold": 0.15,
            "exit_at_holding": True,
        },
        canonical_model_id="ES_MES_LEAD_LAG",
        legacy_aliases=("HYP_16",),
        event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 4_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] != "completed"
    assert "pipeline_blocker:leader_tape_missing:ES" in result["fail_closed_reasons"]
    assert not (out_dir / "stats_summary.json").is_file()


def test_divergence_model_names_both_missing_leaders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "cross_asset_blocked_two"
    config = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": "ES_NQ_DIVERGENCE_SNAPBACK",
            "quantity": 1.0,
            "signal_threshold": 0.15,
            "exit_at_holding": True,
        },
        canonical_model_id="ES_NQ_DIVERGENCE_SNAPBACK",
        legacy_aliases=("HYP_18",),
        event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 4_000_000_000},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] != "completed"
    assert "pipeline_blocker:leader_tape_missing:ES+NQ" in result["fail_closed_reasons"]


def test_required_feature_backend_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The workstation resolves the python extractor; a run REQUIRING cpp
    # must fail closed with a named blocker and no stats receipt â€” evidence
    # can never silently carry a different feature implementation.
    from dataclasses import replace

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "backend_mismatch"
    config = replace(
        _config(
            tmp_path,
            data_path,
            snapshot_path,
            strategy_id="hypothesis_limit_order",
            strategy_params={
                "model_id": "SPREAD_BLOWOUT_RECOMPRESSION",
                "quantity": 1.0,
                "signal_threshold": 0.15,
                "exit_at_holding": True,
            },
            event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 4_000_000_000},
        ),
        required_feature_backend="cpp",
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert result["status"] != "completed"
    assert any(
        reason.startswith("pipeline_blocker:feature_backend_mismatch:required=cpp,got=")
        for reason in result["fail_closed_reasons"]
    )
    assert not (out_dir / "stats_summary.json").is_file()
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["required_feature_backend"] == "cpp"


def test_empty_required_feature_backend_accepts_any(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "backend_any"

    result = run_hftbacktest_only(_config(tmp_path, data_path, snapshot_path), out_dir=out_dir)

    assert result["status"] == "completed", result["fail_closed_reasons"]


def _cross_asset_config(
    tmp_path: Path,
    data_path: Path,
    snapshot_path: Path,
    *,
    model_id: str,
    aliases: tuple[str, ...],
    cross_asset_npz: dict[str, str] | None = None,
) -> HftBacktestOnlyRunConfig:
    from dataclasses import replace

    base = _config(
        tmp_path,
        data_path,
        snapshot_path,
        strategy_id="hypothesis_limit_order",
        strategy_params={
            "model_id": model_id,
            "quantity": 1.0,
            "signal_threshold": 0.15,
            "exit_at_holding": True,
        },
        canonical_model_id=model_id,
        legacy_aliases=aliases,
        event_window={"cutoff_ts_ns": 1_000_000_000, "end_ts_ns": 4_000_000_000},
    )
    return replace(base, cross_asset_npz=cross_asset_npz or {})


def test_leader_tape_coverage_unblocks_cross_asset_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ES leader tape staged ~1s BEFORE the primary window so its features are
    # PIT-visible at every primary step; the run must not raise
    # leader_tape_missing and receipts must carry leader provenance.
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    leader_path = _write_valid_l3_npz(tmp_path / "es_leader.npz", base_exch_ts=1_000)
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "cross_asset_covered"
    config = _cross_asset_config(
        tmp_path,
        data_path,
        snapshot_path,
        model_id="ES_MES_LEAD_LAG",
        aliases=("HYP_16",),
        cross_asset_npz={"ES": str(leader_path)},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert not any(
        "leader_tape_missing" in reason for reason in result["fail_closed_reasons"]
    ), result["fail_closed_reasons"]
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["leader_symbols"] == ["ES"]
    manifest = json.loads((out_dir / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["cross_asset_npz"] == {"ES": str(leader_path)}


def test_partial_leader_coverage_names_only_missing_leaders(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    leader_path = _write_valid_l3_npz(tmp_path / "es_leader.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "cross_asset_partial"
    config = _cross_asset_config(
        tmp_path,
        data_path,
        snapshot_path,
        model_id="ES_NQ_DIVERGENCE_SNAPBACK",
        aliases=("HYP_18",),
        cross_asset_npz={"ES": str(leader_path)},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert "pipeline_blocker:leader_tape_missing:NQ" in result["fail_closed_reasons"]
    assert not (out_dir / "stats_summary.json").is_file()


def test_leader_tape_after_window_counts_alignment_gaps(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Leader events timestamped AFTER the primary window are never PIT-visible:
    # every primary step records an alignment gap and the leg stays withheld.
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    leader_path = _write_valid_l3_npz(tmp_path / "es_leader.npz", base_exch_ts=9_000_000_000)
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "cross_asset_future_leader"
    config = _cross_asset_config(
        tmp_path,
        data_path,
        snapshot_path,
        model_id="ES_MES_LEAD_LAG",
        aliases=("HYP_16",),
        cross_asset_npz={"ES": str(leader_path)},
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert not any(
        "leader_tape_missing" in reason for reason in result["fail_closed_reasons"]
    )
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["leader_alignment_gaps"] > 0


def test_vix_model_without_sensor_tape_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "vix_blocked"
    config = _cross_asset_config(
        tmp_path,
        data_path,
        snapshot_path,
        model_id="VIX_SPIKE_EVENT_FADE",
        aliases=("HYP_46",),
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert "pipeline_blocker:sensor_tape_missing:VIX" in result["fail_closed_reasons"]
    assert not (out_dir / "stats_summary.json").is_file()


def _write_sensor_npz(path: Path, *, ts: list[int]) -> Path:
    payload = {
        "ts": np.asarray(ts, dtype=np.int64),
        "vix_opt_bipower_var": np.linspace(0.1, 0.9, len(ts)),
        "_attrs_json": np.asarray([str({"columns": ["vix_opt_bipower_var"]})], dtype=object),
    }
    np.savez_compressed(path, **payload)
    return path


def test_stale_sensor_rows_withheld_with_honest_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Sensor samples exist only ~3s before the window with a 1ms staleness
    # cap: every step must withhold the leg and count a gap — never inject
    # old data stamped with the decision time.
    from dataclasses import replace

    _install_fake_hftbacktest(monkeypatch)
    data_path = _write_valid_l3_npz(tmp_path / "event_l3.npz")
    snapshot_path = _write_valid_l3_npz(tmp_path / "initial_snapshot.npz")
    sensor_path = _write_sensor_npz(tmp_path / "vix.npz", ts=[1_000, 2_000])
    out_dir = tmp_path / "artifacts" / "hbt_runs" / "vix_stale"
    config = replace(
        _cross_asset_config(
            tmp_path,
            data_path,
            snapshot_path,
            model_id="VIX_SPIKE_EVENT_FADE",
            aliases=("HYP_46",),
        ),
        sensor_feature_npz={"VIX": str(sensor_path)},
        max_leader_staleness_ns=1_000_000,
    )

    result = run_hftbacktest_only(config, out_dir=out_dir)

    assert not any(
        "sensor_tape_missing" in reason for reason in result["fail_closed_reasons"]
    )
    replay = json.loads((out_dir / "official_replay.json").read_text(encoding="utf-8"))
    assert replay["leader_alignment_gaps"] > 0
    assert replay["sensor_ids"] == ["VIX"]
