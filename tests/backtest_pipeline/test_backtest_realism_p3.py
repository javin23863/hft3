from __future__ import annotations

from pathlib import Path

from hbt_stub import _ensure_hftbacktest_stub

_ensure_hftbacktest_stub()

from backtest_pipeline.src.hftbacktest_only_pipeline import (
    HftBacktestOnlyPipelineError,
    HftBacktestOnlyRunConfig,
    _resolve_latency_ns,
)
from replay.replay_session import _fifo_fill_state, _unrealized_pnl_marked_to_mid


def _fill(side: str, price: float, qty: float, fees: float = 0.0) -> dict:
    return {"side": side, "exec_price": price, "qty": qty, "fees": fees}


def test_fifo_open_lots_after_partial_close() -> None:
    fills = [_fill("BUY", 100.0, 2.0, fees=1.0), _fill("SELL", 101.0, 1.0, fees=0.5)]
    realized, open_lots, open_side = _fifo_fill_state(fills)
    # 1 lot closed at +1.00, minus half the open fee (0.5) and the close fee (0.5).
    assert realized == 1.0 - 0.5 - 0.5
    assert open_side == 1
    assert len(open_lots) == 1
    assert open_lots[0][0] == 100.0
    assert open_lots[0][1] == 1.0


def test_unrealized_marked_to_mid_long_and_short() -> None:
    long_fills = [_fill("BUY", 100.0, 2.0)]
    assert _unrealized_pnl_marked_to_mid(long_fills, 101.5) == 3.0
    short_fills = [_fill("SELL", 100.0, 2.0)]
    assert _unrealized_pnl_marked_to_mid(short_fills, 99.0) == 2.0


def test_unrealized_is_zero_when_flat_and_none_when_unmarkable() -> None:
    flat = [_fill("BUY", 100.0, 1.0), _fill("SELL", 101.0, 1.0)]
    assert _unrealized_pnl_marked_to_mid(flat, 105.0) == 0.0
    open_pos = [_fill("BUY", 100.0, 1.0)]
    assert _unrealized_pnl_marked_to_mid(open_pos, None) is None


def test_contract_multiplier_scales_price_component_not_fees() -> None:
    fills = [_fill("BUY", 100.0, 2.0, fees=1.0), _fill("SELL", 101.0, 1.0, fees=0.5)]
    realized, _lots, _side = _fifo_fill_state(fills, contract_multiplier=5.0)
    # Price component 1.00 * 5 = 5.00; fees (0.5 open apportioned + 0.5 close)
    # are already account currency and must not scale.
    assert realized == 5.0 - 0.5 - 0.5
    open_pos = [_fill("BUY", 100.0, 1.0)]
    assert _unrealized_pnl_marked_to_mid(open_pos, 101.0, contract_multiplier=5.0) == 5.0


def _config(**overrides) -> HftBacktestOnlyRunConfig:
    base = dict(
        run_id="p3_test",
        symbol="MES",
        contract="MESH6",
        event_id="CPI_2024_09_11_TIGHT",
        normalized_npz=Path("missing.npz"),
        initial_snapshot=Path("missing_snapshot.npz"),
        strategy_id="smoke_limit_order",
        strategy_params={},
    )
    base.update(overrides)
    return HftBacktestOnlyRunConfig(**base)


def test_latency_constant_default_resolves_from_config_ns() -> None:
    entry_ns, resp_ns = _resolve_latency_ns(
        _config(entry_latency_ns=250_000, response_latency_ns=500_000)
    )
    assert entry_ns == 250_000
    assert resp_ns == 500_000


def test_latency_chi404_fails_closed_when_unmeasured(monkeypatch) -> None:
    import backtest_pipeline.src.chi404_latency as chi404_latency
    import pytest

    def _raise(**_kwargs):
        raise FileNotFoundError("CHI404 latency summary missing")

    monkeypatch.setattr(chi404_latency, "resolve_latency_model", _raise)
    with pytest.raises(HftBacktestOnlyPipelineError, match="chi404_latency_unmeasured"):
        _resolve_latency_ns(_config(latency_model="chi404_measured"))


def test_latency_chi404_measured_resolves_via_probe(monkeypatch) -> None:
    import backtest_pipeline.src.chi404_latency as chi404_latency

    def _fake_resolve(*, regime: str):
        assert regime == "normal"
        return {
            "latency_model_family": "ConstantLatency",
            "order_entry_latency_ms": 3.1,
            "order_response_latency_ms": 3.2,
        }

    monkeypatch.setattr(chi404_latency, "resolve_latency_model", _fake_resolve)
    entry_ns, resp_ns = _resolve_latency_ns(_config(latency_model="chi404_measured:normal"))
    assert entry_ns == 3_100_000
    assert resp_ns == 3_200_000


def test_latency_unsupported_fails_closed() -> None:
    import pytest

    with pytest.raises(HftBacktestOnlyPipelineError, match="unsupported_latency_model"):
        _resolve_latency_ns(_config(latency_model="magic_latency"))
