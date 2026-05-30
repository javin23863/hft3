"""Tests for defensive-layer toggles on PDF hybrid strategy."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from backtest_pipeline.src.pdf_defensive_config import DefensiveConfig, all_defensive_configs
from backtest_pipeline.src.pdf_hybrid_ablation import summarize_replay_result
from backtest_pipeline.src.pdf_hybrid_strategy import HybridExecutionStrategy
from features_engine.src.features.mbo_features import MBOEvent


def _event_meta() -> dict:
    return {
        "event_id": "TEST",
        "end_utc": "2024-09-11T12:35:00+00:00",
        "window_utc": "2024-09-11T12:29:30+00:00 to 2024-09-11T12:35:00+00:00",
    }


def _events() -> list[MBOEvent]:
    base = 1_000_000_000_000
    return [
        MBOEvent(base, 1, "ADD", "B", 5500.0, 10),
        MBOEvent(base + 100, 2, "ADD", "A", 5500.25, 8),
        MBOEvent(base + 200, 3, "TRADE", "A", 5500.25, 5),
    ]


def test_defensive_config_mode_ids() -> None:
    modes = {c.mode_id for c in all_defensive_configs()}
    assert modes == {"as_baseline", "ofi_only", "vpin_only", "hybrid_full"}


def test_as_baseline_zeros_drift() -> None:
    events = _events()
    full = HybridExecutionStrategy(
        "unused.npz", event_meta=_event_meta(), mbo_events=events, use_ofi=True, use_vpin=True
    )
    baseline = HybridExecutionStrategy(
        "unused.npz", event_meta=_event_meta(), mbo_events=events, use_ofi=False, use_vpin=False
    )
    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.cancel = MagicMock()

    full.on_step(hbt)
    baseline.on_step(hbt)

    assert full.last_hybrid_out.payload.OFI_drift_component != 0.0
    assert baseline.last_hybrid_out.payload.OFI_drift_component == 0.0
    assert baseline.last_hybrid_out.payload.VPIN_multiplier == 1.0


def test_use_vpin_false_skips_ingest_bar() -> None:
    events = _events()
    strategy = HybridExecutionStrategy(
        "unused.npz", event_meta=_event_meta(), mbo_events=events, use_vpin=False
    )
    calls: list[float] = []
    original = strategy.vpin_model.ingest_bar
    strategy.vpin_model.ingest_bar = lambda *a, **k: calls.append(a[1]) or original(*a, **k)

    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0

    strategy.on_step(hbt)
    assert calls == []


def test_use_ofi_false_skips_book_update() -> None:
    events = _events()
    strategy = HybridExecutionStrategy(
        "unused.npz", event_meta=_event_meta(), mbo_events=events, use_ofi=False, use_vpin=True
    )
    with patch.object(strategy.book_model, "update_bbo") as mock_bbo:
        depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
        hbt = MagicMock()
        hbt.depth.return_value = depth
        hbt.current_timestamp = events[-1].timestamp_ns
        hbt.position.return_value = 0.0
        hbt.cancel = MagicMock()
        strategy.on_step(hbt)
    mock_bbo.assert_not_called()


def test_summarize_replay_result_error() -> None:
    out = summarize_replay_result({"error": 3, "steps": 10})
    assert out["error"] == 3
    assert out["net_pnl"] == 0.0


def test_vpin_only_unit_probe_nonzero_drift() -> None:
    events = _events()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
        defensive=DefensiveConfig(use_ofi=False, use_vpin=True),
    )
    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.cancel = MagicMock()
    hbt.submit_buy_order = MagicMock()
    hbt.submit_sell_order = MagicMock()

    strategy.on_step(hbt)
    assert strategy.last_hybrid_out is not None
    assert strategy.last_hybrid_out.payload is not None
    assert strategy.last_hybrid_out.payload.OFI_drift_component != 0.0
    assert strategy.diagnostics["ofi_unit_probe"] == 1.0
    strategy._last_vpin_out.VPIN_value = 0.25
    strategy.on_step(hbt)
    assert strategy.last_hybrid_out.payload.OFI_drift_component != 0.0


def test_defensive_conflicts_with_use_ofi_kwarg() -> None:
    with pytest.raises(ValueError, match="conflicts"):
        HybridExecutionStrategy(
            "unused.npz",
            defensive=DefensiveConfig(use_ofi=False, use_vpin=True),
            use_ofi=True,
        )


def test_quote_refresh_skips_redundant_submits() -> None:
    events = _events()
    strategy = HybridExecutionStrategy(
        "unused.npz",
        event_meta=_event_meta(),
        mbo_events=events,
        quote_refresh_ticks=100,
    )
    stable = MagicMock(
        payload=MagicMock(
            cancel_quote_flag=False,
            passive_to_aggressive_flag=False,
            optimal_bid=5499.75,
            optimal_ask=5500.5,
        )
    )
    strategy.hybrid_model.evaluate = MagicMock(return_value=stable)

    depth = SimpleNamespace(best_bid=5500.0, best_ask=5500.25, best_bid_qty=10, best_ask_qty=8)
    hbt = MagicMock()
    hbt.depth.return_value = depth
    hbt.current_timestamp = events[-1].timestamp_ns
    hbt.position.return_value = 0.0
    hbt.cancel = MagicMock()
    hbt.submit_buy_order = MagicMock()
    hbt.submit_sell_order = MagicMock()

    strategy.on_step(hbt)
    first_refresh = strategy.diagnostics["quote_refresh_count"]
    strategy.on_step(hbt)
    assert strategy.diagnostics["quote_refresh_count"] == first_refresh
    hbt.submit_buy_order.assert_called_once()
    hbt.submit_sell_order.assert_called_once()


def test_summarize_includes_diagnostics() -> None:
    diag = {
        "cancel_count": 2.0,
        "quote_refresh_count": 1.0,
        "mean_vpin": 0.1,
        "mean_ofi_smooth": 0.5,
    }
    out = summarize_replay_result(
        {"balance": 100.0, "fee": 5.0, "steps": 10, "num_trades": 1, "position": 0.0},
        diagnostics=diag,
    )
    assert out["net_pnl_after_fee"] == 95.0
    assert out["cancel_count"] == 2.0
    assert out["mean_vpin"] == 0.1


def test_resolve_replay_latency_ms_cli_override() -> None:
    from backtest_pipeline.src.chi404_latency import resolve_replay_latency_ms

    ms, source, chi404 = resolve_replay_latency_ms(latency_ms=2.5)
    assert ms == 2.5
    assert source == "CLI --latency-ms"
    assert chi404 is None


def test_resolve_replay_latency_ms_rejects_out_of_band() -> None:
    from backtest_pipeline.src.chi404_latency import resolve_replay_latency_ms

    with pytest.raises(ValueError, match="outside BLUEPRINT band"):
        resolve_replay_latency_ms(latency_ms=0.01)


def test_resolve_replay_latency_ms_from_chi404_summary() -> None:
    from backtest_pipeline.src.chi404_latency import DEFAULT_CHI404_SUMMARY, resolve_replay_latency_ms

    if not DEFAULT_CHI404_SUMMARY.is_file():
        pytest.skip(f"CHI404 summary missing: {DEFAULT_CHI404_SUMMARY}")
    ms, source, chi404 = resolve_replay_latency_ms(latency_ms=None)
    assert 0.5 <= ms <= 10.0
    assert "CHI404" in source
    assert chi404 is not None
    assert chi404.get("backtest_latency_ms") == ms


def test_resolve_replay_latency_ms_rejects_chi404_out_of_band(tmp_path: Path) -> None:
    import json

    from backtest_pipeline.src.chi404_latency import resolve_replay_latency_ms

    bad = tmp_path / "bad_latency.json"
    bad.write_text(
        json.dumps({"network": {"rithmic_tcp_65000": {"p99_ms": 0.01}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="outside BLUEPRINT band"):
        resolve_replay_latency_ms(latency_ms=None, chi404_summary=bad)
