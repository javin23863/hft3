"""Tests for the VectorBT parameter-matrix screening mode (Phase 5).

Verifies:
- ``_chunk_parameter_trials``: correct chunking, boundary independence
- ``_param_chunk_hash``: deterministic, differs on different params
- ``_build_sl_tp_arrays``: correct per-column values, None handling
- ``run_vectorbt_simulation_matrix``:
    - Same result count as loop mode for same inputs
    - Parameter ordering is deterministic
    - Chunk boundary independence (results same regardless of chunk size)
    - No-lookahead shift applied per column

VectorBT is mocked with a fake Portfolio that supports matrix-shaped
entries/exits and per-column ``pf.stats(column=...)`` extraction, mirroring the
real VectorBT matrix API.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from backtest_pipeline.src import vectorbt_adapter
from backtest_pipeline.src.paid_screen_matrix import (
    DEFAULT_MATRIX_CHUNK_SIZE,
    _build_sl_tp_arrays,
    _chunk_parameter_trials,
    _param_chunk_hash,
    _sl_tp_for_portfolio,
    run_vectorbt_simulation_matrix,
)
from backtest_pipeline.src.vectorbt_adapter import (
    DEFAULT_PARAM_GRID,
    _run_vectorbt_simulation,
    expand_parameter_grid,
)
from research_pipeline.types import CandidateModel


# ---------------------------------------------------------------------------
# Shared test helpers
# ---------------------------------------------------------------------------

def _mock_candidate(model_id: str = "HYP_5", threshold: float = 0.15) -> CandidateModel:
    return CandidateModel(
        candidate_id=f"{model_id}_thresh_{threshold}",
        model_id=model_id,
        strategy_params={"signal_threshold": threshold},
        thesis="Fade spread blowout after CPI",
        metadata={"source_model": model_id, "strategy_family": model_id, "symbol": "MES"},
    )


def _complete_vbt_stats(
    *,
    total_return_pct: float = 1.25,
    total_trades: int = 1,
    expectancy: float = 0.01,
    max_drawdown_pct: float = -0.2,
) -> dict:
    return {
        "Total Return [%]": total_return_pct,
        "Total Trades": total_trades,
        "Expectancy": expectancy,
        "Profit Factor": 1.4,
        "Sharpe Ratio": 0.8,
        "Sortino Ratio": 1.1,
        "Max Drawdown [%]": max_drawdown_pct,
    }


def _install_fake_vectorbt(monkeypatch, from_signals_factory):
    """Install a fake ``vectorbt`` module whose ``Portfolio.from_signals`` returns
    an object built by ``from_signals_factory``.

    ``from_signals_factory(close, entries, exits, **kwargs)`` must return an
    object supporting ``.stats(column=...)`` per column (a
    ``FakeMatrixPortfolio``).
    """
    fake_vectorbt = SimpleNamespace(
        Portfolio=SimpleNamespace(from_signals=from_signals_factory)
    )
    monkeypatch.setitem(sys.modules, "vectorbt", fake_vectorbt)
    monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", True)
    monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", "1.0.0")
    monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)


class FakeMatrixPortfolio:
    """Fake Portfolio that mimics VectorBT's matrix API.

    ``from_signals`` receives 2-D ``entries``/``exits`` arrays of shape
    (n_bars, n_cols).  Each column produces an independent fake backtest with
    stats derived from the entry/exit pattern, so different parameter
    combinations yield different (deterministic) stats.

    ``pf[:, i]`` returns a per-column view whose ``.stats()`` returns the
    stats for column ``i`` only.
    """

    def __init__(self, close: np.ndarray, entries: np.ndarray, exits: np.ndarray):
        self._close = np.asarray(close, dtype=float)
        # entries/exits are boolean 2-D arrays [n_bars, n_cols]
        self._entries = np.asarray(entries, dtype=bool)
        self._exits = np.asarray(exits, dtype=bool)
        self._n_cols = self._entries.shape[1] if self._entries.ndim == 2 else 1
        self._stats_cache: dict[int, dict] = {}
        for col in range(self._n_cols):
            self._stats_cache[col] = self._simulate_column(col)

    def _simulate_column(self, col: int) -> dict:
        if self._entries.ndim == 2:
            entry_col = self._entries[:, col]
            exit_col = self._exits[:, col]
        else:
            entry_col = self._entries
            exit_col = self._exits
        if self._close.ndim == 2:
            close = self._close[:, col]
        else:
            close = self._close
        position = False
        entry_price = 0.0
        returns: list[float] = []
        for index, price in enumerate(close):
            price = float(price)
            if not position and bool(entry_col[index]):
                position = True
                entry_price = price
            elif position and bool(exit_col[index]):
                returns.append((price - entry_price) / entry_price if entry_price else 0.0)
                position = False
        if position:
            returns.append((float(close[-1]) - entry_price) / entry_price if entry_price else 0.0)
        expectancy = float(np.mean(returns)) if returns else 0.0
        total_return_pct = float(np.sum(returns) * 100.0)
        return _complete_vbt_stats(
            total_return_pct=round(total_return_pct, 6),
            total_trades=len(returns),
            expectancy=round(expectancy, 6),
            max_drawdown_pct=-0.1,
        )

    def __getitem__(self, key):
        # Support pf[:, i] indexing -> a per-column portfolio view
        if isinstance(key, tuple) and len(key) == 2 and isinstance(key[0], slice):
            col = int(key[1])
            return _FakeColumnPortfolio(self, col)
        # Support integer column index too
        if isinstance(key, (int, np.integer)):
            return _FakeColumnPortfolio(self, int(key))
        raise IndexError(f"unsupported index: {key!r}")

    def stats(self, column=None, **kwargs):
        if column is not None:
            return dict(self._stats_cache[int(column)])
        # Aggregate stats (not used by the matrix mode, which uses per-column
        # stats).  Return the first column's stats as a fallback.
        return self._stats_cache.get(0, _complete_vbt_stats())

    @property
    def wrapper(self):
        return SimpleNamespace(columns=list(range(self._n_cols)))


class _FakeColumnPortfolio:
    def __init__(self, parent: FakeMatrixPortfolio, col: int):
        self._parent = parent
        self._col = col

    def stats(self):
        return dict(self._parent._stats_cache[self._col])


def _make_fake_from_signals(captured: dict | None = None):
    """Return a ``from_signals`` factory that builds a FakeMatrixPortfolio."""

    def from_signals(close, entries, exits, **kwargs):
        if captured is not None:
            captured["close"] = close
            captured["entries"] = np.asarray(entries)
            captured["exits"] = np.asarray(exits)
            captured["kwargs"] = kwargs
        return FakeMatrixPortfolio(close, entries, exits)

    return from_signals


def _synthetic_ohlcv(n: int = 80) -> np.ndarray:
    close = 100.0 + np.arange(n, dtype=float) * 0.1
    return np.column_stack([close, close, close, close, np.ones_like(close)])


def _signal_computer_returns_fixed(entry_idx: int = 1, exit_idx: int = -1):
    """Signal computer that ignores params and returns a fixed signal pattern."""

    def computer(cand, bars, parsed, repo_root):
        entry = np.zeros(len(bars))
        exit_ = np.zeros(len(bars))
        entry[entry_idx] = 1.0
        if exit_idx < 0:
            exit_[exit_idx] = -1.0
        else:
            exit_[exit_idx] = -1.0
        return entry, exit_

    return computer


# ---------------------------------------------------------------------------
# _chunk_parameter_trials
# ---------------------------------------------------------------------------

class TestChunkParameterTrials:
    def test_correct_chunking_even_division(self):
        trials = [{"i": i} for i in range(8)]
        chunks = list(_chunk_parameter_trials(trials, chunk_size=4))
        assert len(chunks) == 2
        assert len(chunks[0]) == 4
        assert len(chunks[1]) == 4

    def test_correct_chunking_uneven_division(self):
        trials = [{"i": i} for i in range(10)]
        chunks = list(_chunk_parameter_trials(trials, chunk_size=4))
        assert len(chunks) == 3
        assert len(chunks[0]) == 4
        assert len(chunks[1]) == 4
        assert len(chunks[2]) == 2

    def test_concatenation_reproduces_input(self):
        trials = [{"a": i, "b": i * 2} for i in range(13)]
        chunks = list(_chunk_parameter_trials(trials, chunk_size=5))
        flat = [p for chunk in chunks for p in chunk]
        assert flat == trials

    def test_chunk_boundary_independence_of_order(self):
        """Results are independent of chunk size — concatenation is identical."""
        trials = [{"i": i} for i in range(16)]
        for cs in (1, 2, 4, 8, 16, 64):
            chunks = list(_chunk_parameter_trials(trials, chunk_size=cs))
            flat = [p for chunk in chunks for p in chunk]
            assert flat == trials, f"chunk_size={cs} broke ordering"

    def test_empty_trials(self):
        chunks = list(_chunk_parameter_trials([], chunk_size=4))
        assert chunks == []

    def test_chunk_size_larger_than_trials(self):
        trials = [{"i": i} for i in range(3)]
        chunks = list(_chunk_parameter_trials(trials, chunk_size=100))
        assert len(chunks) == 1
        assert len(chunks[0]) == 3

    def test_invalid_chunk_size_raises(self):
        with pytest.raises(ValueError):
            list(_chunk_parameter_trials([{"i": 0}], chunk_size=0))
        with pytest.raises(ValueError):
            list(_chunk_parameter_trials([{"i": 0}], chunk_size=-1))

    def test_chunk_size_one_gives_singletons(self):
        trials = [{"i": i} for i in range(4)]
        chunks = list(_chunk_parameter_trials(trials, chunk_size=1))
        assert len(chunks) == 4
        assert all(len(c) == 1 for c in chunks)


# ---------------------------------------------------------------------------
# _param_chunk_hash
# ---------------------------------------------------------------------------

class TestParamChunkHash:
    def test_deterministic_same_chunk(self):
        chunk = [{"signal_threshold": 0.1, "holding_period_bars": 5}]
        assert _param_chunk_hash(chunk) == _param_chunk_hash(chunk)

    def test_differs_on_different_params(self):
        c1 = [{"signal_threshold": 0.1, "holding_period_bars": 5}]
        c2 = [{"signal_threshold": 0.2, "holding_period_bars": 5}]
        assert _param_chunk_hash(c1) != _param_chunk_hash(c2)

    def test_differs_on_different_order(self):
        c1 = [{"signal_threshold": 0.1}, {"signal_threshold": 0.2}]
        c2 = [{"signal_threshold": 0.2}, {"signal_threshold": 0.1}]
        assert _param_chunk_hash(c1) != _param_chunk_hash(c2)

    def test_handles_none_values(self):
        chunk = [{"stop_loss_pct": None, "take_profit_pct": 1.0}]
        h = _param_chunk_hash(chunk)
        assert isinstance(h, str)
        assert len(h) == 32

    def test_key_order_independent_within_dict(self):
        """Sorted-key serialization means dict key order doesn't matter."""
        c1 = [{"a": 1, "b": 2}]
        c2 = [{"b": 2, "a": 1}]
        assert _param_chunk_hash(c1) == _param_chunk_hash(c2)

    def test_empty_chunk_hash_is_stable(self):
        h1 = _param_chunk_hash([])
        h2 = _param_chunk_hash([])
        assert h1 == h2


# ---------------------------------------------------------------------------
# _build_sl_tp_arrays
# ---------------------------------------------------------------------------

class TestBuildSlTpArrays:
    def test_correct_values(self):
        chunk = [
            {"stop_loss_pct": 0.5, "take_profit_pct": 1.0},
            {"stop_loss_pct": 2.0, "take_profit_pct": None},
        ]
        sl, tp = _build_sl_tp_arrays(chunk)
        assert sl == [0.005, 0.02]
        assert tp == [0.01, None]

    def test_none_handling(self):
        chunk = [
            {"stop_loss_pct": None, "take_profit_pct": None},
            {"stop_loss_pct": 1.0, "take_profit_pct": 1.0},
        ]
        sl, tp = _build_sl_tp_arrays(chunk)
        assert sl == [None, 0.01]
        assert tp == [None, 0.01]

    def test_all_none(self):
        chunk = [
            {"stop_loss_pct": None, "take_profit_pct": None},
            {"stop_loss_pct": None, "take_profit_pct": None},
        ]
        sl, tp = _build_sl_tp_arrays(chunk)
        assert sl == [None, None]
        assert tp == [None, None]

    def test_divides_by_100(self):
        chunk = [{"stop_loss_pct": 50.0, "take_profit_pct": 200.0}]
        sl, tp = _build_sl_tp_arrays(chunk)
        assert sl == [0.5]
        assert tp == [2.0]

    def test_empty_chunk(self):
        sl, tp = _build_sl_tp_arrays([])
        assert sl == []
        assert tp == []

    def test_missing_keys_default_none(self):
        chunk = [{"signal_threshold": 0.1}]  # no sl/tp keys
        sl, tp = _build_sl_tp_arrays(chunk)
        assert sl == [None]
        assert tp == [None]


class TestSlTpForPortfolio:
    """Rust engine sl/tp arrays must match column count, not bar count."""

    def test_rust_sl_tp_length_matches_trial_count_not_bars(self):
        n_trials = 64
        m_bars = 5
        chunk = [
            {"stop_loss_pct": 0.5 if i % 2 == 0 else None, "take_profit_pct": 1.0}
            for i in range(n_trials)
        ]
        sl_arr, tp_arr = _build_sl_tp_arrays(chunk)
        assert len(sl_arr) == n_trials
        assert len(tp_arr) == n_trials

        sl, tp = _sl_tp_for_portfolio(sl_arr, tp_arr, engine="rust")
        assert sl is not None
        assert tp is not None
        assert sl.shape == (n_trials,)
        assert tp.shape == (n_trials,)
        assert sl.dtype == np.float64
        assert tp.dtype == np.float64
        assert sl.shape != (m_bars,)

    def test_rust_partial_chunk_width(self):
        n_trials = 7
        chunk = [{"stop_loss_pct": 1.0, "take_profit_pct": None} for _ in range(n_trials)]
        sl_arr, tp_arr = _build_sl_tp_arrays(chunk)
        sl, tp = _sl_tp_for_portfolio(sl_arr, tp_arr, engine="rust")
        assert sl.shape == (n_trials,)
        assert tp is None

    def test_numba_engine_returns_column_ndarrays(self):
        chunk = [{"stop_loss_pct": 1.0, "take_profit_pct": 2.0}]
        sl_arr, tp_arr = _build_sl_tp_arrays(chunk)
        sl, tp = _sl_tp_for_portfolio(sl_arr, tp_arr, engine="numba")
        assert isinstance(sl, np.ndarray)
        assert isinstance(tp, np.ndarray)
        assert sl.shape == (1,)
        assert tp.shape == (1,)
        assert sl.dtype == np.float64
        assert tp.dtype == np.float64

    def test_numba_ndarray_width_matches_matrix_cols_not_bars(self):
        """Reproduces Vast repro128 failure: n_bars=5, n_cols=64 must not broadcast."""
        n_trials = 64
        m_bars = 5
        chunk = [
            {
                "stop_loss_pct": 0.5 if i % 2 == 0 else None,
                "take_profit_pct": 1.0,
                "holding_period_bars": 5,
            }
            for i in range(n_trials)
        ]
        sl_arr, tp_arr = _build_sl_tp_arrays(chunk)
        sl, tp = _sl_tp_for_portfolio(sl_arr, tp_arr, engine="numba")
        assert sl.shape == (n_trials,)
        assert tp.shape == (n_trials,)
        assert sl.shape != (m_bars,)

        try:
            import vectorbt as vbt
        except ImportError:
            pytest.skip("vectorbt not installed")

        close = np.ones((m_bars, n_trials))
        entries = np.zeros((m_bars, n_trials), dtype=bool)
        entries[0, :] = True
        exits = np.zeros((m_bars, n_trials), dtype=bool)
        exits[-1, :] = True
        pf = vbt.Portfolio.from_signals(
            close,
            entries=entries,
            exits=exits,
            sl_stop=sl,
            tp_stop=tp,
            freq="1min",
            engine="numba",
        )
        assert pf.wrapper.shape[1] == n_trials


# ---------------------------------------------------------------------------
# run_vectorbt_simulation_matrix — integration with fake VectorBT
# ---------------------------------------------------------------------------

class TestRunVectorbtSimulationMatrix:
    def _setup(self, monkeypatch, captured=None):
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals(captured))
        ohlcv = _synthetic_ohlcv(80)
        grid = {
            "signal_threshold": [0.1, 0.15, 0.2, 0.25],
            "holding_period_bars": [5, 15, 30, 60],
            "stop_loss_pct": [None, 0.5, 1.0, 2.0],
            "take_profit_pct": [None, 0.5, 1.0, 2.0],
        }
        return ohlcv, grid

    def test_same_result_count_as_loop_mode(self, monkeypatch, tmp_path):
        """Matrix mode produces exactly as many result rows as the loop mode."""
        captured_matrix: dict = {}
        ohlcv, grid = self._setup(monkeypatch, captured_matrix)
        cand = _mock_candidate("HYP_5", 0.15)

        result_matrix = run_vectorbt_simulation_matrix(
            ohlcv,
            [cand],
            parsed=None,
            grid=grid,
            repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            screening_scope="pilot",
            chunk_size=16,
        )

        # Loop mode with the same fake VectorBT
        captured_loop: dict = {}
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals(captured_loop))
        result_loop = _run_vectorbt_simulation(
            ohlcv,
            [cand],
            parsed=None,
            grid=grid,
            repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            screening_scope="pilot",
        )

        assert result_matrix.backend == "vectorbt"
        # Same total number of result rows (promoted + rejected)
        n_matrix = len(result_matrix.promoted) + len(result_matrix.rejected)
        n_loop = len(result_loop.promoted) + len(result_loop.rejected)
        assert n_matrix == n_loop
        # Both should have processed the same number of trials (pilot scope
        # caps max_trials at 32 by default).
        assert result_matrix.trials_run == result_loop.trials_run

    def test_parameter_ordering_is_deterministic(self, monkeypatch, tmp_path):
        """Running twice produces identical ordered candidate IDs."""
        ohlcv, grid = self._setup(monkeypatch)
        cand = _mock_candidate("HYP_5", 0.15)

        r1 = run_vectorbt_simulation_matrix(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            chunk_size=32,
        )
        r2 = run_vectorbt_simulation_matrix(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            chunk_size=32,
        )
        ids1 = [p.candidate_id for p in r1.promoted] + [r.candidate_id for r in r1.rejected]
        ids2 = [p.candidate_id for p in r2.promoted] + [r.candidate_id for r in r2.rejected]
        assert ids1 == ids2

    def test_chunk_boundary_independence(self, monkeypatch, tmp_path):
        """Results are identical regardless of chunk size."""
        ohlcv, grid = self._setup(monkeypatch)
        cand = _mock_candidate("HYP_5", 0.15)
        computer = _signal_computer_returns_fixed(1, -1)

        results = {}
        for cs in (1, 8, 64, 256, 1000):
            _install_fake_vectorbt(monkeypatch, _make_fake_from_signals())
            r = run_vectorbt_simulation_matrix(
                ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
                signal_computer=computer, chunk_size=cs,
            )
            ids = tuple(p.candidate_id for p in r.promoted) + tuple(
                r.candidate_id for r in r.rejected
            )
            results[cs] = ids

        # All chunk sizes produce the same ordered ID list
        reference = results[1]
        for cs, ids in results.items():
            assert ids == reference, f"chunk_size={cs} produced different ordering"

    def test_chunk_boundary_independence_metrics(self, monkeypatch, tmp_path):
        """Per-trial metrics are identical regardless of chunk size."""
        ohlcv, grid = self._setup(monkeypatch)
        cand = _mock_candidate("HYP_5", 0.15)
        computer = _signal_computer_returns_fixed(1, -1)

        def collect(cs):
            _install_fake_vectorbt(monkeypatch, _make_fake_from_signals())
            r = run_vectorbt_simulation_matrix(
                ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
                signal_computer=computer, chunk_size=cs,
            )
            # Map candidate_id -> vectorbt_results for promoted rows
            return {p.candidate_id: p.vectorbt_results for p in r.promoted}

        small = collect(1)
        big = collect(64)
        assert set(small.keys()) == set(big.keys())
        for cid in small:
            assert small[cid] == big[cid], f"metrics differ for {cid}"

    def test_no_lookahead_shift_applied_per_column(self, monkeypatch, tmp_path):
        """The entries/exits passed to from_signals are shifted by one bar."""
        captured: dict = {}
        ohlcv, grid = self._setup(monkeypatch, captured)
        cand = _mock_candidate("HYP_5", 0.15)

        def computer(cand, bars, parsed, repo_root):
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[0] = 1.0  # signal at bar 0
            exit_[1] = -1.0
            return entry, exit_

        run_vectorbt_simulation_matrix(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=computer,
            chunk_size=4,
        )
        entries = captured["entries"]
        # After shift, entry at bar 0 should appear at bar 1 in every column
        n_cols = entries.shape[1]
        for col in range(n_cols):
            assert bool(entries[1, col]) is True, f"col {col} not shifted"
            assert bool(entries[0, col]) is False, f"col {col} has unshifted entry"

    def test_per_column_stats_extraction(self, monkeypatch, tmp_path):
        """Per-column stats via pf.stats(column=...) are extracted."""
        captured: dict = {}
        ohlcv, grid = self._setup(monkeypatch, captured)
        cand = _mock_candidate("HYP_5", 0.15)

        # Use a param-dependent signal so different columns have different stats
        def computer(cand, bars, parsed, repo_root):
            threshold = float(cand.strategy_params["signal_threshold"])
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            # Higher threshold -> entry later in the bar series
            entry_idx = min(int(threshold * 40), len(bars) - 2)
            entry[entry_idx] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = run_vectorbt_simulation_matrix(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=computer, chunk_size=16,
        )
        # All promoted rows should have complete stats (no missing-gate rejections)
        assert len(result.promoted) > 0
        for prom in result.promoted:
            stats = prom.vectorbt_results["vbt_stats"]
            assert "Total Trades" in stats
            assert "Expectancy" in stats
            assert prom.vectorbt_results["gate_metric_authority"] == "official_vectorbt_portfolio_stats"

    def test_candidate_id_matches_loop_mode(self, monkeypatch, tmp_path):
        """Per-trial candidate IDs are identical to loop mode."""
        ohlcv, grid = self._setup(monkeypatch)
        cand = _mock_candidate("HYP_5", 0.15)
        computer = _signal_computer_returns_fixed(1, -1)

        r_matrix = run_vectorbt_simulation_matrix(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=computer, chunk_size=32,
        )
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals())
        r_loop = _run_vectorbt_simulation(
            ohlcv, [cand], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=computer, screening_scope="pilot",
        )
        ids_matrix = set(
            p.candidate_id for p in r_matrix.promoted
        ) | set(r.candidate_id for r in r_matrix.rejected)
        ids_loop = set(
            p.candidate_id for p in r_loop.promoted
        ) | set(r.candidate_id for r in r_loop.rejected)
        assert ids_matrix == ids_loop

    def test_fail_closed_without_vectorbt(self, monkeypatch, tmp_path):
        """When VectorBT is unavailable, the screen fails closed."""
        monkeypatch.setattr(vectorbt_adapter, "_has_vectorbt", False)
        monkeypatch.setattr(vectorbt_adapter, "_vectorbt_version", None)
        monkeypatch.setattr(vectorbt_adapter, "_rust_engine_available", False)
        # _screening_engine_metadata caches; force re-evaluation by clearing cache if any
        ohlcv = _synthetic_ohlcv(40)
        result = run_vectorbt_simulation_matrix(
            ohlcv, [_mock_candidate()], parsed=None,
            grid={"signal_threshold": [0.1], "holding_period_bars": [5],
                  "stop_loss_pct": [None], "take_profit_pct": [None]},
            repo_root=tmp_path,
        )
        assert result.backend == "vectorbt_unavailable"
        assert all(r.reject_reason == "vectorbt_unavailable_fail_closed" for r in result.rejected)
        assert len(result.promoted) == 0

    def test_signal_failure_rejects_trial(self, monkeypatch, tmp_path):
        """A signal computer that raises rejects the trial, not the whole chunk."""
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals())
        ohlcv = _synthetic_ohlcv(40)
        grid = {
            "signal_threshold": [0.1, 0.15],
            "holding_period_bars": [5],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        }
        call_count = {"n": 0}

        def computer(cand, bars, parsed, repo_root):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            entry = np.zeros(len(bars))
            exit_ = np.zeros(len(bars))
            entry[1] = 1.0
            exit_[-1] = -1.0
            return entry, exit_

        result = run_vectorbt_simulation_matrix(
            ohlcv, [_mock_candidate()], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=computer, chunk_size=4,
        )
        # One rejected (signal failure) + one promoted
        rejected_signal = [r for r in result.rejected if r.reject_reason == "unresolvable_model_id"]
        assert len(rejected_signal) == 1
        assert len(result.promoted) == 1

    def test_max_total_trials_respected(self, monkeypatch, tmp_path):
        """trials_run does not exceed max_total_trials budget; remaining trials
        are appended as RUN_BUDGET_REACHED rejections (same as loop mode)."""
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals())
        ohlcv = _synthetic_ohlcv(40)
        grid = {
            "signal_threshold": [0.1, 0.15, 0.2, 0.25],
            "holding_period_bars": [5, 15, 30, 60],
            "stop_loss_pct": [None, 0.5, 1.0, 2.0],
            "take_profit_pct": [None, 0.5, 1.0, 2.0],
        }
        result = run_vectorbt_simulation_matrix(
            ohlcv, [_mock_candidate()], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            max_total_trials=10,
            chunk_size=16,
        )
        # Pilot scope caps max_trials at 32, so grid_trials has 32 entries.
        # max_total_trials=10 limits actual runs to 10; the remaining 22 are
        # appended as RUN_BUDGET_REACHED skipped trials.
        assert result.trials_run == 10
        n_rows = len(result.promoted) + len(result.rejected)
        assert n_rows == 32
        skipped = [r for r in result.rejected if r.reject_reason == "RUN_BUDGET_REACHED"]
        assert len(skipped) == 32 - 10

    def test_sl_tp_kwargs_match_matrix_column_count(self, monkeypatch, tmp_path):
        """sl_stop/tp_stop passed to from_signals must match entry column count."""
        captured: dict = {}
        ohlcv, grid = self._setup(monkeypatch, captured)
        cand = _mock_candidate("HYP_5", 0.15)

        run_vectorbt_simulation_matrix(
            ohlcv,
            [cand],
            parsed=None,
            grid=grid,
            repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            screening_scope="pilot",
            chunk_size=16,
        )

        entries = np.asarray(captured["entries"])
        n_cols = int(entries.shape[1])
        assert n_cols > 0
        sl = captured["kwargs"].get("sl_stop")
        tp = captured["kwargs"].get("tp_stop")
        if sl is not None:
            sl_len = int(sl.shape[0]) if isinstance(sl, np.ndarray) else len(sl)
            assert sl_len == n_cols
        if tp is not None:
            tp_len = int(tp.shape[0]) if isinstance(tp, np.ndarray) else len(tp)
            assert tp_len == n_cols

    def test_uses_build_signal_matrix_helper_path(self, monkeypatch, tmp_path):
        """When all columns share a param-independent signal, the matrix has the
        expected [bars, n_cols] shape passed to from_signals."""
        captured: dict = {}
        _install_fake_vectorbt(monkeypatch, _make_fake_from_signals(captured))
        ohlcv = _synthetic_ohlcv(40)
        grid = {
            "signal_threshold": [0.1, 0.15],
            "holding_period_bars": [5, 15],
            "stop_loss_pct": [None],
            "take_profit_pct": [None],
        }
        run_vectorbt_simulation_matrix(
            ohlcv, [_mock_candidate()], parsed=None, grid=grid, repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            chunk_size=4,
        )
        entries = np.asarray(captured["entries"])
        # 4 trials in one chunk -> 4 columns
        assert entries.ndim == 2
        assert entries.shape[0] == 40
        assert entries.shape[1] == 4

    def test_default_chunk_size_constant(self):
        """DEFAULT_MATRIX_CHUNK_SIZE is a positive integer."""
        assert isinstance(DEFAULT_MATRIX_CHUNK_SIZE, int)
        assert DEFAULT_MATRIX_CHUNK_SIZE > 0

    def test_promoted_vectorbt_results_include_feature_recipe(self, monkeypatch, tmp_path):
        """Matrix promotions carry feature_recipe fields like loop mode."""
        ohlcv, grid = self._setup(monkeypatch)
        recipe = {
            "feature_recipe_hash": "recipe_hash_abc",
            "feature_slots": [{"slot_id": "spread_z", "family": "microstructure"}],
        }
        cand = _mock_candidate("HYP_5", 0.15)
        cand.feature_recipe_hash = "recipe_hash_abc"
        cand.feature_recipe = recipe

        result = run_vectorbt_simulation_matrix(
            ohlcv,
            [cand],
            parsed=None,
            grid=grid,
            repo_root=tmp_path,
            signal_computer=_signal_computer_returns_fixed(1, -1),
            screening_scope="pilot",
            chunk_size=16,
        )

        assert len(result.promoted) > 0
        for prom in result.promoted:
            assert prom.vectorbt_results["feature_recipe_hash"] == "recipe_hash_abc"
            assert prom.vectorbt_results["feature_recipe"] == recipe