"""
Tests for ReplaySessionConfig.feature_latency_ms:
  - Adapter is synced to ts - feature_latency_ns (not ts)
  - Default (None) mirrors latency_ms
  - Zero feature_latency gives ts
  - Negative feature_latency_ms raises ValueError
  - Guard: timestamps never go negative (ts < feat_latency_ns clamps to 0)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock, call, patch

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from replay.replay_session import ReplaySessionConfig


# ---------------------------------------------------------------------------
# Config field tests (no I/O required)
# ---------------------------------------------------------------------------

class TestReplaySessionConfigField:
    def test_default_is_none(self):
        cfg = ReplaySessionConfig()
        assert cfg.feature_latency_ms is None

    def test_explicit_value_stored(self):
        cfg = ReplaySessionConfig(feature_latency_ms=2.5)
        assert cfg.feature_latency_ms == 2.5

    def test_zero_stored(self):
        cfg = ReplaySessionConfig(feature_latency_ms=0.0)
        assert cfg.feature_latency_ms == 0.0


# ---------------------------------------------------------------------------
# Stub market-data adapter to record sync calls
# ---------------------------------------------------------------------------

class _RecordingMDA:
    """Stub that records the timestamp argument passed to sync_to_timestamp."""

    def __init__(self) -> None:
        self.sync_calls: List[int] = []
        self._state = MagicMock()

    def sync_to_timestamp(self, ts: int) -> None:
        self.sync_calls.append(ts)

    def current_market_state(self, symbol: str):
        return self._state


# ---------------------------------------------------------------------------
# Integration: verify adapter receives delayed timestamp
# ---------------------------------------------------------------------------

class TestFeatureLatencyApplied:
    """
    Patches the ReplaySession internals so no real hbt/NPZ is needed.
    Verifies that mda.sync_to_timestamp receives (ts - feat_latency_ns).
    """

    def _run_patched_session(
        self,
        cfg: ReplaySessionConfig,
        sim_timestamps: List[int],
    ) -> _RecordingMDA:
        """
        Run ReplaySession._run-like logic with mocked hbt and mda.
        Returns the stub MDA so callers can inspect sync_calls.
        """
        from replay.replay_session import ReplaySession
        from unittest.mock import MagicMock

        mda = _RecordingMDA()
        depth_mock = MagicMock()
        depth_mock.best_bid = 5000.0
        depth_mock.best_ask = 5000.25

        # Build a fake hbt iterator over sim_timestamps
        elapsed = [0]
        ts_iter = iter(sim_timestamps)

        def fake_elapse(step_ns):
            try:
                hbt_mock.current_timestamp = next(ts_iter)
                return 0
            except StopIteration:
                return 1  # signals end

        hbt_mock = MagicMock()
        hbt_mock.elapse = fake_elapse
        hbt_mock.depth.return_value = depth_mock
        hbt_mock.position.return_value = 0.0

        adapter_mock = MagicMock()
        adapter_mock.drain_order_events.return_value = []
        adapter_mock.get_account_state.return_value = MagicMock(
            balance=0.0, fee=0.0, num_trades=0,
            trading_volume=0.0, position=0.0, unrealized_pnl=0.0,
        )

        strategy_mock = MagicMock()
        strategy_mock.on_step.return_value = []

        session = ReplaySession(cfg, strategy_mock)

        with (
            # build_hftbacktest is imported locally inside run(); patch the source module.
            patch("backtest_pipeline.src.hft_backtest_builder.build_hftbacktest", return_value=hbt_mock),
            patch("replay.replay_session.create_adapter", return_value=adapter_mock),
            patch("replay.replay_session.safety.assert_replay_safe"),
            patch("replay.replay_session.HistoricalReplayMarketDataAdapter.from_npz", return_value=mda),
            patch("replay.replay_session.build_certification_stamp", return_value={}),
            patch("replay.replay_session.format_stamp_footer", return_value=""),
            patch.object(session, "_write_audits"),
        ):
            session.run()

        return mda

    def test_feature_latency_none_mirrors_latency_ms(self):
        """feature_latency_ms=None → adapter synced to ts - latency_ms ticks."""
        latency_ms = 2.0
        feat_latency_ns = int(latency_ms * 1_000_000)
        ts = 10_000_000_000  # 10 s in ns

        cfg = ReplaySessionConfig(
            latency_ms=latency_ms,
            feature_latency_ms=None,
            max_steps=1,
        )
        mda = self._run_patched_session(cfg, [ts])
        assert mda.sync_calls == [ts - feat_latency_ns]

    def test_explicit_feature_latency(self):
        """feature_latency_ms=0.5 → adapter synced to ts - 500_000 ns."""
        ts = 10_000_000_000
        feat_latency_ms = 0.5
        feat_latency_ns = int(feat_latency_ms * 1_000_000)

        cfg = ReplaySessionConfig(
            latency_ms=1.0,
            feature_latency_ms=feat_latency_ms,
            max_steps=1,
        )
        mda = self._run_patched_session(cfg, [ts])
        assert mda.sync_calls == [ts - feat_latency_ns]

    def test_zero_feature_latency_gives_ts(self):
        """feature_latency_ms=0.0 → adapter synced to ts (no delay)."""
        ts = 10_000_000_000

        cfg = ReplaySessionConfig(
            latency_ms=1.0,
            feature_latency_ms=0.0,
            max_steps=1,
        )
        mda = self._run_patched_session(cfg, [ts])
        assert mda.sync_calls == [ts]

    def test_ts_less_than_latency_clamps_to_zero(self):
        """When ts < feat_latency_ns, adapter is synced to 0 (not negative)."""
        ts = 500_000  # 0.5 ms in ns
        feat_latency_ms = 1.0
        # ts - feat_latency_ns = 500_000 - 1_000_000 < 0 → must clamp to 0

        cfg = ReplaySessionConfig(
            latency_ms=1.0,
            feature_latency_ms=feat_latency_ms,
            max_steps=1,
        )
        mda = self._run_patched_session(cfg, [ts])
        assert mda.sync_calls == [0]

    def test_negative_feature_latency_raises(self):
        """feature_latency_ms < 0 raises ValueError during run().

        The ValueError is raised before any hbt/adapter construction, so no
        patching of those is needed.
        """
        from replay.replay_session import ReplaySession

        cfg = ReplaySessionConfig(feature_latency_ms=-1.0)
        session = ReplaySession(cfg, MagicMock())

        # ValueError fires at the start of run() before any I/O; no patches needed.
        with pytest.raises(ValueError, match="feature_latency_ms"):
            session.run()

    def test_multiple_steps_all_delayed(self):
        """All steps across the run receive the delayed timestamp."""
        base_ts = 10_000_000_000
        steps = [base_ts, base_ts + 100_000, base_ts + 200_000]
        feat_latency_ms = 1.0
        feat_latency_ns = int(feat_latency_ms * 1_000_000)

        cfg = ReplaySessionConfig(
            latency_ms=1.0,
            feature_latency_ms=feat_latency_ms,
        )
        mda = self._run_patched_session(cfg, steps)
        expected = [max(0, ts - feat_latency_ns) for ts in steps]
        assert mda.sync_calls == expected
