from __future__ import annotations

from backtest_pipeline.src import replay_matrix
from replay.replay_session import _realized_closed_trade_pnl


class _Hyp:
    hyp_id = 7


class _ReplayStub:
    def __init__(self, cfg, strategy) -> None:
        self.cfg = cfg
        self.strategy = strategy

    def run(self) -> dict:
        return {
            "balance": -5000.52,
            "fee": 0.52,
            "num_trades": 1,
            "position": 1.0,
            "fill_events": [{
                "timestamp_ns": 1,
                "side": "BUY",
                "exec_price": 5000.0,
                "qty": 1.0,
                "fees": 0.52,
            }],
        }


def test_open_inventory_cash_balance_is_not_reported_as_realized_pnl(monkeypatch):
    monkeypatch.setattr(replay_matrix, "ReplaySession", _ReplayStub)

    result = replay_matrix.run_hypothesis_replay(_Hyp(), "unused.npz")

    assert result.num_trades == 0
    assert result.net_pnl == 0.0
    assert result.expectancy == 0.0
    assert result.hftbacktest_cash_balance == -5000.52
    assert result.ending_position_qty == 1.0


class _RoundTripReplayStub:
    def __init__(self, cfg, strategy) -> None:
        self.cfg = cfg
        self.strategy = strategy

    def run(self) -> dict:
        return {
            "balance": 0.76,
            "fee": 0.24,
            "num_trades": 2,
            "position": 0.0,
            "fill_events": [
                {
                    "timestamp_ns": 1,
                    "side": "BUY",
                    "exec_price": 100.0,
                    "qty": 1.0,
                    "fees": 0.12,
                },
                {
                    "timestamp_ns": 2,
                    "side": "SELL",
                    "exec_price": 101.0,
                    "qty": 1.0,
                    "fees": 0.12,
                },
            ],
        }


def test_closed_round_trip_pnl_is_net_of_matched_fill_fees(monkeypatch):
    monkeypatch.setattr(replay_matrix, "ReplaySession", _RoundTripReplayStub)

    result = replay_matrix.run_hypothesis_replay(_Hyp(), "unused.npz")

    assert result.num_trades == 1
    assert result.net_pnl == 0.76
    assert result.expectancy == 0.76
    assert result.tail_loss == 0.76
    assert result.hftbacktest_cash_balance == 0.76
    assert result.ending_position_qty == 0.0


def test_replay_audit_realized_pnl_excludes_open_inventory_cash():
    fill_records = [
        {
            "timestamp_ns": 1,
            "side": "BUY",
            "exec_price": 5000.0,
            "qty": 1.0,
            "fees": 0.52,
        }
    ]

    assert _realized_closed_trade_pnl(fill_records) == 0.0


def test_replay_audit_realized_pnl_counts_only_matched_fill_fees():
    fill_records = [
        {
            "timestamp_ns": 1,
            "side": "BUY",
            "exec_price": 100.0,
            "qty": 2.0,
            "fees": 0.20,
        },
        {
            "timestamp_ns": 2,
            "side": "SELL",
            "exec_price": 101.0,
            "qty": 1.0,
            "fees": 0.10,
        },
    ]

    assert _realized_closed_trade_pnl(fill_records) == 0.8
