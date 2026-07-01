from __future__ import annotations

import json
from pathlib import Path

from hbt_stub import _ensure_hftbacktest_stub

_ensure_hftbacktest_stub()

import backtest_pipeline.src.hbt_only_gates as gates
import backtest_pipeline.src.hftbacktest_only_pipeline as pipeline
from backtest_pipeline.src.hbt_only_gates import (
    default_sensitivity_scenarios,
    run_robustness_gate,
    run_sensitivity_battery,
)
from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyRunConfig,
    write_promotion_decision,
)


def _config(**overrides) -> HftBacktestOnlyRunConfig:
    base = dict(
        run_id="gate_test",
        symbol="MES",
        contract="MESH6",
        event_id="CPI_2024_09_11_TIGHT",
        normalized_npz=Path("event.npz"),
        initial_snapshot=Path("snapshot.npz"),
        strategy_id="hypothesis_limit_order",
        canonical_model_id="SECOND_WAVE_CONTINUATION",
        strategy_params={"quantity": 1.0, "take_profit_pct": 1.0, "stop_loss_pct": 0.5},
    )
    base.update(overrides)
    return HftBacktestOnlyRunConfig(**base)


def test_default_scenarios_cover_fill_latency_fee_axes() -> None:
    scenarios = default_sensitivity_scenarios(_config())
    ids = {s["scenario_id"] for s in scenarios}
    # 2 fill x 3 latency x 2 fee = 12 minus the base identity = 11.
    assert len(scenarios) == 11
    assert any("PartialFillExchange" in i for i in ids)
    assert any("lat0.5x" in i for i in ids)
    assert any("lat2x" in i for i in ids)
    assert any("fee_conservative" in i for i in ids)
    # Conservative fee bump = MES all-in per side (0.25 + 0.25 + 0.02).
    conservative = next(s for s in scenarios if s["fee_label"] == "conservative")
    assert abs(conservative["maker_fee"] - 0.52) < 1e-9


def _fake_strategy_runner(realized_by_scenario: dict[str, float]):
    def _run(config):
        realized = realized_by_scenario.get(config.run_id.split("__", 1)[-1], 5.0)
        replay = {
            "official_hftbacktest_replay_status": "pass",
            "orders": [],
            "fills": [{"order_id": 9001, "filled_quantity": 1.0}],
            "orders_submitted": 1,
            "fills_count": 1,
            "fill_rate": 1.0,
            "gross_pnl": realized,
            "net_pnl": realized,
            "realized_closed_trade_pnl": realized,
            "fail_closed_reasons": [],
        }
        return replay, []

    return _run


def _base_stats(realized: float = 5.0) -> dict:
    return {
        "mechanical_validity_status": "pass",
        "realized_closed_trade_pnl": realized,
        "fills_count": 3,
        "fill_rate": 1.0,
        "fail_closed_reasons": [],
    }


def test_sensitivity_battery_passes_when_all_scenarios_survive(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(pipeline, "_run_minimal_strategy", _fake_strategy_runner({}))
    report = run_sensitivity_battery(_config(), out_dir=tmp_path, base_stats=_base_stats())
    assert report["status"] == "pass", report["fail_closed_reasons"]
    assert report["scenario_count"] == 12  # base + 11 scenarios
    assert report["min_realized_closed_trade_pnl"] > 0
    assert (tmp_path / "gate3_sensitivity.json").is_file()


def test_sensitivity_battery_fails_when_conservative_scenario_loses(
    tmp_path: Path, monkeypatch
) -> None:
    losing = {"PartialFillExchange__lat2x__fee_conservative": -3.0}
    monkeypatch.setattr(pipeline, "_run_minimal_strategy", _fake_strategy_runner(losing))
    report = run_sensitivity_battery(_config(), out_dir=tmp_path, base_stats=_base_stats())
    assert report["status"] == "fail"
    assert any(r.startswith("realized_pnl_not_robust_to_realism") for r in report["fail_closed_reasons"])


def test_robustness_gate_fails_closed_on_insufficient_events(tmp_path: Path) -> None:
    events = [
        {"event_id": "CPI_1", "realized_closed_trade_pnl": 10.0},
        {"event_id": "CPI_2", "realized_closed_trade_pnl": 12.0},
    ]
    report = run_robustness_gate(events, out_dir=tmp_path)
    assert report["status"] == "fail"
    reasons = report["fail_closed_reasons"]
    assert any(r.startswith("insufficient_events:2<4") for r in reasons)
    assert "cscv_matrix_missing" in reasons
    assert (tmp_path / "robustness_report.json").is_file()


def test_robustness_gate_passes_with_events_and_dominant_config(tmp_path: Path) -> None:
    events = [
        {"event_id": f"CPI_{i}", "realized_closed_trade_pnl": pnl}
        for i, pnl in enumerate([10.0, 12.0, 11.0, 13.0, 12.0, 11.5, 12.5, 11.0])
    ]
    # Config 0 dominates every chronological block -> PBO ~ 0.
    matrix = [[pnl, pnl - 5.0, pnl - 6.0, pnl - 7.0] for pnl in (r["realized_closed_trade_pnl"] for r in events)]
    report = run_robustness_gate(events, out_dir=tmp_path, performance_matrix=matrix)
    assert report["status"] == "pass", report["fail_closed_reasons"]
    assert report["psr"] >= 0.95
    assert report["dsr"] >= 0.90
    assert report["cscv"]["pbo"] <= 0.20


def _write_minimal_outputs(out_dir: Path, *, economic: str = "pass") -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "recorder_result.npz").write_bytes(b"npz")
    (out_dir / "stats_summary.json").write_text(
        json.dumps(
            {
                "run_id": "gate_test",
                "mechanical_validity_status": "pass",
                "economic_result_status": economic,
                "realized_closed_trade_pnl": 10.0,
            }
        ),
        encoding="utf-8",
    )


def test_promotion_denied_without_gate_reports(tmp_path: Path) -> None:
    _write_minimal_outputs(tmp_path)
    decision = write_promotion_decision(tmp_path)
    assert decision["promotion_allowed"] is False
    assert decision["decision"] == "observe"
    assert decision["microstructure_realism_status"] == "not_evaluated_minimal_slice"


def test_promotion_allowed_only_when_all_four_gates_pass(tmp_path: Path) -> None:
    _write_minimal_outputs(tmp_path)
    (tmp_path / "gate3_sensitivity.json").write_text(
        json.dumps({"status": "pass", "fail_closed_reasons": []}), encoding="utf-8"
    )
    (tmp_path / "robustness_report.json").write_text(
        json.dumps({"status": "pass", "fail_closed_reasons": []}), encoding="utf-8"
    )
    decision = write_promotion_decision(tmp_path)
    assert decision["promotion_allowed"] is True
    assert decision["decision"] == "promote"

    # A failing robustness gate flips promotion back off.
    (tmp_path / "robustness_report.json").write_text(
        json.dumps({"status": "fail", "fail_closed_reasons": ["insufficient_events:2<4"]}),
        encoding="utf-8",
    )
    decision = write_promotion_decision(tmp_path)
    assert decision["promotion_allowed"] is False
    assert decision["gate_fail_closed_reasons"]["gate4"] == ["insufficient_events:2<4"]


def test_promotion_denied_when_economic_gate_observes(tmp_path: Path) -> None:
    _write_minimal_outputs(tmp_path, economic="observe")
    (tmp_path / "gate3_sensitivity.json").write_text(
        json.dumps({"status": "pass", "fail_closed_reasons": []}), encoding="utf-8"
    )
    (tmp_path / "robustness_report.json").write_text(
        json.dumps({"status": "pass", "fail_closed_reasons": []}), encoding="utf-8"
    )
    decision = write_promotion_decision(tmp_path)
    assert decision["promotion_allowed"] is False
    assert decision["decision"] == "observe"
