#!/usr/bin/env python3
"""Full-universe screening runner.

Runs the active hypothesis matrix against every (event_id, symbol, latency_band)
work unit found in the event lake, aggregates results by hypothesis × event_type ×
band, applies Holm and Benjamini-Hochberg multiple-testing correction, and writes
a universe_result.json + universe_report.md research card.

Aggregation method
------------------
BacktestResult exposes per-event aggregates (expectancy, win_rate, etc.) not
per-trade arrays. Per-event expectancy values are pooled across all events in a
(hypothesis, event_type, band) cell. The cell-level mean/win-rate/adverse-selection
are simple arithmetic means of the per-event values; P5 tail is the 5th-percentile
of per-event expectancies (i.e. worst-event tail, not worst-trade tail — this is
documented in both the JSON and the Markdown card).

p-value derivation
------------------
For each (hypothesis, event_type, band) cell with n_events >= 3, a one-sample
two-sided t-test is run via scipy.stats.ttest_1samp on the vector of per-event
expectancies against null_mean=0.  For n_events < 3 the p-value is set to 1.0
(not enough data to test).  The p-values feed MultipleTestingGate.apply_correction
with method="holm" and method="benjamini_hochberg" separately.

Pool / spawn safety
-------------------
Worker function (_worker) is defined at module top level (picklable).  Only
plain Python scalars, strings, and floats are passed as arguments.  numpy arrays
from the NPZ are not transmitted across the pool boundary; the worker loads them
itself from the path string.  The Pool uses spawn context on all platforms
(explicit via mp.get_context("spawn")) so Windows fork-emulation edge cases are
avoided.  workers=1 forces sequential execution in tests without spawning child
processes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import multiprocessing as mp
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from hft3_bootstrap import setup_repo_paths

setup_repo_paths()

from backtest_pipeline.src.hft_backtest_builder import LATENCY_BANDS_MS
from backtest_pipeline.src.chi404_latency import DEFAULT_CHI404_SUMMARY, resolve_order_ack_ms
from hft3.validation.research_stamp import build_certification_stamp, format_stamp_footer
from replay.replay_clock import deterministic_run_id

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_EVENTS_CSV = _REPO / "packages" / "data_system" / "config" / "events.csv"
DEFAULT_NPZ_DIR = _REPO / "data" / "npz"
DEFAULT_SYMBOL = "MES.v.0"
NPZ_PATTERN = re.compile(r"^(?P<symbol>.+?)_(?P<event_id>.+)_mbo\.npz$")
RESEARCH_EMBARGO_START = "2026-01-01"  # ALPHA_CME.md §4 / DEPLOYMENT.md §4.2: research sweeps must never read data >= this date; first 2026 touch is the M9 paper-shadow bundle.


# ---------------------------------------------------------------------------
# Lake manifest / NPZ discovery
# ---------------------------------------------------------------------------

def _load_manifest(repo_root: Path) -> dict[tuple[str, str], str] | None:
    """Return {(symbol, event_id): npz_path_str} from lake_manifest if available."""
    try:
        from data_system.src.lake_manifest import load_manifest  # type: ignore[import]

        from data_system.src.lake_manifest import resolve_npz_path

        entries = load_manifest(repo_root)
        if entries is None:
            return None
        result: dict[tuple[str, str], str] = {}
        for e in entries:
            result[(e["symbol"], e["event_id"])] = str(
                resolve_npz_path(repo_root, e["npz_path"])
            )
        return result
    except Exception:  # module not yet shipped — fall through to scan
        return None


def _scan_npz_dir(npz_dir: Path) -> dict[tuple[str, str], str]:
    """Fallback: scan data/npz/*.npz and parse {symbol}_{event_id}_mbo.npz names."""
    result: dict[tuple[str, str], str] = {}
    if not npz_dir.is_dir():
        return result
    for f in sorted(npz_dir.glob("*.npz")):
        m = NPZ_PATTERN.match(f.name)
        if m:
            result[(m.group("symbol"), m.group("event_id"))] = str(f)
    return result


def load_lake_index(
    repo_root: Path, *, rescan: bool = False
) -> dict[tuple[str, str], str]:
    """Return {(symbol, event_id): npz_path_str}.

    Tries lake_manifest first; falls back to scanning data/npz/*.npz.
    Pass rescan=True to skip the manifest and force a directory scan.
    """
    from data_system.src.npz_resolver import npz_root

    if not rescan:
        manifest = _load_manifest(repo_root)
        if manifest:
            return manifest
    return _scan_npz_dir(npz_root(repo_root))


# ---------------------------------------------------------------------------
# Work-unit enumeration
# ---------------------------------------------------------------------------

def _read_events_csv(events_csv: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(events_csv, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(dict(row))
    return rows


def _parse_symbols(symbols_str: str) -> list[str]:
    return [s.strip() for s in symbols_str.split(",") if s.strip()]


def build_work_units(
    events_csv: Path,
    lake_index: dict[tuple[str, str], str],
    *,
    latency_bands: list[float],
    event_type_filter: str | None,
    symbol_filter: list[str] | None,
    max_events: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (work_units, skipped_units) sorted deterministically.

    Each work unit: {event_id, symbol, npz_path, latency_ms, event_type, release_date}
    Each skipped unit: {event_id, symbol, latency_ms, reason}
    """
    rows = _read_events_csv(events_csv)
    # stable sort by event_id for determinism
    rows.sort(key=lambda r: r["event_id"])

    work: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    # max_events caps events that actually produce work units — truncating
    # the raw rows first would just select the alphabetically-first events
    # regardless of whether their NPZ exists.
    events_with_work: set[str] = set()

    for row in rows:
        etype = row.get("event_type", "")
        if event_type_filter and etype != event_type_filter:
            continue

        event_id = row["event_id"]
        candidate_symbols = _parse_symbols(row.get("symbols", DEFAULT_SYMBOL))
        if symbol_filter:
            candidate_symbols = [s for s in candidate_symbols if s in symbol_filter]
        if not candidate_symbols:
            candidate_symbols = [DEFAULT_SYMBOL]

        release_date = row.get("release_date", "")
        if release_date >= RESEARCH_EMBARGO_START:
            for symbol in sorted(candidate_symbols):
                for band in sorted(latency_bands):
                    skipped.append({
                        "event_id": event_id,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": "embargo_2026",
                    })
            continue

        has_any_npz = any(
            lake_index.get((symbol, event_id)) is not None
            for symbol in candidate_symbols
        )
        if (
            max_events is not None
            and has_any_npz
            and event_id not in events_with_work
            and len(events_with_work) >= max_events
        ):
            continue

        for symbol in sorted(candidate_symbols):
            npz_path = lake_index.get((symbol, event_id))
            for band in sorted(latency_bands):
                if npz_path is None:
                    skipped.append({
                        "event_id": event_id,
                        "symbol": symbol,
                        "latency_ms": band,
                        "reason": "npz_missing",
                    })
                else:
                    work.append({
                        "event_id": event_id,
                        "symbol": symbol,
                        "npz_path": npz_path,
                        "latency_ms": band,
                        "event_type": etype,
                        "release_date": release_date,
                    })
                    events_with_work.add(event_id)

    return work, skipped


# ---------------------------------------------------------------------------
# VIX feature helpers
# ---------------------------------------------------------------------------

def _vix_feature_path(event_id: str) -> Path | None:
    """Return path to VIX precomputed feature npz if it exists, else None.

    Checks <feature_store_root>/VIX.OPT/VIX.OPT_<event_id>_features_v1.npz.
    Honours HFT3_FEATURE_ROOT env var via feature_store_root().
    Returns None if the feature_store module is absent or the file does not exist.
    """
    try:
        from data_system.src.feature_store import feature_store_root  # type: ignore[import]
        froot = feature_store_root(_REPO)
        candidate = froot / "VIX.OPT" / f"VIX.OPT_{event_id}_features_v1.npz"
        return candidate if candidate.is_file() else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Worker (top-level — picklable for spawn pool)
# ---------------------------------------------------------------------------

def _worker(unit: dict[str, Any]) -> dict[str, Any]:
    """Run full hypothesis matrix for one (event_id, symbol, npz_path, latency_ms).

    Returns a plain dict with all results; no numpy arrays cross the process
    boundary.
    """
    import sys
    from pathlib import Path

    _REPO_W = Path(__file__).resolve().parents[1]
    if str(_REPO_W) not in sys.path:
        sys.path.insert(0, str(_REPO_W))
    from hft3_bootstrap import setup_repo_paths as _setup
    _setup()

    from backtest_pipeline.src.replay_matrix import run_all_hypotheses_replay
    from features_engine.src.hypotheses.registry import get_active_hypotheses
    from replay.replay_clock import deterministic_run_id as _det_id

    event_id: str = unit["event_id"]
    symbol: str = unit["symbol"]
    npz_path: str = unit["npz_path"]
    latency_ms: float = float(unit["latency_ms"])
    hyp_ids_filter: list[int] | None = unit.get("hyp_ids")

    run_id = _det_id(npz_path, latency_ms, "LogProbQueueModel2")

    vix_path = _vix_feature_path(event_id)
    sensor_feature_npz: dict[str, str] | None = (
        {"VIX": str(vix_path)} if vix_path is not None else None
    )

    t0 = time.monotonic()
    try:
        hyps = get_active_hypotheses()
        if hyp_ids_filter is not None:
            hyps = [h for h in hyps if h.hyp_id in hyp_ids_filter]
        results = run_all_hypotheses_replay(
            hyps, npz_path, latency_ms=latency_ms, sensor_feature_npz=sensor_feature_npz
        )
        hyp_name_map = {h.hyp_id: h.name for h in hyps}
        # Derive fee_per_round_trip_usd from FeeModel for R6 fee-stress (post-hoc).
        # Uses the same product resolution as replay_matrix / stage_a_screen.
        # tick_value_usd is needed for per-trade slippage adder arithmetic.
        # Both fields are additive (never present in old records) — stress only
        # applies to runs produced after this commit (rp_v1 → rp_v2 upgrade path).
        from backtest_pipeline.src.fee_model import FeeModel as _FeeModel

        _tick_val_map = {"ES": 12.50, "NQ": 5.00, "MES": 1.25, "MNQ": 0.50}
        _sym_base = symbol.split(".")[0]
        _tick_val_usd = _tick_val_map.get(_sym_base, 1.25)
        _fm = _FeeModel(product=_sym_base if _sym_base in _FeeModel.TICK_VALUES else "MES")
        _fee_per_rt = _fm.get_fee_per_contract() * 2.0  # both legs, 1 contract

        serialized: list[dict[str, Any]] = []
        for hyp_id in sorted(results):
            res = results[hyp_id]
            serialized.append({
                "hypothesis_id": hyp_id,
                "hypothesis_name": hyp_name_map.get(hyp_id, ""),
                "net_pnl_usd": round(float(res.net_pnl), 6),
                "num_trades": int(res.num_trades),
                "win_rate": round(float(res.win_rate), 6),
                "expectancy_usd": round(float(res.expectancy), 6),
                "adverse_selection_ticks": round(float(res.adverse_selection_ticks), 6),
                "tail_loss_usd": round(float(res.tail_loss), 6),
                # R6 fee-stress decomposition fields (additive, rp_v2+):
                # fee_per_round_trip_usd = FeeModel(product, non_member) * 2 legs
                # tick_value_usd = 1 tick move in USD for this product (1 contract)
                # gross_expectancy = expectancy_usd + fee_per_round_trip_usd
                "fee_per_round_trip_usd": round(_fee_per_rt, 6),
                "tick_value_usd": round(_tick_val_usd, 6),
            })
        elapsed = time.monotonic() - t0
        return {
            "run_id": run_id,
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": npz_path,
            "latency_ms": latency_ms,
            "event_type": unit.get("event_type", ""),
            "release_date": unit.get("release_date", ""),
            "elapsed_s": round(elapsed, 3),
            "error": None,
            "hypotheses": serialized,
        }
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - t0
        return {
            "run_id": run_id,
            "event_id": event_id,
            "symbol": symbol,
            "npz_path": npz_path,
            "latency_ms": latency_ms,
            "event_type": unit.get("event_type", ""),
            "release_date": unit.get("release_date", ""),
            "elapsed_s": round(elapsed, 3),
            "error": str(exc),
            "hypotheses": [],
        }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _aggregate_results(
    unit_results: list[dict[str, Any]],
) -> dict[str, dict[str, dict[float, dict[str, Any]]]]:
    """Aggregate per-event results into hypothesis × event_type × band cells.

    Returns nested dict:
      {hypothesis_id_str: {event_type: {latency_ms: cell_dict}}}

    Cell dict keys:
      n_events, total_trades, mean_expectancy, mean_win_rate,
      mean_adverse_selection_ticks, p5_expectancy_tail,
      per_event_expectancies (list[float]) — used for t-test
    """
    # Accumulate per cell
    # Key: (hyp_id, event_type, latency_ms)
    accum: dict[tuple[int, str, float], dict[str, Any]] = {}

    for ur in unit_results:
        if ur.get("error"):
            continue
        etype = ur["event_type"]
        band = float(ur["latency_ms"])
        for hrow in ur.get("hypotheses", []):
            hyp_id = int(hrow["hypothesis_id"])
            key = (hyp_id, etype, band)
            if key not in accum:
                accum[key] = {
                    "hypothesis_id": hyp_id,
                    "hypothesis_name": hrow["hypothesis_name"],
                    "event_type": etype,
                    "latency_ms": band,
                    "n_events": 0,
                    "total_trades": 0,
                    "sum_expectancy": 0.0,
                    "sum_win_rate": 0.0,
                    "sum_adverse_selection": 0.0,
                    "per_event_expectancies": [],
                    "per_event_win_rates": [],
                }
            cell = accum[key]
            cell["n_events"] += 1
            cell["total_trades"] += int(hrow["num_trades"])
            cell["sum_expectancy"] += float(hrow["expectancy_usd"])
            cell["sum_win_rate"] += float(hrow["win_rate"])
            cell["sum_adverse_selection"] += float(hrow["adverse_selection_ticks"])
            cell["per_event_expectancies"].append(float(hrow["expectancy_usd"]))
            cell["per_event_win_rates"].append(float(hrow["win_rate"]))

    # Finalise cells
    finalised: dict[str, dict[str, dict[float, dict[str, Any]]]] = {}
    for (hyp_id, etype, band), cell in sorted(accum.items()):
        n = cell["n_events"]
        expecs = cell["per_event_expectancies"]
        hyp_key = str(hyp_id)
        if hyp_key not in finalised:
            finalised[hyp_key] = {}
        if etype not in finalised[hyp_key]:
            finalised[hyp_key][etype] = {}
        finalised[hyp_key][etype][band] = {
            "hypothesis_id": hyp_id,
            "hypothesis_name": cell["hypothesis_name"],
            "n_events": n,
            "total_trades": cell["total_trades"],
            "mean_expectancy_usd": round(cell["sum_expectancy"] / n, 6) if n else 0.0,
            "mean_win_rate": round(cell["sum_win_rate"] / n, 6) if n else 0.0,
            "mean_adverse_selection_ticks": round(cell["sum_adverse_selection"] / n, 6) if n else 0.0,
            "p5_expectancy_tail_usd": round(float(np.percentile(expecs, 5)), 6) if expecs else 0.0,
            "per_event_expectancies": [round(v, 6) for v in expecs],
            "aggregation_note": (
                "mean/win_rate/adverse_selection are arithmetic means of per-event BacktestResult values; "
                "p5_tail is 5th-percentile of per-event expectancies (worst-event, not worst-trade)"
            ),
        }
    return finalised


# ---------------------------------------------------------------------------
# Multiple-testing correction
# ---------------------------------------------------------------------------

def _derive_p_value(per_event_expectancies: list[float]) -> float:
    """One-sample two-sided t-test on per-event expectancies vs null=0.

    Returns p=1.0 when n < 3 (insufficient data; documented).
    Uses scipy.stats.ttest_1samp identical to MultipleTestingGate.compute_p_value.
    """
    from scipy import stats  # type: ignore[import]

    if len(per_event_expectancies) < 3:
        return 1.0
    arr = np.array(per_event_expectancies, dtype=float)
    _, p_val = stats.ttest_1samp(arr, 0.0)
    return float(np.clip(p_val, 1e-15, 1.0))


def _apply_corrections(
    aggregated: dict[str, dict[str, dict[float, dict[str, Any]]]],
    *,
    stage_a_tested_cells: list[dict[str, Any]] | None = None,
    stage_a_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply Holm and BH corrections per event_type across all (hypothesis, band) cells.

    Returns {event_type: {method: ChampionReport_dict}}

    When stage_a_tested_cells is provided (stage-B selective run), every cell in the
    original stage-A family that was NOT re-run in this sweep receives a placeholder
    HypothesisTestResult(p_value=1.0, metric_value=0.0, num_trades=0) before
    apply_correction.  This keeps Holm n = full original family so adjusted alphas are
    identical to a full run where screened-out cells failed; conservative FWER,
    no multiplicity laundering.
    """
    from decision_engine.python.src.multiple_testing_correction import (  # type: ignore[import]
        HypothesisTestResult,
        MultipleTestingGate,
    )

    # Collect cells per event_type
    by_etype: dict[str, list[dict[str, Any]]] = {}
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                entry = {**cell, "band": band}
                by_etype.setdefault(etype, []).append(entry)

    # Build set of (hyp_id, event_type, band) actually run in this sweep
    rerun_keys: set[tuple[int, str, float]] = set()
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band in band_map:
                rerun_keys.add((int(hyp_key), etype, float(band)))

    # Derive placeholder slugs from stage_a_tested_cells not in rerun_keys
    # tested_cells entries expected: {hyp_id, event_type, band, slug (optional)}
    placeholder_slugs: list[str] = []
    placeholder_results_by_etype: dict[str, list[HypothesisTestResult]] = {}
    # index of (hyp_id, etype) pairs present in rerun_keys (band-agnostic)
    rerun_keys_no_band: set[tuple[int, str]] = {(h, e) for h, e, _ in rerun_keys}
    placeholders_matched = 0
    if stage_a_tested_cells:
        for tc in stage_a_tested_cells:
            tc_hyp = int(tc.get("hyp_id", tc.get("hypothesis_id", 0)))
            tc_etype = str(tc.get("event_type", ""))
            raw_band = tc.get("band_ms", tc.get("band", tc.get("latency_ms")))
            if raw_band is not None:
                tc_band: float | None = float(raw_band)
            else:
                tc_band = None

            if tc_band is not None and (tc_hyp, tc_etype, tc_band) in rerun_keys:
                placeholders_matched += 1
                continue
            if tc_band is None and (tc_hyp, tc_etype) in rerun_keys_no_band:
                placeholders_matched += 1
                continue

            slug = tc.get("slug") or f"hyp_{tc_hyp}_band_{tc_band}"
            placeholder_slugs.append(slug)
            placeholder_results_by_etype.setdefault(tc_etype, []).append(
                # selective-inference guard — adjusted alphas identical to a full
                # run where screened-out cells failed; conservative FWER,
                # no multiplicity laundering.
                HypothesisTestResult(
                    slug=slug,
                    legacy_id=f"HYP_{tc_hyp}",
                    metric_name="mean_expectancy_usd",
                    metric_value=0.0,
                    p_value=1.0,
                    t_statistic=0.0,
                    num_trades=0,
                )
            )

        family_accounted = len(placeholder_slugs) + placeholders_matched
        if family_accounted != len(stage_a_tested_cells):
            print(
                f"WARNING _apply_corrections: family size mismatch — "
                f"tested_cells={len(stage_a_tested_cells)}, "
                f"placeholders={len(placeholder_slugs)}, "
                f"rerun_matched={placeholders_matched}, "
                f"accounted={family_accounted}",
                flush=True,
            )

    if stage_a_filter is not None:
        stage_a_filter["placeholders_added"] = len(placeholder_slugs)

    corrections: dict[str, Any] = {}
    gate = MultipleTestingGate(alpha=0.05)

    # Union of etypes: those we ran + those that only appear in placeholders
    all_etypes = sorted(set(by_etype.keys()) | set(placeholder_results_by_etype.keys()))

    for etype in all_etypes:
        cells = sorted(by_etype.get(etype, []), key=lambda c: (c["hypothesis_id"], c["band"]))
        test_results: list[HypothesisTestResult] = []
        for cell in cells:
            p_val = _derive_p_value(cell["per_event_expectancies"])
            n = cell["n_events"]
            expecs = cell["per_event_expectancies"]
            arr = np.array(expecs, dtype=float)
            t_stat = 0.0
            if len(expecs) >= 2:
                se = float(np.std(arr, ddof=1) / np.sqrt(len(arr)))
                if se >= 1e-15:
                    t_stat = float(np.mean(arr) / se)
            slug = f"hyp_{cell['hypothesis_id']}_band_{cell['band']}"
            test_results.append(HypothesisTestResult(
                slug=slug,
                legacy_id=f"HYP_{cell['hypothesis_id']}",
                metric_name="expectancy",
                metric_value=cell["mean_expectancy_usd"],
                p_value=p_val,
                t_statistic=t_stat,
                num_trades=cell["total_trades"],
            ))
        # Append placeholders for cells not re-run (Holm family honesty)
        test_results.extend(placeholder_results_by_etype.get(etype, []))

        holm_report = gate.apply_correction(test_results, method="holm")
        # BH requires fresh HypothesisTestResult objects (is_significant written in-place)
        test_results_bh: list[HypothesisTestResult] = []
        for cell in cells:
            p_val = _derive_p_value(cell["per_event_expectancies"])
            arr2 = np.array(cell["per_event_expectancies"], dtype=float)
            t2 = 0.0
            if len(cell["per_event_expectancies"]) >= 2:
                se2 = float(np.std(arr2, ddof=1) / np.sqrt(len(arr2)))
                if se2 >= 1e-15:
                    t2 = float(np.mean(arr2) / se2)
            slug2 = f"hyp_{cell['hypothesis_id']}_band_{cell['band']}"
            test_results_bh.append(HypothesisTestResult(
                slug=slug2,
                legacy_id=f"HYP_{cell['hypothesis_id']}",
                metric_name="expectancy",
                metric_value=cell["mean_expectancy_usd"],
                p_value=p_val,
                t_statistic=t2,
                num_trades=cell["total_trades"],
            ))
        test_results_bh.extend(placeholder_results_by_etype.get(etype, []))
        bh_report = gate.apply_correction(test_results_bh, method="benjamini_hochberg")

        def _report_to_dict(rpt: Any) -> dict[str, Any]:
            return {
                "method": rpt.method,
                "original_alpha": rpt.original_alpha,
                "total_tested": rpt.total_tested,
                "passed_slugs": rpt.passed_slugs,
                "failed_slugs": rpt.failed_slugs,
                "sorted_results": [
                    {
                        "slug": r.slug,
                        "legacy_id": r.legacy_id,
                        "p_value": round(r.p_value, 8),
                        "t_statistic": round(r.t_statistic, 6),
                        "adjusted_alpha": round(r.adjusted_alpha, 8),
                        "is_significant": r.is_significant,
                        "num_trades": r.num_trades,
                        "metric_value": round(r.metric_value, 6),
                    }
                    for r in rpt.sorted_results
                ],
            }

        etype_placeholders = [r.slug for r in placeholder_results_by_etype.get(etype, [])]
        corrections[etype] = {
            "holm": _report_to_dict(holm_report),
            "benjamini_hochberg": _report_to_dict(bh_report),
            "p_value_method": (
                "scipy.stats.ttest_1samp(per_event_expectancies, popmean=0.0), "
                "two-sided; p=1.0 when n_events < 3"
            ),
            **({"not_rerun_stage_b": etype_placeholders} if etype_placeholders else {}),
        }
    return corrections


# ---------------------------------------------------------------------------
# Robustness producers (additive block — does NOT alter Holm/BH logic above)
# ---------------------------------------------------------------------------

def _compute_robustness(
    aggregated: dict[str, dict[str, dict[float, dict[str, Any]]]],
    unit_results: list[dict[str, Any]],
    stage_a_tested_cells: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute DSR, CSCV-PBO, and bootstrap CI for all cells in the aggregated pool.

    Called AFTER _apply_corrections, BEFORE write_universe_result.
    Does NOT touch Holm/BH logic, placeholders, or walk-forward sections.

    n_trials convention
    -------------------
    n_trials for DSR = len(stage_a_tested_cells), the FULL family including
    Stage-B placeholders (cells that were in Stage A but not re-run).
    When stage_a_tested_cells is empty (standalone Stage-B run without --from-stage-a),
    n_trials defaults to the count of cells actually aggregated in this run.
    This is conservative: omitting the stage-A family understates the DSR penalty.
    The n_trials value is echoed in every dsr_by_cell entry for audit.

    CSCV matrix
    -----------
    Rows = date blocks formed by splitting unit_results events (sorted by
    release_date, then event_id for ties) into n_splits=8 equal-ish blocks.
    Cols = non-placeholder tested cells (hyp_id × event_type × band triples
    that have per_event_expectancies in the aggregated pool).
    Value = per-block mean expectancy of that cell.
    Cells with no events in a block get NaN; cells with any NaN block are
    excluded from the CSCV matrix (n_excluded is recorded).

    Returns
    -------
    Dict matching the JSON shape:
        {
          "dsr_by_cell":      {slug: {...}},
          "bootstrap_by_cell": {slug: {...}},
          "pbo":              {...},
          "producer_version": "rp_v1",
        }
    """
    try:
        from research_pipeline.src.robustness_producers import (  # type: ignore[import]
            bootstrap_ci,
            cscv_pbo,
            deflated_sharpe_for_cell,
            fee_stress_for_cell,
        )
    except Exception as exc:  # noqa: BLE001
        return {"error": f"robustness_producers import failed: {exc}", "producer_version": "rp_v1"}

    # n_trials = full family including placeholders; fall back to live cell count
    n_trials_total = len(stage_a_tested_cells) if stage_a_tested_cells else None

    # Flatten aggregated cells to a list of (slug, per_event_expectancies)
    # collecting only cells that actually have data (not placeholders)
    cell_data: list[tuple[str, int, str, float, list[float]]] = []
    # cell_data: [(slug, hyp_id, event_type, band, per_event_expectancies)]
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                slug = f"hyp_{cell['hypothesis_id']}_band_{band}"
                expecs = cell.get("per_event_expectancies", [])
                cell_data.append((slug, int(cell["hypothesis_id"]), etype, float(band), expecs))

    # If no stage_a_tested_cells supplied, use count of live cells as n_trials
    n_trials_effective = n_trials_total if n_trials_total is not None else len(cell_data)

    # --- Per-cell DSR, bootstrap, and R6 fee stress ---
    dsr_by_cell: dict[str, Any] = {}
    bootstrap_by_cell: dict[str, Any] = {}
    fee_stress_by_cell: dict[str, Any] = {}

    # Build a lookup: (hyp_id, etype, band) → per-event decomposition lists
    # for R6 fee stress.  Keys added in rp_v2; old records return 0.0 →
    # fee_stress_for_cell detects this and sets stress_data_available=False.
    _fee_decomp: dict[tuple[int, str, float], dict[str, list]] = {}
    for ur in unit_results:
        if ur.get("error"):
            continue
        ur_etype = ur.get("event_type", "")
        ur_band  = float(ur.get("latency_ms", 0.0))
        for hrow in ur.get("hypotheses", []):
            _key = (int(hrow["hypothesis_id"]), ur_etype, ur_band)
            if _key not in _fee_decomp:
                _fee_decomp[_key] = {
                    "n_trades":       [],
                    "fee_per_rt":     [],
                    "tick_value":     [],
                }
            _d = _fee_decomp[_key]
            _d["n_trades"].append(int(hrow.get("num_trades", 0)))
            _d["fee_per_rt"].append(float(hrow.get("fee_per_round_trip_usd", 0.0)))
            _d["tick_value"].append(float(hrow.get("tick_value_usd", 0.0)))

    for slug, hyp_id, etype, band, expecs in cell_data:
        cell_slug = f"{slug}_{etype}"
        dsr_by_cell[cell_slug]       = deflated_sharpe_for_cell(expecs, n_trials=n_trials_effective)
        bootstrap_by_cell[cell_slug] = bootstrap_ci(expecs)

        # R6: collect decomposition arrays for this cell
        _dk = (hyp_id, etype, band)
        _decomp = _fee_decomp.get(_dk, {})
        fee_stress_by_cell[cell_slug] = fee_stress_for_cell(
            per_event_expectancies=expecs,
            per_event_n_trades=_decomp.get("n_trades", []),
            per_event_fee_per_rt=_decomp.get("fee_per_rt", []),
            per_event_tick_value=_decomp.get("tick_value", []),
        )

    # --- CSCV PBO matrix ---
    # Build date-ordered event list from non-errored unit_results
    events_ordered: list[str] = []
    seen_events: set[str] = set()
    for ur in sorted(unit_results, key=lambda u: (u.get("release_date", ""), u["event_id"])):
        if not ur.get("error") and ur["event_id"] not in seen_events:
            events_ordered.append(ur["event_id"])
            seen_events.add(ur["event_id"])

    pbo_result: dict[str, Any]
    N_SPLITS = 8

    if len(events_ordered) < N_SPLITS:
        pbo_result = {
            "pbo": None,
            "n_splits": N_SPLITS,
            "n_configs": len(cell_data),
            "n_partitions": 0,
            "n_excluded": 0,
            "reason": f"insufficient_events_for_cscv: {len(events_ordered)} < {N_SPLITS}",
        }
    else:
        # Assign each event to one of N_SPLITS blocks (as equal as possible)
        block_size = len(events_ordered) / N_SPLITS
        event_to_block: dict[str, int] = {}
        for i, eid in enumerate(events_ordered):
            event_to_block[eid] = min(int(i / block_size), N_SPLITS - 1)

        # Build matrix: rows=blocks, cols=cells
        # cell_data already excludes placeholders (they have no per_event_expectancies)
        n_cells = len(cell_data)
        mat = np.full((N_SPLITS, n_cells), np.nan)

        for col_idx, (slug, hyp_id, etype, band, _) in enumerate(cell_data):
            # Collect per-event expectancies keyed by event_id for this cell
            # We need to look up per-event values from unit_results
            event_expec: dict[str, float] = {}
            for ur in unit_results:
                if ur.get("error") or ur.get("event_type", "") != etype:
                    continue
                for hrow in ur.get("hypotheses", []):
                    if int(hrow["hypothesis_id"]) == hyp_id and float(ur["latency_ms"]) == band:
                        event_expec[ur["event_id"]] = float(hrow["expectancy_usd"])
                        break

            # Accumulate block-level means
            block_sums: dict[int, list[float]] = {b: [] for b in range(N_SPLITS)}
            for eid, val in event_expec.items():
                blk = event_to_block.get(eid)
                if blk is not None:
                    block_sums[blk].append(val)

            for blk in range(N_SPLITS):
                vals = block_sums[blk]
                if vals:
                    mat[blk, col_idx] = float(np.mean(vals))
                # else stays NaN → cell excluded from CSCV

        pbo_result = cscv_pbo(mat, n_splits=N_SPLITS)

    return {
        "dsr_by_cell": dsr_by_cell,
        "bootstrap_by_cell": bootstrap_by_cell,
        "pbo": pbo_result,
        "fee_stress_by_cell": fee_stress_by_cell,
        "producer_version": "rp_v2",
        "producer_version_note": (
            "rp_v2 adds R6 fee/slippage stress (fee_stress_by_cell). "
            "stress_pass = fee_x2_pass is a REPORTED gate consumed at CC5/CC7 "
            "promotion time — does NOT mutate Holm/BH survivor logic. "
            "Records from pre-rp_v2 runs will show stress_data_available=False."
        ),
    }


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------

def _git_commit(repo_root: Path) -> str:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


def write_universe_result(
    out_dir: Path,
    *,
    unit_results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    aggregated: dict[str, Any],
    corrections: dict[str, Any],
    robustness: dict[str, Any] | None = None,
    latency_bands: list[float],
    cli_args: dict[str, Any],
    stamp: dict[str, Any],
    run_start_utc: str,
    run_end_utc: str,
    total_elapsed_s: float,
    stage_a_filter: dict[str, Any] | None = None,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    units_skipped_embargo = sum(1 for s in skipped if s.get("reason") == "embargo_2026")
    payload = {
        "schema": "universe_result_v1",
        "run_start_utc": run_start_utc,
        "run_end_utc": run_end_utc,
        "total_elapsed_s": round(total_elapsed_s, 2),
        "git_commit": _git_commit(_REPO),
        "cli_args": cli_args,
        "latency_bands_ms": sorted(latency_bands),
        "units_run": len(unit_results),
        "units_skipped": len(skipped),
        "units_errored": sum(1 for u in unit_results if u.get("error")),
        "embargo": {
            "start": RESEARCH_EMBARGO_START,
            "units_skipped_embargo": units_skipped_embargo,
        },
        **({"stage_a_filter": stage_a_filter} if stage_a_filter is not None else {}),
        "skipped": sorted(skipped, key=lambda s: (s["event_id"], s["symbol"], s["latency_ms"])),
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
        "aggregated": aggregated,
        "corrections": corrections,
        **({"robustness": robustness} if robustness is not None else {}),
        "unit_results": unit_results,
    }
    path = out_dir / "universe_result.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_universe_report(
    out_dir: Path,
    *,
    unit_results: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    aggregated: dict[str, Any],
    corrections: dict[str, Any],
    latency_bands: list[float],
    stamp: dict[str, Any],
    run_start_utc: str,
    total_elapsed_s: float,
) -> Path:
    lines: list[str] = []

    lines += [
        "# Universe screening report",
        "",
        f"- **run_start_utc:** {run_start_utc}",
        f"- **total_elapsed_s:** {total_elapsed_s:.1f}",
        f"- **latency_bands_ms:** {sorted(latency_bands)}",
        f"- **units_run:** {len(unit_results)}",
        f"- **units_skipped:** {len(skipped)}",
        f"- **units_errored:** {sum(1 for u in unit_results if u.get('error'))}",
        f"- **cert_status:** {stamp.get('status', 'MISSING')}",
        "",
    ]

    # --- Coverage stats ---
    event_types = sorted({u["event_type"] for u in unit_results if not u.get("error")})
    lines += ["## Coverage", "", "| event_type | events_run | events_skipped |", "|---|---|---|"]
    skip_by_etype: dict[str, int] = {}
    for s in skipped:
        # We don't have event_type on skipped entries; count globally
        skip_by_etype["all"] = skip_by_etype.get("all", 0) + 1

    run_by_etype: dict[str, set[str]] = {}
    for u in unit_results:
        if not u.get("error"):
            etype = u["event_type"]
            run_by_etype.setdefault(etype, set()).add(u["event_id"])
    for etype in event_types:
        n_events = len(run_by_etype.get(etype, set()))
        lines.append(f"| {etype} | {n_events} | — |")
    units_skipped_embargo = sum(1 for s in skipped if s.get("reason") == "embargo_2026")
    lines.append(f"\nEmbargoed (>= 2026-01-01): {units_skipped_embargo} units skipped")
    lines.append("")

    # --- Survivors per event_type × band (Holm) ---
    lines += [
        "## Survivors after Holm correction (alpha=0.05)",
        "",
        "_p-value = ttest_1samp(per-event expectancies, 0); n_events >= 3 required_",
        "",
    ]
    for etype in sorted(corrections):
        holm = corrections[etype]["holm"]
        passed = holm["passed_slugs"]
        total = holm["total_tested"]
        lines.append(f"### {etype}")
        lines.append(f"Tested: {total}  |  Survivors: {len(passed)}")
        lines.append("")
        if passed:
            lines += ["| slug | p_value | adj_alpha | t_stat | n_trades | mean_expectancy_usd |",
                      "|---|---|---|---|---|---|"]
            slug_set = set(passed)
            for row in holm["sorted_results"]:
                if row["slug"] in slug_set:
                    lines.append(
                        f"| {row['slug']} | {row['p_value']:.4e} | {row['adjusted_alpha']:.4e} "
                        f"| {row['t_statistic']:.3f} | {row['num_trades']} "
                        f"| {row['metric_value']:.4f} |"
                    )
            lines.append("")
        else:
            lines += ["_No survivors._", ""]

    # --- BH survivors ---
    lines += [
        "## Survivors after Benjamini-Hochberg correction (alpha=0.05)",
        "",
    ]
    for etype in sorted(corrections):
        bh = corrections[etype]["benjamini_hochberg"]
        passed_bh = bh["passed_slugs"]
        lines.append(f"### {etype}")
        lines.append(f"Survivors: {len(passed_bh)}")
        lines.append("")
        if passed_bh:
            lines += ["| slug | p_value | adj_alpha | n_trades |",
                      "|---|---|---|---|"]
            slug_set_bh = set(passed_bh)
            for row in bh["sorted_results"]:
                if row["slug"] in slug_set_bh:
                    lines.append(
                        f"| {row['slug']} | {row['p_value']:.4e} "
                        f"| {row['adjusted_alpha']:.4e} | {row['num_trades']} |"
                    )
            lines.append("")
        else:
            lines += ["_No survivors._", ""]

    # --- Biggest negatives ---
    lines += ["## Biggest negatives (by mean_expectancy_usd, bottom 10)", ""]
    neg_rows: list[dict[str, Any]] = []
    for hyp_key, etype_map in aggregated.items():
        for etype, band_map in etype_map.items():
            for band, cell in band_map.items():
                neg_rows.append({
                    "slug": f"hyp_{cell['hypothesis_id']}_band_{band}_{etype}",
                    "mean_expectancy_usd": cell["mean_expectancy_usd"],
                    "n_events": cell["n_events"],
                    "total_trades": cell["total_trades"],
                })
    neg_rows.sort(key=lambda r: r["mean_expectancy_usd"])
    if neg_rows:
        lines += ["| slug | mean_expectancy_usd | n_events | total_trades |",
                  "|---|---|---|---|"]
        for row in neg_rows[:10]:
            lines.append(
                f"| {row['slug']} | {row['mean_expectancy_usd']:.4f} "
                f"| {row['n_events']} | {row['total_trades']} |"
            )
        lines.append("")

    # --- Skipped ---
    if skipped:
        lines += ["## Skipped work units (NPZ missing)", ""]
        lines += ["| event_id | symbol | latency_ms | reason |",
                  "|---|---|---|---|"]
        for s in sorted(skipped, key=lambda x: (x["event_id"], x["symbol"], x["latency_ms"])):
            lines.append(
                f"| {s['event_id']} | {s['symbol']} | {s['latency_ms']} | {s['reason']} |"
            )
        lines.append("")

    lines += [
        "## Aggregation notes",
        "",
        "- mean_expectancy / mean_win_rate / mean_adverse_selection: arithmetic mean of per-event BacktestResult values",
        "- p5_tail: 5th-percentile of per-event expectancies (worst-event, **not** worst-trade)",
        "- p-values: scipy.stats.ttest_1samp on per-event expectancies vs null=0; p=1.0 when n<3",
        "",
        f"_{format_stamp_footer(stamp)}_",
    ]

    path = out_dir / "universe_report.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _resolve_latency_bands(lane: str, bands_override: str | None) -> tuple[list[float], list[float]]:
    """Return (all_bands, measured_bands).

    measured_bands is non-empty only if chi404 order-ack is available.
    """
    if bands_override:
        base = sorted({float(b.strip()) for b in bands_override.split(",") if b.strip()})
    elif lane == "cme":
        base = list(sorted(set(LATENCY_BANDS_MS)))
    else:
        base = list(sorted(set(LATENCY_BANDS_MS)))

    measured: list[float] = []
    if DEFAULT_CHI404_SUMMARY.is_file():
        try:
            import json as _json
            summary = _json.loads(DEFAULT_CHI404_SUMMARY.read_text(encoding="utf-8"))
            ack_ms, measured_flag, _ = resolve_order_ack_ms(summary)
            if measured_flag and ack_ms is not None:
                measured = [round(float(ack_ms), 6)]
        except Exception:  # noqa: BLE001
            pass

    all_bands = sorted(set(base + measured))
    return all_bands, measured


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Full-universe hypothesis screening runner")
    p.add_argument("--lane", default="cme", choices=("cme",),
                   help="Latency lane; selects default bands from LATENCY_BANDS_MS (default: cme)")
    p.add_argument("--bands", default=None,
                   help='Override latency bands as comma-separated floats e.g. "0.5,1.0"')
    p.add_argument("--event-type", default=None, dest="event_type",
                   help="Filter to a single event_type (e.g. CPI)")
    p.add_argument("--symbols", default=None,
                   help="Comma-separated symbol filter (default: all symbols in events.csv)")
    p.add_argument("--events-csv", type=Path, default=DEFAULT_EVENTS_CSV, dest="events_csv")
    p.add_argument("--out", type=Path, default=None,
                   help="Output dir (default: research_cards/universe_<utcstamp>)")
    p.add_argument("--workers", type=int,
                   default=max(1, (os.cpu_count() or 2) - 2),
                   help="Pool worker count (default: cpu_count-2)")
    p.add_argument("--max-events", type=int, default=None, dest="max_events",
                   help="Limit events processed (smoke runs)")
    p.add_argument("--rescan", action="store_true", default=False,
                   help="Skip manifest cache and scan NPZ dir directly (useful when manifest is stale)")
    p.add_argument("--from-stage-a", default=None, dest="from_stage_a", metavar="SURVIVORS_JSON",
                   help="Path to stage-A survivors JSON. Restricts hypotheses/event_types run.")
    p.add_argument("--cells", default=None,
                   help='Explicit cell override e.g. "46:CPI,12:NFP" (hyp_id:event_type pairs).')
    args = p.parse_args(argv)

    # ---------------------------------------------------------------------------
    # Stage-A filtering: parse survivors + cells; build allowed set
    # ---------------------------------------------------------------------------
    stage_a_filter: dict[str, Any] | None = None
    # allowed_cells: {(hyp_id, event_type)} union of stage-A survivors + pass_through
    allowed_cells: set[tuple[int, str]] | None = None
    # per_etype_hyp_ids: {event_type: set[hyp_id]} for _worker hyp_ids filter
    per_etype_hyp_ids: dict[str, set[int]] | None = None
    # tested_cells from the stage-A report (full family for Holm honesty)
    stage_a_tested_cells: list[dict[str, Any]] = []

    if args.from_stage_a:
        sa_path = Path(args.from_stage_a)
        sa_data = json.loads(sa_path.read_text(encoding="utf-8"))
        survivors: list[dict[str, Any]] = sa_data.get("survivors", [])
        pass_through: list[Any] = sa_data.get("pass_through", [])
        stage_a_tested_cells = sa_data.get("tested_cells", [])

        # Full set of event_types present in the original stage-A family
        tested_etypes: set[str] = {tc["event_type"] for tc in stage_a_tested_cells if "event_type" in tc}
        # Surviving event_types (from survivors list) — used for work-unit restriction below
        surviving_etypes: set[str] = {s["event_type"] for s in survivors if "event_type" in s}

        allowed_cells = set()
        for s in survivors:
            if "hyp_id" in s and "event_type" in s:
                allowed_cells.add((int(s["hyp_id"]), s["event_type"]))
        # pass_through hyps advance for ALL event_types in the original tested family
        for pt in pass_through:
            pt_id = int(pt) if isinstance(pt, (int, str)) else int(pt.get("hyp_id", pt))
            for etype in tested_etypes:
                allowed_cells.add((pt_id, etype))

        # Explicit --cells override adds additional cells
        if args.cells:
            for token in args.cells.split(","):
                token = token.strip()
                if ":" in token:
                    hid_str, et = token.split(":", 1)
                    allowed_cells.add((int(hid_str.strip()), et.strip()))

        # Build per-event_type hyp_id sets for _worker filtering
        per_etype_hyp_ids = {}
        for hyp_id, etype in allowed_cells:
            per_etype_hyp_ids.setdefault(etype, set()).add(hyp_id)

        allowed_cells_count = len(allowed_cells)
        stage_a_filter = {
            "survivors_file": str(sa_path),
            "allowed_cells_count": allowed_cells_count,
            "placeholders_added": 0,  # filled in by _apply_corrections
        }
        print(
            f"Stage-A filter active: {allowed_cells_count} allowed cells from {sa_path.name}",
            flush=True,
        )

    utcstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or (_REPO / "research_cards" / f"universe_{utcstamp}")

    symbol_filter: list[str] | None = None
    if args.symbols:
        symbol_filter = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if not symbol_filter:
        symbol_filter = [DEFAULT_SYMBOL]

    all_bands, measured_bands = _resolve_latency_bands(args.lane, args.bands)
    if measured_bands:
        print(f"CHI404 measured order-ack band(s) added: {measured_bands} ms (tagged 'measured')", flush=True)

    print(f"Loading lake index…", flush=True)
    lake_index = load_lake_index(_REPO, rescan=args.rescan)
    print(f"  lake index entries: {len(lake_index)}", flush=True)

    work_units, skipped = build_work_units(
        args.events_csv,
        lake_index,
        latency_bands=all_bands,
        event_type_filter=args.event_type,
        symbol_filter=symbol_filter,
        max_events=args.max_events,
    )

    # Stage-A: restrict work units to event_types with >=1 allowed cell;
    # stamp each unit with the hyp_ids to run for that event_type.
    if per_etype_hyp_ids is not None:
        allowed_etypes = set(per_etype_hyp_ids.keys())
        work_units = [
            {**u, "hyp_ids": sorted(per_etype_hyp_ids[u["event_type"]])}
            for u in work_units
            if u.get("event_type") in allowed_etypes
        ]

    print(f"Work units: {len(work_units)}  skipped: {len(skipped)}", flush=True)
    if not work_units:
        print("No work units — check --symbols, --event-type, and data/npz/ contents.", flush=True)
        # Still write minimal output so callers can see the skipped list
        stamp = build_certification_stamp(
            execution_mode="UNIVERSE_REPLAY",
            data_version="databento_mbo",
        )
        out_dir.mkdir(parents=True, exist_ok=True)
        _early_embargo = sum(1 for s in skipped if s.get("reason") == "embargo_2026")
        payload = {
            "schema": "universe_result_v1",
            "run_start_utc": utcstamp,
            "run_end_utc": utcstamp,
            "total_elapsed_s": 0.0,
            "git_commit": _git_commit(_REPO),
            "units_run": 0,
            "units_skipped": len(skipped),
            "embargo": {
                "start": RESEARCH_EMBARGO_START,
                "units_skipped_embargo": _early_embargo,
            },
            "skipped": skipped,
            "certification_stamp": stamp,
        }
        (out_dir / "universe_result.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"Wrote {out_dir / 'universe_result.json'}", flush=True)
        return 0

    cli_args = {
        "lane": args.lane,
        "bands_override": args.bands,
        "event_type": args.event_type,
        "symbols": args.symbols,
        "events_csv": str(args.events_csv),
        "workers": args.workers,
        "max_events": args.max_events,
        "from_stage_a": args.from_stage_a,
        "cells": args.cells,
    }

    run_start_utc = datetime.now(timezone.utc).isoformat()
    t_start = time.monotonic()

    if args.workers == 1:
        # Sequential path — avoids spawn overhead in tests and single-core envs
        unit_results: list[dict[str, Any]] = []
        for i, unit in enumerate(work_units, 1):
            print(f"  [{i}/{len(work_units)}] {unit['event_id']} {unit['symbol']} {unit['latency_ms']}ms", flush=True)
            unit_results.append(_worker(unit))
    else:
        ctx = mp.get_context("spawn")
        with ctx.Pool(processes=args.workers) as pool:
            unit_results = []
            for i, result in enumerate(pool.imap_unordered(_worker, work_units), 1):
                status = "ERROR" if result.get("error") else "ok"
                print(
                    f"  [{i}/{len(work_units)}] {result['event_id']} "
                    f"{result['symbol']} {result['latency_ms']}ms "
                    f"elapsed={result['elapsed_s']}s {status}",
                    flush=True,
                )
                unit_results.append(result)

    total_elapsed = time.monotonic() - t_start
    run_end_utc = datetime.now(timezone.utc).isoformat()

    # Sort unit_results for determinism
    unit_results.sort(key=lambda u: (u["event_id"], u["symbol"], float(u["latency_ms"])))

    aggregated = _aggregate_results(unit_results)
    corrections = _apply_corrections(
        aggregated,
        stage_a_tested_cells=stage_a_tested_cells if stage_a_filter else None,
        stage_a_filter=stage_a_filter,
    )

    # --- Robustness producers (additive; does not alter Holm/BH/placeholders) ---
    robustness = _compute_robustness(
        aggregated,
        unit_results=unit_results,
        stage_a_tested_cells=stage_a_tested_cells,
    )

    stamp = build_certification_stamp(
        execution_mode="UNIVERSE_REPLAY",
        data_version="databento_mbo",
        execution_adapter_mode="hftbacktest_simulated_exchange",
        queue_model="LogProbQueueModel2",
        fee_model="FeeModel",
    )

    result_path = write_universe_result(
        out_dir,
        unit_results=unit_results,
        skipped=skipped,
        aggregated=aggregated,
        corrections=corrections,
        robustness=robustness,
        latency_bands=all_bands,
        cli_args=cli_args,
        stamp=stamp,
        run_start_utc=run_start_utc,
        run_end_utc=run_end_utc,
        total_elapsed_s=total_elapsed,
        stage_a_filter=stage_a_filter,
    )
    report_path = write_universe_report(
        out_dir,
        unit_results=unit_results,
        skipped=skipped,
        aggregated=aggregated,
        corrections=corrections,
        latency_bands=all_bands,
        stamp=stamp,
        run_start_utc=run_start_utc,
        total_elapsed_s=total_elapsed,
    )

    n_errored = sum(1 for u in unit_results if u.get("error"))
    print(f"\nDone. units_run={len(unit_results)} errored={n_errored} skipped={len(skipped)}", flush=True)
    print(f"Wrote {result_path}", flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
