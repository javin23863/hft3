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

    run_id = _det_id(npz_path, latency_ms, "LogProbQueueModel2")

    t0 = time.monotonic()
    try:
        hyps = get_active_hypotheses()
        results = run_all_hypotheses_replay(hyps, npz_path, latency_ms=latency_ms)
        hyp_name_map = {h.hyp_id: h.name for h in hyps}
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
) -> dict[str, Any]:
    """Apply Holm and BH corrections per event_type across all (hypothesis, band) cells.

    Returns {event_type: {method: ChampionReport_dict}}
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

    corrections: dict[str, Any] = {}
    gate = MultipleTestingGate(alpha=0.05)

    for etype in sorted(by_etype):
        cells = sorted(by_etype[etype], key=lambda c: (c["hypothesis_id"], c["band"]))
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

        corrections[etype] = {
            "holm": _report_to_dict(holm_report),
            "benjamini_hochberg": _report_to_dict(bh_report),
            "p_value_method": (
                "scipy.stats.ttest_1samp(per_event_expectancies, popmean=0.0), "
                "two-sided; p=1.0 when n_events < 3"
            ),
        }
    return corrections


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
    latency_bands: list[float],
    cli_args: dict[str, Any],
    stamp: dict[str, Any],
    run_start_utc: str,
    run_end_utc: str,
    total_elapsed_s: float,
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
        "skipped": sorted(skipped, key=lambda s: (s["event_id"], s["symbol"], s["latency_ms"])),
        "certification_stamp": stamp,
        "certification_footer": format_stamp_footer(stamp),
        "aggregated": aggregated,
        "corrections": corrections,
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
    args = p.parse_args(argv)

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
    corrections = _apply_corrections(aggregated)

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
        latency_bands=all_bands,
        cli_args=cli_args,
        stamp=stamp,
        run_start_utc=run_start_utc,
        run_end_utc=run_end_utc,
        total_elapsed_s=total_elapsed,
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
