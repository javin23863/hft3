#!/usr/bin/env python
"""Portfolio netting report: the central-risk-book view across models.

From the run index plus timestamped round trips (stats_summary.json in
each run's artifact dir), reconstruct per-(event, instrument) exposure
across all candidate models and report:

- netting benefit: time-integrated gross vs net contract exposure
  (contract-seconds), the fraction of gross exposure that internal
  crossing removes;
- model correlation matrix on per-event realized PnL over shared events;
- leave-one-out marginal PSR per model on the equal-weight portfolio
  series (reuses research_pipeline.statistics.probabilistic_sharpe_ratio);
- portfolio-level Gate-4 (PSR/DSR/PBO) on the summed per-event series.

The report contextualizes promotion decisions; it never overrides
per-model gates. Rows whose stats_summary.json no longer matches the
indexed sha are skipped and counted (sha_mismatch_rows), trips without
entry/exit timestamps are counted (trips_missing_timestamps) — nothing
silently no-ops.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

SCHEMA_VERSION = "hft3_portfolio_report_v1"
_MIN_SHARED_EVENTS = 3


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _series_moments(values: "np.ndarray") -> tuple[float, float, float, float]:
    import numpy as np

    mean = float(values.mean())
    std = float(values.std(ddof=1))
    if std <= 0.0 or not np.isfinite(std):
        return mean, std, 0.0, 3.0
    centered = values - mean
    skew = float((centered**3).mean() / std**3)
    kurt = float((centered**4).mean() / std**4)
    return mean, std, skew, kurt


def _series_psr(values: "np.ndarray") -> float | None:
    from research_pipeline.statistics import probabilistic_sharpe_ratio

    if len(values) < 2:
        return None
    mean, std, skew, kurt = _series_moments(values)
    if std <= 0.0:
        return None
    return float(
        probabilistic_sharpe_ratio(mean / std, 0.0, len(values), skew, kurt)
    )


def _net_gross_contract_seconds(
    intervals: list[tuple[int, int, float]],
) -> tuple[float, float]:
    """Time-integrate |sum signed qty| (net) and sum |qty| (gross).

    intervals: (entry_ts_ns, exit_ts_ns, signed_quantity) per round trip.
    """
    points = sorted({ts for start, end, _ in intervals for ts in (start, end)})
    net_integral = 0.0
    gross_integral = 0.0
    for left, right in zip(points, points[1:]):
        duration_s = (right - left) / 1e9
        active = [q for start, end, q in intervals if start <= left and end >= right]
        if not active:
            continue
        net_integral += abs(sum(active)) * duration_s
        gross_integral += sum(abs(q) for q in active) * duration_s
    return net_integral, gross_integral


def build_portfolio_report(run_index_path: Path, out_dir: Path) -> dict[str, Any]:
    import numpy as np

    rows: list[dict[str, Any]] = []
    with run_index_path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))

    sha_mismatch_rows = 0
    trips_missing_timestamps = 0
    trips_used = 0
    # model -> {event_id -> realized pnl}; (event, instrument) -> intervals
    model_events: dict[str, dict[str, float]] = {}
    exposures: dict[tuple[str, str], list[tuple[int, int, float]]] = {}
    for row in rows:
        realized = row.get("realized_closed_trade_pnl")
        if not isinstance(realized, (int, float)):
            continue
        model_id = str(row.get("canonical_model_id") or "")
        event_id = str(row.get("event_id") or "")
        instrument = str(row.get("contract") or row.get("symbol") or "")
        if not model_id or not event_id:
            continue
        stats_path = Path(str(row.get("artifact_dir") or "")) / "stats_summary.json"
        if not stats_path.is_file():
            continue
        indexed_sha = str(row.get("stats_summary_sha256") or "")
        if indexed_sha and _sha256_file(stats_path) != indexed_sha:
            sha_mismatch_rows += 1
            continue
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        # Sum realized pnl when one model covers an event via several runs.
        bucket = model_events.setdefault(model_id, {})
        bucket[event_id] = bucket.get(event_id, 0.0) + float(realized)
        direction_of = {"BUY": 1.0, "SELL": -1.0}
        for trip in stats.get("round_trips") or []:
            entry_ts = trip.get("entry_ts_ns")
            exit_ts = trip.get("exit_ts_ns")
            if not isinstance(entry_ts, int) or not isinstance(exit_ts, int):
                trips_missing_timestamps += 1
                continue
            signed = direction_of.get(str(trip.get("side")), 0.0) * float(
                trip.get("closed_quantity") or 0.0
            )
            if signed == 0.0 or exit_ts <= entry_ts:
                trips_missing_timestamps += 1
                continue
            exposures.setdefault((event_id, instrument), []).append(
                (int(entry_ts), int(exit_ts), signed)
            )
            trips_used += 1

    models = sorted(model_events)
    # Pairwise correlation on shared events only.
    correlation: list[list[float | None]] = []
    for a in models:
        corr_row: list[float | None] = []
        for b in models:
            shared = sorted(set(model_events[a]) & set(model_events[b]))
            if len(shared) < _MIN_SHARED_EVENTS:
                corr_row.append(None)
                continue
            xs = np.array([model_events[a][e] for e in shared])
            ys = np.array([model_events[b][e] for e in shared])
            if xs.std() <= 0.0 or ys.std() <= 0.0:
                corr_row.append(None)
                continue
            value = float(np.corrcoef(xs, ys)[0, 1])
            corr_row.append(value if np.isfinite(value) else None)
        correlation.append(corr_row)

    # Netting: integrate exposure per (event, instrument), sum across all.
    net_total = 0.0
    gross_total = 0.0
    for intervals in exposures.values():
        net, gross = _net_gross_contract_seconds(intervals)
        net_total += net
        gross_total += gross
    netting_benefit = (
        (gross_total - net_total) / gross_total if gross_total > 0.0 else None
    )

    # Equal-weight portfolio series: per-event MEAN over models present,
    # so events with richer model coverage carry no extra notional weight.
    def _equal_weight_series(
        event_map: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        sums: dict[str, float] = {}
        counts: dict[str, int] = {}
        for events in event_map.values():
            for event_id, pnl in events.items():
                sums[event_id] = sums.get(event_id, 0.0) + pnl
                counts[event_id] = counts.get(event_id, 0) + 1
        return {e: sums[e] / counts[e] for e in sums}

    portfolio = _equal_weight_series(model_events)
    event_ids = sorted(portfolio)
    portfolio_values = np.array([portfolio[e] for e in event_ids])
    portfolio_psr = _series_psr(portfolio_values) if event_ids else None

    marginal_psr: dict[str, float | None] = {}
    for model_id in models:
        without = _equal_weight_series(
            {m: ev for m, ev in model_events.items() if m != model_id}
        )
        psr_without = _series_psr(np.array([without[e] for e in sorted(without)]))
        if portfolio_psr is None or psr_without is None:
            marginal_psr[model_id] = None
        else:
            marginal_psr[model_id] = portfolio_psr - psr_without

    out_dir.mkdir(parents=True, exist_ok=True)
    gate: dict[str, Any] = {"status": "not_run"}
    if event_ids:
        from backtest_pipeline.src.hbt_only_gates import run_robustness_gate

        gate = run_robustness_gate(
            [
                {"event_id": e, "realized_closed_trade_pnl": portfolio[e]}
                for e in event_ids
            ],
            out_dir=out_dir,
            # CSCV/PBO needs the per-model performance matrix; a model
            # absent from an event held no position there, an honest 0.
            performance_matrix=[
                [model_events[m].get(e, 0.0) for m in models] for e in event_ids
            ],
            # DSR deflated for the number of models composing the book.
            n_trials=len(models),
        )

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_index": str(run_index_path),
        "run_index_sha256": _sha256_file(run_index_path),
        "models": models,
        "model_event_counts": {m: len(model_events[m]) for m in models},
        "correlation_matrix": correlation,
        "netting": {
            "gross_contract_seconds": gross_total,
            "net_contract_seconds": net_total,
            "netting_benefit_fraction": netting_benefit,
            "trips_used": trips_used,
            "trips_missing_timestamps": trips_missing_timestamps,
        },
        "portfolio_events": len(event_ids),
        "portfolio_psr": portfolio_psr,
        "marginal_psr": marginal_psr,
        "gate4_status": gate.get("status"),
        "gate4_psr": gate.get("psr"),
        "gate4_dsr": gate.get("dsr"),
        "sha_mismatch_rows": sha_mismatch_rows,
    }
    (out_dir / "portfolio_report.json").write_text(
        json.dumps(report, indent=1, sort_keys=True), encoding="utf-8"
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-index", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_portfolio_report(args.run_index, args.out_dir)
    print(
        json.dumps(
            {
                "models": len(report["models"]),
                "netting_benefit_fraction": report["netting"][
                    "netting_benefit_fraction"
                ],
                "portfolio_psr": report["portfolio_psr"],
                "gate4_status": report["gate4_status"],
            },
            indent=1,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
