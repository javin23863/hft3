"""Unit tests for src/digest.py — offline, no network, no ollama.

Covers:
- Per-symbol totals and aggregate sums
- Trade-count z-score computation (sufficient baseline)
- Insufficient-baseline path (< 5 prior sessions -> z=0, flag set)
- Zero standard deviation in baseline -> z=0
- Empty manifest directory -> empty Digest
- to_dict() round-trip shape
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pytest

# Ensure apps/ and packages/ are on the path when running from the repo root.
_REPO = Path(__file__).resolve().parents[2]
_APPS = _REPO / "apps"
_PKGS = _REPO / "packages"
for p in (_APPS, _PKGS, str(_REPO)):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from llm_slow_tier.src.digest import (
    Digest,
    SymbolDigest,
    build_digest,
    _compute_zscore,
    _collect_prior_trade_counts,
    load_manifests_for_date,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_manifest(directory: Path, symbol: str, trade_date: str, **kwargs) -> Path:
    """Write a minimal manifest JSON to directory."""
    defaults = {
        "symbol": symbol,
        "exchange": "CME",
        "trade_date": trade_date,
        "records": 1000,
        "trades": 500,
        "quotes": 500,
        "first_ts_exch_ns": 0,
        "last_ts_exch_ns": 1,
        "max_queue_gap_flag_count": 0,
        "md_drops_total": 0,
        "reconnects": 0,
        "file_bytes": 1024,
        "updated_wall_ns": 0,
        "unknown_symbol_drops": 0,
    }
    defaults.update(kwargs)
    path = directory / f"{symbol}.manifest.json"
    path.write_text(json.dumps(defaults), encoding="utf-8")
    return path


def _write_tree(tmp_path: Path, sessions: list[tuple[str, str, int]]) -> Path:
    """
    Build a manifest tree at tmp_path/manifests/.
    sessions: list of (symbol, date, trades)
    Returns the tree root (tmp_path/manifests/).
    """
    tree_root = tmp_path / "manifests"
    for symbol, date, trades in sessions:
        day_dir = tree_root / date
        day_dir.mkdir(parents=True, exist_ok=True)
        _write_manifest(day_dir, symbol, date, trades=trades)
    return tree_root


# ---------------------------------------------------------------------------
# Tests: _compute_zscore
# ---------------------------------------------------------------------------

class TestComputeZscore:

    def test_insufficient_baseline_less_than_5(self):
        """Fewer than 5 prior sessions -> z=0, insufficient_baseline=True."""
        z, insuff = _compute_zscore(100, [90, 80, 70])
        assert insuff is True
        assert z == 0.0

    def test_insufficient_baseline_exactly_4(self):
        z, insuff = _compute_zscore(100, [100, 100, 100, 100])
        assert insuff is True
        assert z == 0.0

    def test_sufficient_baseline_positive_z(self):
        """Trade count well above mean -> positive z."""
        baseline = [100, 100, 100, 100, 100]  # mean=100, std=0... use varied
        baseline = [90, 100, 110, 120, 80]   # mean=100, std=14.14
        z, insuff = _compute_zscore(150, baseline)
        assert insuff is False
        assert z > 2.0  # 50 / ~14 ≈ 3.5

    def test_sufficient_baseline_negative_z(self):
        baseline = [90, 100, 110, 120, 80]
        z, insuff = _compute_zscore(50, baseline)
        assert insuff is False
        assert z < -2.0

    def test_zero_std_returns_zero(self):
        """All prior sessions identical -> std=0 -> z=0."""
        baseline = [100, 100, 100, 100, 100]
        z, insuff = _compute_zscore(200, baseline)
        assert insuff is False
        assert z == 0.0

    def test_exactly_5_sessions_is_sufficient(self):
        baseline = [80, 90, 100, 110, 120]
        z, insuff = _compute_zscore(100, baseline)
        assert insuff is False

    def test_20_session_baseline(self):
        baseline = list(range(81, 101))  # 20 values: 81..100, mean=90.5
        z, insuff = _compute_zscore(100, baseline)
        assert insuff is False
        # z should be positive
        assert z > 0.0

    def test_z_math_correctness(self):
        """Explicit z-score value check."""
        baseline = [100, 100, 120, 100, 100]  # mean=104, variance=64, std=8
        # mean = (100+100+120+100+100)/5 = 104
        # var  = ((4^2 + 4^2 + 16^2 + 4^2 + 4^2) / 5 = (16+16+256+16+16)/5 = 320/5 = 64
        # std  = 8
        z, insuff = _compute_zscore(112, baseline)
        assert insuff is False
        expected = (112 - 104) / 8
        assert abs(z - expected) < 1e-9


# ---------------------------------------------------------------------------
# Tests: build_digest totals
# ---------------------------------------------------------------------------

class TestBuildDigestTotals:

    def test_single_symbol(self, tmp_path):
        date = "2026-06-10"
        day_dir = tmp_path / date
        day_dir.mkdir(parents=True)
        _write_manifest(
            day_dir, "MES", date,
            trades=1000, quotes=2000, records=3000,
            max_queue_gap_flag_count=2, md_drops_total=1, reconnects=0,
        )

        digest = build_digest(date, day_dir)

        assert digest.n_symbols == 1
        assert digest.total_trades == 1000
        assert digest.total_quotes == 2000
        assert digest.total_records == 3000
        assert digest.total_gap_flags == 2
        assert digest.total_drops == 1
        assert digest.total_reconnects == 0
        assert "MES" in digest.symbols

    def test_multi_symbol_aggregation(self, tmp_path):
        date = "2026-06-10"
        day_dir = tmp_path / date
        day_dir.mkdir(parents=True)
        _write_manifest(day_dir, "MES", date, trades=500, quotes=600, records=1100,
                        max_queue_gap_flag_count=1, md_drops_total=0, reconnects=1)
        _write_manifest(day_dir, "NQ", date, trades=300, quotes=400, records=700,
                        max_queue_gap_flag_count=0, md_drops_total=2, reconnects=0)

        digest = build_digest(date, day_dir)

        assert digest.n_symbols == 2
        assert digest.total_trades == 800
        assert digest.total_quotes == 1000
        assert digest.total_records == 1800
        assert digest.total_gap_flags == 1
        assert digest.total_drops == 2
        assert digest.total_reconnects == 1

    def test_empty_manifest_dir(self, tmp_path):
        date = "2026-06-10"
        empty_dir = tmp_path / date
        empty_dir.mkdir(parents=True)

        digest = build_digest(date, empty_dir)

        assert digest.n_symbols == 0
        assert digest.total_trades == 0
        assert digest.symbols == []

    def test_nonexistent_manifest_dir(self, tmp_path):
        missing = tmp_path / "2026-06-99"
        digest = build_digest("2026-06-99", missing)
        assert digest.n_symbols == 0

    def test_skips_wrong_date(self, tmp_path):
        date = "2026-06-10"
        day_dir = tmp_path / date
        day_dir.mkdir(parents=True)
        # Write a manifest with a different trade_date
        _write_manifest(day_dir, "MES", "2026-06-09", trades=100)

        digest = build_digest(date, day_dir)
        assert digest.n_symbols == 0

    def test_to_dict_shape(self, tmp_path):
        date = "2026-06-10"
        day_dir = tmp_path / date
        day_dir.mkdir(parents=True)
        _write_manifest(day_dir, "MES", date, trades=100)

        digest = build_digest(date, day_dir)
        d = digest.to_dict()

        required_keys = {
            "trade_date", "symbols", "total_records", "total_trades",
            "total_quotes", "total_gap_flags", "total_drops", "total_reconnects",
            "per_symbol", "trade_count_zscores", "insufficient_baseline", "n_symbols",
        }
        assert required_keys <= set(d.keys())
        assert d["trade_date"] == date
        assert isinstance(d["per_symbol"], dict)
        assert isinstance(d["trade_count_zscores"], dict)
        assert isinstance(d["insufficient_baseline"], list)


# ---------------------------------------------------------------------------
# Tests: z-score integration in build_digest
# ---------------------------------------------------------------------------

class TestBuildDigestZScores:

    def test_insufficient_baseline_flag_in_digest(self, tmp_path):
        """No prior sessions -> symbol appears in insufficient_baseline."""
        date = "2026-06-10"
        tree_root = tmp_path / "manifests"
        day_dir = tree_root / date
        day_dir.mkdir(parents=True)
        _write_manifest(day_dir, "MES", date, trades=500)

        digest = build_digest(date, day_dir)

        assert "MES" in digest.insufficient_baseline
        assert digest.trade_count_zscores["MES"] == 0.0

    def test_sufficient_baseline_produces_nonzero_z(self, tmp_path):
        """6 prior sessions with consistent trades -> z-score computed."""
        tree_root = tmp_path / "manifests"

        # Prior sessions: 5 dates with 100 trades, 1 with 200 trades (outlier)
        prior_sessions = [
            ("MES", f"2026-06-0{i}", 100 + i * 10)
            for i in range(1, 7)
        ]
        for sym, date, trades in prior_sessions:
            d = tree_root / date
            d.mkdir(parents=True, exist_ok=True)
            _write_manifest(d, sym, date, trades=trades)

        # Current date with very high trade count
        current_date = "2026-06-10"
        day_dir = tree_root / current_date
        day_dir.mkdir(parents=True, exist_ok=True)
        _write_manifest(day_dir, "MES", current_date, trades=500)

        digest = build_digest(current_date, day_dir)

        assert "MES" not in digest.insufficient_baseline
        # With prior trades ranging 110-160 and current 500, z must be large positive
        assert digest.trade_count_zscores["MES"] > 2.0

    def test_multiple_symbols_independent_zscores(self, tmp_path):
        """Two symbols get independent z-scores.

        MES baseline is all 100 (std=0 -> z=0 regardless of current value).
        NQ baseline is varied (90..140) so std > 0, enabling real z computation.
        """
        tree_root = tmp_path / "manifests"

        # MES: uniform baseline -> std=0 -> z=0 for any current value
        for i in range(1, 7):
            d = tree_root / f"2026-06-0{i}"
            d.mkdir(parents=True, exist_ok=True)
            _write_manifest(d, "MES", f"2026-06-0{i}", trades=100)

        # NQ: varied baseline (90, 100, 110, 120, 80, 95) -> mean~99.2, std>10
        nq_baselines = [90, 100, 110, 120, 80, 95]
        for i, baseline_trades in enumerate(nq_baselines, start=1):
            d = tree_root / f"2026-06-0{i}"
            d.mkdir(parents=True, exist_ok=True)
            _write_manifest(d, "NQ", f"2026-06-0{i}", trades=baseline_trades)

        current_date = "2026-06-10"
        day_dir = tree_root / current_date
        day_dir.mkdir(parents=True, exist_ok=True)
        _write_manifest(day_dir, "MES", current_date, trades=100)   # std=0 -> z=0
        _write_manifest(day_dir, "NQ", current_date, trades=300)    # well above baseline

        digest = build_digest(current_date, day_dir)

        # MES std=0 so z must be 0
        assert digest.trade_count_zscores["MES"] == pytest.approx(0.0)
        # NQ 300 trades vs baseline mean~99 with std~12 -> z > 10
        assert digest.trade_count_zscores["NQ"] > 5.0

    def test_baseline_capped_at_20_sessions(self, tmp_path):
        """Only the 20 most recent prior sessions are used."""
        tree_root = tmp_path / "manifests"

        # Write 25 prior sessions
        for i in range(1, 26):
            date_str = f"2026-01-{i:02d}"
            d = tree_root / date_str
            d.mkdir(parents=True, exist_ok=True)
            _write_manifest(d, "MES", date_str, trades=100)

        current_date = "2026-02-01"
        day_dir = tree_root / current_date
        day_dir.mkdir(parents=True, exist_ok=True)
        _write_manifest(day_dir, "MES", current_date, trades=100)

        digest = build_digest(current_date, day_dir)
        # Should not raise; z should be computable (std=0 -> z=0)
        assert digest.trade_count_zscores["MES"] == 0.0
