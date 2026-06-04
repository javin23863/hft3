import json

import pytest

from models.ghost_route.ghost_route_backtest import EVENT_LOG_FIELDS
from models.ghost_route.ghost_route_model import load_config
from models.ghost_route.ghost_route_metrics import summarize_event_log


def test_ghost_route_event_schema_has_required_replay_fields() -> None:
    schema = json.loads(open("models/ghost_route/ghost_route_event_log_schema.json", encoding="utf-8").read())
    required = set(schema["required"])

    for field in EVENT_LOG_FIELDS:
        assert field in required


def test_ghost_route_metrics_never_pass_without_net_expectancy() -> None:
    rows = [
        {
            "fill_status": "FULL_FILL",
            "filled_quantity": 1,
            "gross_pnl": 1.0,
            "fees": 0.2,
            "slippage_cost": 0.1,
            "adverse_selection_cost": 0.1,
            "net_pnl": -0.05,
            "lead_time_us": 23,
        }
    ]

    summary = summarize_event_log(rows)

    assert summary["classification"] == "FAIL"
    assert summary["net_expectancy_per_signal"] < 0


def test_ghost_route_event_log_schema_validates_minimal_row() -> None:
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(open("models/ghost_route/ghost_route_event_log_schema.json", encoding="utf-8").read())
    row = {field: "" for field in EVENT_LOG_FIELDS}
    row.update(
        {
            "signal_id": "ghost_route_1",
            "timestamp_signal": 1,
            "timestamp_order_arrival": 24,
            "macro_contract": "ES",
            "micro_contract": "MES",
            "direction": "SELL",
            "fill_status": "FULL_FILL",
            "filled_quantity": 1,
            "net_pnl": 0.1,
            "reject_reason": "",
        }
    )

    jsonschema.validate(row, schema)


def test_ghost_route_loads_tracked_config() -> None:
    cfg = load_config("models/ghost_route/ghost_route_config.yaml")

    assert cfg.timestamp_unit == "ns"
    assert cfg.compute_latency_us == 0
    assert cfg.compute_latency_source == "default_zero_requires_measured_override"
    assert cfg.pair_calibration["ES_MES"].tau_hat_us == 50
