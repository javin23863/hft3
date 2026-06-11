"""Consistency test: Stage A's searchsorted PIT loop must produce the same
hyp.evaluate(state) values as ReplaySession's strategy sees at every
decision timestamp for the same feature-store NPZ.

Alignment strategy
------------------
ReplaySession uses event-stepping (step_mode="event").  The session records
(ts, signal) at each ``on_step`` call.  Stage A iterates over every ts[i] in
the feature store, computes the visible row via::

    j = searchsorted(ts, ts[i] - feat_latency_ns, side="right") - 1

and evaluates hyp.evaluate(state_j).

The two grids are NOT identical: ReplaySession fires at HftBacktest feed
events (derived from the raw MBO NPZ), while Stage A fires at feature-store
rows (also derived from the same MBO NPZ after the C++ pipeline).  We
therefore intersect the two timestamp sets and require:

1. The intersection is non-empty.
2. Every common timestamp produces byte-identical float64 signal values
   (np.allclose atol=0 rtol=0).

This test is cpp-guarded: it skips when ``hft3_features_cpp`` is not built
(same pattern as ``test_feature_store/test_build_determinism.py``).
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


# ---------------------------------------------------------------------------
# cpp guard
# ---------------------------------------------------------------------------

def _cpp_available() -> bool:
    try:
        from features_engine.src.features._cpp_loader import load_cpp_features
        return load_cpp_features() is not None
    except Exception:
        return False


_CPP_PRESENT = _cpp_available()
_SKIP_NO_CPP = pytest.mark.skipif(
    not _CPP_PRESENT,
    reason="hft3_features_cpp not built (cmake --build build)",
)


# ---------------------------------------------------------------------------
# Script loader (same pattern as test_build_determinism.py)
# ---------------------------------------------------------------------------

def _load_build_script():
    script = _REPO / "scripts" / "build_feature_store.py"
    spec = importlib.util.spec_from_file_location("build_feature_store", script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def _make_lake_npz(dest: Path) -> Path:
    from backtest_pipeline.src.replay_npz_fixture import build_minimal_mbo_npz
    build_minimal_mbo_npz(dest)
    return dest


def _build_feature_store(bmod, npz_path: Path, out_root: Path) -> dict:
    import hashlib
    h = hashlib.sha256()
    with npz_path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    source_sha = h.hexdigest()

    unit = {
        "symbol": "MES.v.0",
        "event_id": "CONS_EVT",
        "npz_path": str(npz_path),
        "tick_size": 0.25,
        "out_root": str(out_root),
        "event_type": "CPI",
        "release_date": "2024-03-10",
        "git_commit": "test",
        "source_npz_sha256": source_sha,
    }
    os.environ["HFT3_FEATURE_BACKEND"] = "cpp"
    return bmod._worker(unit)


# ---------------------------------------------------------------------------
# Instrumented strategy: records (ts_ns, {hyp_id: signal}) per step
# ---------------------------------------------------------------------------

class _RecordingStrategy:
    """Never orders; records (ts, hyp.evaluate(state)) at every step."""

    def __init__(self, hypotheses, hyp_ids_to_track):
        self._hyps = {h.hyp_id: h for h in hypotheses if h.hyp_id in hyp_ids_to_track}
        # records: list of (ts_ns, {hyp_id: float})
        self.records: List[Tuple[int, Dict[int, float]]] = []

    def on_step(self, ctx):
        if ctx.market_state is None:
            return []
        ts = int(ctx.clock.now_ns)
        sigs = {}
        for hid, hyp in self._hyps.items():
            sigs[hid] = float(hyp.evaluate(ctx.market_state))
        self.records.append((ts, sigs))
        return []


# ---------------------------------------------------------------------------
# Stage A visible-row loop (replicated from stage_a_screen._worker, lines 289-309)
# ---------------------------------------------------------------------------

def _stage_a_signals(
    store: dict,
    hypotheses,
    hyp_ids_to_track,
    band_ms: float,
) -> Dict[int, List[Tuple[int, float]]]:
    """
    Replicates stage_a_screen's decision loop for the given hypotheses.
    Returns {hyp_id: [(ts_ns, signal), ...]} parallel to Stage A row i.
    """
    from features_engine.src.hypotheses.modules import MarketState
    from research_pipeline.src.stage_a_screen import (
        REGIME_LABELS_ORDERED,
        _REGIME_SLOT_START,
    )

    ts: np.ndarray = store["ts"]
    X: np.ndarray = store["X"]
    event_ctx_id: np.ndarray = store["event_ctx_id"]
    event_ctx_vocab: list = list(store["event_ctx_vocab"])
    regime_state_id: np.ndarray = store["regime_state_id"]
    regime_state_vocab: list = list(store["regime_state_vocab"])
    vol_state_id: np.ndarray = store["vol_state_id"]
    vol_state_vocab: list = list(store["vol_state_vocab"])
    liq_state_id: np.ndarray = store["liq_state_id"]
    liq_state_vocab: list = list(store["liq_state_vocab"])

    feat_latency_ns = int(band_ms * 1_000_000)
    N = len(ts)

    hyps = {h.hyp_id: h for h in hypotheses if h.hyp_id in hyp_ids_to_track}

    state = MarketState(
        primary_features={},
        cross_asset_features={},
        regime_state="normal",
        event_context="NORMAL",
        volatility_state="NORMAL",
        liquidity_state="NORMAL",
        latency_ms=band_ms,
        current_inventory=0,
        feature_vector=None,
        regime_posterior={},
    )

    out: Dict[int, List[Tuple[int, float]]] = {hid: [] for hid in hyps}

    for i in range(N):
        vis_ts = int(ts[i]) - feat_latency_ns
        j = int(np.searchsorted(ts, vis_ts, side="right")) - 1
        if j < 0:
            continue

        vec_j = X[j]
        state.feature_vector = vec_j
        state.primary_features = {}
        state.event_context = (
            str(event_ctx_vocab[int(event_ctx_id[j])]) if event_ctx_vocab else "NORMAL"
        )
        state.regime_state = (
            str(regime_state_vocab[int(regime_state_id[j])]) if regime_state_vocab else "normal"
        )
        state.volatility_state = (
            str(vol_state_vocab[int(vol_state_id[j])]) if vol_state_vocab else "NORMAL"
        )
        state.liquidity_state = (
            str(liq_state_vocab[int(liq_state_id[j])]) if liq_state_vocab else "NORMAL"
        )
        state.regime_posterior = {
            lbl: float(vec_j[_REGIME_SLOT_START + k])
            for k, lbl in enumerate(REGIME_LABELS_ORDERED)
        }
        state.cross_asset_features = {}

        decision_ts = int(ts[i])
        for hid, hyp in hyps.items():
            out[hid].append((decision_ts, float(hyp.evaluate(state))))

    return out


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

class TestStageAReplayConsistency:

    @_SKIP_NO_CPP
    def test_signal_identical_on_common_timestamps(self, tmp_path):
        """
        At every timestamp present in BOTH the ReplaySession record and the
        Stage A loop, hyp.evaluate(state) must be byte-identical (atol=0, rtol=0).

        Hypotheses tested: hyp_id 1 (SecondWaveContinuation — pure aggressor_volume_imbalance)
        and hyp_id 7 (ForcedLiquidationCascade — aggressor_volume_imbalance + spread_stress +
        cancel_to_add_ratio), both pure-feature (no VIX, no cross-asset).

        Timestamp alignment: ReplaySession fires at HftBacktest feed events;
        Stage A fires at feature-store rows.  We intersect the two timestamp
        sets (exact int64 match) and assert the intersection is non-empty.
        Both paths call the identical MarketState.feature_vector path so the
        same float64 bits are consumed by hyp.evaluate — byte-identical result
        is guaranteed by construction when the feature vector is identical.
        """
        # Build feature store from fixture NPZ
        bmod = _load_build_script()
        npz_path = tmp_path / "MES.v.0_CONS_EVT_mbo.npz"
        _make_lake_npz(npz_path)
        out_root = tmp_path / "features"
        out_root.mkdir()

        result = _build_feature_store(bmod, npz_path, out_root)
        assert result["status"] == "built", f"feature store build failed: {result.get('error')}"

        from data_system.src.feature_store import load_store, store_path
        store_file = store_path(out_root, "MES.v.0", "CONS_EVT")
        store = load_store(store_file)

        from features_engine.src.hypotheses.registry import get_active_hypotheses
        hypotheses = get_active_hypotheses()

        TRACK = frozenset({1, 7})
        BAND_MS = 1.0

        # --- Stage A path ---
        stage_a_sigs = _stage_a_signals(store, hypotheses, TRACK, BAND_MS)

        # Build ts -> signal dict for O(1) lookup
        stage_a_by_ts: Dict[int, Dict[int, float]] = {}
        for hid, pairs in stage_a_sigs.items():
            for (ts_ns, sig) in pairs:
                if ts_ns not in stage_a_by_ts:
                    stage_a_by_ts[ts_ns] = {}
                stage_a_by_ts[ts_ns][hid] = sig

        # --- ReplaySession path ---
        # Use the same fixture NPZ directly (not the feature store NPZ).
        # The instrumented strategy records (ts, evaluate) at each step.
        # We set threshold effectively infinite so no orders are submitted.
        from features_engine.src.hypotheses.modules import SecondWaveContinuation, ForcedLiquidationCascade
        tracked_hyps = [SecondWaveContinuation(), ForcedLiquidationCascade()]
        strategy = _RecordingStrategy(tracked_hyps, TRACK)

        # ReplaySession needs the raw MBO npz, not the feature store npz.
        from replay.replay_session import ReplaySession, ReplaySessionConfig
        cfg = ReplaySessionConfig(
            npz_path=str(npz_path),
            latency_ms=BAND_MS,
            feature_latency_ms=BAND_MS,
            symbol="MES",
            product="MES",
            tick_size=0.25,
            lot_size=1.0,
            queue_model="LogProbQueueModel2",
            step_mode="event",
            seed=0,
        )
        session = ReplaySession(cfg, strategy)
        session.run()

        assert len(strategy.records) > 0, "ReplaySession produced no steps — fixture too small?"

        # --- Intersect timestamps ---
        replay_by_ts: Dict[int, Dict[int, float]] = {}
        for (ts_ns, sigs) in strategy.records:
            replay_by_ts[ts_ns] = sigs

        common_ts = sorted(set(stage_a_by_ts.keys()) & set(replay_by_ts.keys()))

        # Diagnostic info for failures
        assert len(common_ts) > 0, (
            f"Empty timestamp intersection: Stage A has {len(stage_a_by_ts)} unique ts, "
            f"ReplaySession has {len(replay_by_ts)} unique ts. "
            "Both derive from the same MBO NPZ — check that feature_latency_ms and "
            "feat_latency_ns use the same band_ms."
        )

        # --- Assert byte-identical signals on the intersection ---
        mismatches = []
        for ts_ns in common_ts:
            for hid in TRACK:
                if hid not in stage_a_by_ts.get(ts_ns, {}):
                    continue
                if hid not in replay_by_ts.get(ts_ns, {}):
                    continue
                sa_val = stage_a_by_ts[ts_ns][hid]
                rp_val = replay_by_ts[ts_ns][hid]
                if not np.isclose(sa_val, rp_val, atol=0.0, rtol=0.0, equal_nan=True):
                    mismatches.append((ts_ns, hid, sa_val, rp_val))

        assert len(mismatches) == 0, (
            f"Signal mismatch at {len(mismatches)} (ts, hyp_id) points "
            f"(intersection size={len(common_ts)}). "
            f"First 5 mismatches: {mismatches[:5]}"
        )

    @_SKIP_NO_CPP
    def test_intersection_covers_meaningful_fraction(self, tmp_path):
        """Intersection of Stage-A ts and ReplaySession ts is non-trivially large.

        Both grids originate from the same MBO NPZ so the intersection must
        include at least 50% of the smaller grid (conservative lower bound;
        in practice they are nearly equal for event-stepping).
        """
        bmod = _load_build_script()
        npz_path = tmp_path / "MES.v.0_CONS_EVT2_mbo.npz"
        _make_lake_npz(npz_path)
        out_root = tmp_path / "features2"
        out_root.mkdir()

        import hashlib
        h = hashlib.sha256()
        with npz_path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        source_sha = h.hexdigest()

        unit = {
            "symbol": "MES.v.0",
            "event_id": "CONS_EVT2",
            "npz_path": str(npz_path),
            "tick_size": 0.25,
            "out_root": str(out_root),
            "event_type": "CPI",
            "release_date": "2024-03-11",
            "git_commit": "test",
            "source_npz_sha256": source_sha,
        }
        os.environ["HFT3_FEATURE_BACKEND"] = "cpp"
        r = bmod._worker(unit)
        assert r["status"] == "built", r.get("error")

        from data_system.src.feature_store import load_store, store_path
        from features_engine.src.hypotheses.registry import get_active_hypotheses

        store = load_store(store_path(out_root, "MES.v.0", "CONS_EVT2"))
        hypotheses = get_active_hypotheses()
        TRACK = frozenset({1})
        BAND_MS = 1.0

        stage_a_sigs = _stage_a_signals(store, hypotheses, TRACK, BAND_MS)
        stage_a_ts_set = set(ts for ts, _ in stage_a_sigs[1])

        from features_engine.src.hypotheses.modules import SecondWaveContinuation
        strategy = _RecordingStrategy([SecondWaveContinuation()], TRACK)

        from replay.replay_session import ReplaySession, ReplaySessionConfig
        cfg = ReplaySessionConfig(
            npz_path=str(npz_path),
            latency_ms=BAND_MS,
            feature_latency_ms=BAND_MS,
            symbol="MES",
            product="MES",
            tick_size=0.25,
            lot_size=1.0,
            queue_model="LogProbQueueModel2",
            step_mode="event",
            seed=0,
        )
        ReplaySession(cfg, strategy).run()

        replay_ts_set = {ts for ts, _ in strategy.records}
        common = stage_a_ts_set & replay_ts_set
        smaller = min(len(stage_a_ts_set), len(replay_ts_set))
        if smaller > 0:
            ratio = len(common) / smaller
            assert ratio >= 0.5, f"Intersection ratio={ratio:.2%} < 50%"
        # Always pass fraction check — the grids may be disjoint for tiny fixtures;
        # the important assertion is in the first test (byte-identity on common ts).
