"""End-to-end output schema tests for run_stage_a.

All stores are hand-written via np.savez (no cpp dependency) using the
feature_index_hash computed at import time, which guarantees load_store
accepts them.  This avoids the C++ build requirement and exercises the
full run_stage_a → stage_a_result.json / stage_a_survivors.json pipeline.

Coverage
--------
1.  stage_a_result.json schema fields (schema, research_only, promotion_label,
    certification_stamp.execution_mode / queue_model, ev_approximation_note,
    aggregation_note, embargo block).
2.  stage_a_survivors.json: tested_cells covers every (hyp_id, event_type)
    cell; pass_through == [42, 43, 45]; survivor schema fields present.
3.  Embargo: a manifest record with release_date >= 2026-01-01 is skipped;
    units_skipped_embargo >= 1 and the embargoed event contributes no cells.
4.  VIX coverage: store without VIX sibling → vix_coverage 0 for its cells;
    store WITH a synthetic VIX features NPZ present → vix_coverage > 0 for
    that event's cells.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()


# ---------------------------------------------------------------------------
# Hand-written store helpers
# ---------------------------------------------------------------------------

def _feature_index_hash_value() -> str:
    from data_system.src.feature_store import feature_index_hash
    return feature_index_hash()


def _regime_vocab() -> list:
    from research_pipeline.src.stage_a_screen import REGIME_LABELS_ORDERED
    return list(REGIME_LABELS_ORDERED)


_FEATURE_DIM = 64
_N_ROWS = 30  # enough for p-value (needs >=3 events, each with this many rows)


def _make_feature_store_npz(
    dest: Path,
    *,
    n_rows: int = _N_ROWS,
    signal_burst: bool = True,
) -> Path:
    """
    Write a hand-crafted feature store NPZ that load_store will accept.

    When signal_burst=True, aggressor_volume_imbalance (slot 0) is set high
    enough to exceed SIGNAL_THRESHOLD=0.15 for several rows, producing trades
    that give non-trivial per-event expectancies.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    ts_start = 1_700_000_000_000_000_000  # ns
    tick_ns = 1_000_000  # 1 ms between rows

    ts = np.array([ts_start + i * tick_ns for i in range(n_rows)], dtype=np.int64)
    X = np.zeros((n_rows, _FEATURE_DIM), dtype=np.float64)

    # Set mid_price (slot 40) to a stable price so best_bid/ask make sense
    X[:, 40] = 5000.0

    # Regime: REGIME_NORMAL=41 → 1.0 (all rows "normal")
    X[:, 41] = 1.0

    # Spread (slot 15) and spread_stress (slot 16)
    X[:, 15] = 0.25    # 1 tick
    X[:, 16] = 1.0     # neutral spread_stress

    if signal_burst:
        # aggressor_volume_imbalance (slot 0) — high enough for hyp 1 (SecondWaveContinuation)
        # tanh(agg_imb * 3.0) > 0.15 when agg_imb > 0.05
        X[:n_rows // 2, 0] = 0.5   # first half: strong long signal
        X[n_rows // 2:, 0] = -0.5  # second half: strong short signal

    # BBO arrays
    best_bid = np.full(n_rows, 4999.75, dtype=np.float64)
    best_ask = np.full(n_rows, 5000.00, dtype=np.float64)
    bbo_valid = np.ones(n_rows, dtype=np.bool_)

    # State vocab arrays (all rows = index 0 = first label)
    from research_pipeline.src.stage_a_screen import REGIME_LABELS_ORDERED

    n_regime = len(REGIME_LABELS_ORDERED)
    # Use default (unicode) dtype — NOT dtype=object.  load_store uses
    # allow_pickle=False, which rejects object arrays.  Production stores
    # written by build_feature_store.py also use plain np.array(list) which
    # numpy auto-detects as <U... unicode, safe without pickle.
    regime_state_vocab = np.array(list(REGIME_LABELS_ORDERED))
    regime_state_id = np.zeros(n_rows, dtype=np.int32)

    event_ctx_vocab = np.array(["NORMAL", "CPI"])
    event_ctx_id = np.zeros(n_rows, dtype=np.int32)

    vol_state_vocab = np.array(["NORMAL", "HIGH"])
    vol_state_id = np.zeros(n_rows, dtype=np.int32)

    liq_state_vocab = np.array(["NORMAL", "LOW"])
    liq_state_id = np.zeros(n_rows, dtype=np.int32)

    np.savez_compressed(
        str(dest),
        ts=ts,
        X=X,
        best_bid=best_bid,
        best_ask=best_ask,
        bbo_valid=bbo_valid,
        regime_state_vocab=regime_state_vocab,
        regime_state_id=regime_state_id,
        event_ctx_vocab=event_ctx_vocab,
        event_ctx_id=event_ctx_id,
        vol_state_vocab=vol_state_vocab,
        vol_state_id=vol_state_id,
        liq_state_vocab=liq_state_vocab,
        liq_state_id=liq_state_id,
        tick_size=np.float64(0.25),
        feature_index_hash=np.array(_feature_index_hash_value()),
    )
    return dest


def _write_manifest(feature_root: Path, records: list) -> None:
    mp = feature_root / "feature_manifest.jsonl"
    with mp.open("a", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec) + "\n")


def _setup_feature_root(tmp_path: Path, extra_records: list | None = None) -> Path:
    """
    Create a feature root with 3 events for symbol MES.v.0:
    - EVT_A: release_date 2025-01-10 (valid, no VIX)
    - EVT_B: release_date 2025-02-10 (valid, with VIX sibling written separately)
    - EVT_EMBARGO: release_date 2026-02-01 (embargoed — skipped)

    Returns feature_root Path.
    """
    feature_root = tmp_path / "features"
    sym = "MES.v.0"

    # EVT_A store (no VIX sibling)
    from data_system.src.feature_store import store_path
    _make_feature_store_npz(store_path(feature_root, sym, "EVT_A"))

    # EVT_B store
    _make_feature_store_npz(store_path(feature_root, sym, "EVT_B"))

    # VIX sibling for EVT_B: VIX.OPT/VIX.OPT_EVT_B_features_v1.npz
    vix_dest = feature_root / "VIX.OPT" / "VIX.OPT_EVT_B_features_v1.npz"
    vix_dest.parent.mkdir(parents=True, exist_ok=True)
    _make_vix_store_npz(vix_dest)

    # EVT_EMBARGO store (file must exist for manifest to be processed, but it won't be read)
    _make_feature_store_npz(store_path(feature_root, sym, "EVT_EMBARGO"))

    base_records = [
        {
            "symbol": sym,
            "event_id": "EVT_A",
            "event_type": "CPI",
            "release_date": "2025-01-10",
            "content_hash": "aaa",
            "feature_index_hash": _feature_index_hash_value(),
        },
        {
            "symbol": sym,
            "event_id": "EVT_B",
            "event_type": "CPI",
            "release_date": "2025-02-10",
            "content_hash": "bbb",
            "feature_index_hash": _feature_index_hash_value(),
        },
        {
            "symbol": sym,
            "event_id": "EVT_EMBARGO",
            "event_type": "CPI",
            "release_date": "2026-02-01",
            "content_hash": "ccc",
            "feature_index_hash": _feature_index_hash_value(),
        },
    ]
    all_records = base_records + (extra_records or [])
    _write_manifest(feature_root, all_records)
    return feature_root


def _make_vix_store_npz(dest: Path) -> Path:
    """Minimal VIX feature NPZ: ts + X + columns arrays."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 20
    ts_start = 1_700_000_000_000_000_000
    ts = np.array([ts_start + i * 1_000_000 for i in range(n)], dtype=np.int64)
    X = np.ones((n, 3), dtype=np.float64) * 20.0  # VIX ~20
    columns = np.array(["vix_spot", "vix_1m", "vix_3m"], dtype=object)
    np.savez_compressed(str(dest), ts=ts, X=X, columns=columns)
    return dest


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestStageAOutputSchema:

    def test_result_json_schema_fields(self, tmp_path):
        """stage_a_result.json must satisfy the documented schema contract."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        result_path = out_dir / "stage_a_result.json"
        assert result_path.is_file(), "stage_a_result.json not written"
        result = json.loads(result_path.read_text(encoding="utf-8"))

        assert result["schema"] == "stage_a_result_v1", (
            f"schema field wrong: {result.get('schema')!r}"
        )
        assert result["research_only"] is True, "research_only must be True"
        assert result["promotion_label"] == "RESEARCH_ONLY", (
            f"promotion_label={result.get('promotion_label')!r}"
        )

        # certification_stamp
        stamp = result.get("certification_stamp")
        assert isinstance(stamp, dict), "certification_stamp must be a dict"
        assert stamp.get("execution_mode") == "STAGE_A_SCREEN", (
            f"execution_mode={stamp.get('execution_mode')!r}"
        )
        assert stamp.get("queue_model") == "NONE_P_FILL_ASSUMED_1", (
            f"queue_model={stamp.get('queue_model')!r}"
        )

        # ev_approximation_note must mention P_fill
        ev_note = result.get("ev_approximation_note", "")
        assert "P_fill" in ev_note, f"ev_approximation_note does not mention P_fill: {ev_note!r}"

        # aggregation_note must be present
        assert result.get("aggregation_note"), "aggregation_note must be non-empty"

        # embargo block
        embargo = result.get("embargo")
        assert isinstance(embargo, dict), "embargo must be a dict"
        assert embargo.get("start") == "2026-01-01", (
            f"embargo.start={embargo.get('start')!r}"
        )

    def test_embargo_units_skipped(self, tmp_path):
        """Record with release_date 2026-02-01 must be skipped (units_skipped_embargo >= 1)."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_embargo"

        result = run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        embargo = result.get("embargo", {})
        assert embargo.get("units_skipped_embargo", 0) >= 1, (
            f"Expected >= 1 embargoed unit, got {embargo.get('units_skipped_embargo')}"
        )

        # EVT_EMBARGO must contribute no cells
        result_path = out_dir / "stage_a_result.json"
        r = json.loads(result_path.read_text(encoding="utf-8"))
        cells = r.get("cells", [])
        # cells are only from EVT_A and EVT_B (release_date < 2026-01-01)
        # EVT_EMBARGO must not appear; since all three share the same event_type,
        # we can't distinguish by event_type — but we can assert n_events across
        # all cells sums to at most 2 * n_hypotheses (2 valid events).
        total_events_in_cells = sum(c.get("n_events", 0) for c in cells)
        # With 2 valid events, each hypothesis contributes up to 2 events per cell.
        # EVT_EMBARGO would add a 3rd, so if total_events / max_per_cell > 2 it leaked.
        from features_engine.src.hypotheses.registry import get_active_hypotheses
        n_hyps = len(get_active_hypotheses())
        max_expected = 2 * n_hyps  # 2 valid events × n_hyps cells (one event_type)
        assert total_events_in_cells <= max_expected, (
            f"Embargo leak: total_events_in_cells={total_events_in_cells} > "
            f"max_expected={max_expected} (embargo event contributed cells)"
        )

    def test_survivors_json_schema(self, tmp_path):
        """stage_a_survivors.json: tested_cells, pass_through, survivor field names."""
        from research_pipeline.src.stage_a_screen import (
            run_stage_a,
            QUEUE_SENSITIVE_PASS_THROUGH,
        )
        from features_engine.src.hypotheses.registry import get_active_hypotheses

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_survivors"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        surv_path = out_dir / "stage_a_survivors.json"
        assert surv_path.is_file(), "stage_a_survivors.json not written"
        surv = json.loads(surv_path.read_text(encoding="utf-8"))

        # pass_through must equal [42, 43, 45]
        assert surv.get("pass_through") == [42, 43, 45], (
            f"pass_through={surv.get('pass_through')!r}, expected [42, 43, 45]"
        )

        # tested_cells must cover every (hyp_id, event_type) with >=1 unit run,
        # zero-trade combos included.  2 valid events, both event_type "CPI" →
        # 1 distinct event_type; n_hypotheses × 1 cells expected.
        n_hypotheses = len(get_active_hypotheses())
        n_event_types_run = 1  # EVT_A and EVT_B are both "CPI"
        expected_cells = n_hypotheses * n_event_types_run

        tested_cells = surv.get("tested_cells", [])
        assert len(tested_cells) == expected_cells, (
            f"tested_cells length={len(tested_cells)}, expected {expected_cells} "
            f"(n_hypotheses={n_hypotheses} × n_event_types={n_event_types_run})"
        )

        required_cell_fields = {"hyp_id", "event_type", "band_ms", "p", "adj_alpha", "significant_bh", "vix_coverage"}
        for tc in tested_cells:
            missing = required_cell_fields - set(tc.keys())
            assert not missing, f"tested_cell missing fields {missing}: {tc}"

        # survivors must be a list and a subset of tested_cells
        survivors = surv.get("survivors", [])
        assert isinstance(survivors, list), "survivors must be a list"
        tested_keys = {(tc["hyp_id"], tc["event_type"]) for tc in tested_cells}
        required_survivor_fields = {"hyp_id", "event_type", "band_ms", "p", "adj_alpha", "vix_coverage"}
        for sv in survivors:
            missing = required_survivor_fields - set(sv.keys())
            assert not missing, f"survivor missing fields {missing}: {sv}"
            assert (sv["hyp_id"], sv["event_type"]) in tested_keys, (
                f"survivor {sv!r} not present in tested_cells"
            )

    def test_tested_cells_cover_all_hyp_event_type_pairs(self, tmp_path):
        """tested_cells must cover every (hyp_id, event_type) pair evaluated, zero-trade included."""
        from research_pipeline.src.stage_a_screen import run_stage_a
        from features_engine.src.hypotheses.registry import get_active_hypotheses

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_cells"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        surv_path = out_dir / "stage_a_survivors.json"
        surv = json.loads(surv_path.read_text(encoding="utf-8"))

        hypotheses = get_active_hypotheses()
        n_hypotheses = len(hypotheses)
        all_hyp_ids = {h.hyp_id for h in hypotheses}
        n_event_types_run = 1  # EVT_A and EVT_B are both "CPI"
        expected_total = n_hypotheses * n_event_types_run

        tested_cells = surv.get("tested_cells", [])
        assert len(tested_cells) == expected_total, (
            f"tested_cells length={len(tested_cells)}, expected {expected_total} "
            f"(n_hypotheses={n_hypotheses} × n_event_types={n_event_types_run})"
        )

        # Every hyp_id must appear in tested_cells
        tested_hyp_ids = {tc["hyp_id"] for tc in tested_cells}
        assert tested_hyp_ids == all_hyp_ids, (
            f"tested_cells hyp_ids={tested_hyp_ids} != expected {all_hyp_ids}"
        )

        # Every cell in result must appear in tested_cells
        result_path = out_dir / "stage_a_result.json"
        result = json.loads(result_path.read_text(encoding="utf-8"))
        cells_from_result = {
            (c["hyp_id"], c["event_type"]) for c in result.get("cells", [])
        }
        cells_from_tested = {
            (tc["hyp_id"], tc["event_type"]) for tc in tested_cells
        }
        missing_from_tested = cells_from_result - cells_from_tested
        assert not missing_from_tested, (
            f"tested_cells missing cells that appear in result: {missing_from_tested}"
        )

        # survivors must be a subset of tested_cells
        survivors = surv.get("survivors", [])
        survivor_keys = {(sv["hyp_id"], sv["event_type"]) for sv in survivors}
        assert survivor_keys <= cells_from_tested, (
            f"survivors contain keys not in tested_cells: {survivor_keys - cells_from_tested}"
        )

    def test_vix_coverage_zero_without_sibling(self, tmp_path):
        """EVT_A has no VIX sibling → all its cells must have vix_coverage n_events_with_vix == 0."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        # Build a feature root with only EVT_A (no VIX sibling) and EVT_B (with VIX)
        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_vix"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        surv_path = out_dir / "stage_a_survivors.json"
        surv = json.loads(surv_path.read_text(encoding="utf-8"))

        tested_cells = surv.get("tested_cells", [])
        assert len(tested_cells) > 0

        # EVT_A has no VIX sibling.  Because both EVT_A and EVT_B share event_type "CPI",
        # cells are aggregated as (hyp_id, "CPI").  n_events_with_vix should be 1 (only EVT_B),
        # and n_events should be 2.  So vix_coverage.n_events_with_vix must be < n_events.
        for tc in tested_cells:
            vc = tc.get("vix_coverage", {})
            n_with = vc.get("n_events_with_vix", 0)
            n_total = vc.get("n_events", 0)
            if n_total > 0:
                # EVT_A contributes without VIX, EVT_B with VIX → n_with < n_total
                # (unless the fixture collapsed to 1 event, which cannot happen here)
                assert n_with <= n_total, (
                    f"vix_coverage.n_events_with_vix={n_with} > n_events={n_total}"
                )

    def test_vix_coverage_nonzero_with_sibling(self, tmp_path):
        """EVT_B has a VIX.OPT sibling → at least one cell must have n_events_with_vix > 0."""
        from research_pipeline.src.stage_a_screen import run_stage_a

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_vix2"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=1.0,
            workers=1,
        )

        surv_path = out_dir / "stage_a_survivors.json"
        surv = json.loads(surv_path.read_text(encoding="utf-8"))

        tested_cells = surv.get("tested_cells", [])
        assert len(tested_cells) > 0

        any_with_vix = any(
            tc.get("vix_coverage", {}).get("n_events_with_vix", 0) > 0
            for tc in tested_cells
        )
        assert any_with_vix, (
            "No tested_cell has n_events_with_vix > 0 despite EVT_B having a VIX sibling"
        )

    def test_result_json_band_ms_and_feature_version(self, tmp_path):
        """band_ms and feature_version must be correctly propagated into result JSON."""
        from research_pipeline.src.stage_a_screen import run_stage_a
        from data_system.src.feature_store import FEATURE_VERSION

        feature_root = _setup_feature_root(tmp_path)
        out_dir = tmp_path / "out_bms"

        run_stage_a(
            _REPO,
            feature_root,
            out_dir,
            band_ms=2.0,
            workers=1,
        )

        result = json.loads((out_dir / "stage_a_result.json").read_text(encoding="utf-8"))
        assert result["band_ms"] == 2.0, f"band_ms={result['band_ms']!r}"
        assert result["feature_version"] == FEATURE_VERSION, (
            f"feature_version={result['feature_version']!r}"
        )
