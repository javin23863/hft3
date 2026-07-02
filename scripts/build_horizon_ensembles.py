#!/usr/bin/env python
"""Horizon ensembles: combine a family's horizon variants into one series.

For each (model, symbol) family, the run index provides per-event realized
PnL for every horizon variant (holding_period_bars x max_round_trips).
Inverse-variance weights are fitted on TRAINING events only (chronological
event-level split + embargo, same convention as the meta trainer); the
weighted ensemble series is evaluated on post-split events and judged by
the existing Gate-4 machinery (PSR/DSR/PBO). Receipts carry the weights,
the cross-variant correlation matrix, and the effective breadth
((sum w)^2 / sum w^2) so diversification is measured, not asserted.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO), str(REPO / "packages")]

SCHEMA_VERSION = "hft3_horizon_ensemble_v1"
_EVENT_DATE_RE = re.compile(r"(\d{4})_(\d{2})_(\d{2})")
_MIN_VARIANT_EVENTS = 4


def _event_date(event_id: str) -> str:
    match = _EVENT_DATE_RE.search(str(event_id))
    if not match:
        raise ValueError(f"ensemble_event_date_unparseable:{event_id}")
    return "-".join(match.groups())


def _variant_key(strategy_params: dict[str, Any]) -> str:
    holding = strategy_params.get("holding_period_bars", "na")
    trips = strategy_params.get("max_round_trips", 1)
    return f"h{holding}_rt{trips}"


def build_family_ensembles(
    run_index_path: Path,
    out_dir: Path,
    *,
    eval_fraction: float = 0.25,
    embargo_days: int = 2,
) -> dict[str, Any]:
    import numpy as np

    rows: list[dict[str, Any]] = []
    with run_index_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            realized = row.get("realized_closed_trade_pnl")
            if not isinstance(realized, (int, float)):
                continue
            rows.append(row)

    families: dict[tuple[str, str], dict[str, dict[str, float]]] = {}
    for row in rows:
        family = (str(row["canonical_model_id"]), str(row.get("symbol") or ""))
        variant = _variant_key(dict(row.get("strategy_params") or {}))
        event_id = str(row["event_id"])
        families.setdefault(family, {}).setdefault(variant, {})[event_id] = float(
            row["realized_closed_trade_pnl"]
        )

    out_dir.mkdir(parents=True, exist_ok=True)
    receipts: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "run_index": str(run_index_path),
        "eval_fraction": eval_fraction,
        "embargo_days": embargo_days,
        "families": {},
    }
    for (model_id, symbol), variants in sorted(families.items()):
        # variants need shared event coverage; keep those with enough events
        variants = {
            k: v for k, v in variants.items() if len(v) >= _MIN_VARIANT_EVENTS
        }
        if len(variants) < 2:
            continue
        event_ids = sorted(
            set.intersection(*(set(v) for v in variants.values())),
            key=lambda e: (_event_date(e), e),
        )
        if len(event_ids) < 2 * _MIN_VARIANT_EVENTS:
            continue
        dates = [_event_date(e) for e in event_ids]
        unique_dates = sorted(set(dates))
        split_index = max(1, int(len(unique_dates) * (1.0 - eval_fraction)))
        split_date = unique_dates[split_index - 1]
        import pandas as pd

        embargo_end = (
            pd.Timestamp(split_date) + pd.Timedelta(days=embargo_days)
        ).strftime("%Y-%m-%d")
        train_ids = [e for e, d in zip(event_ids, dates) if d <= split_date]
        eval_ids = [e for e, d in zip(event_ids, dates) if d > embargo_end]
        if len(train_ids) < _MIN_VARIANT_EVENTS or len(eval_ids) < 2:
            continue

        names = sorted(variants)
        train_matrix = np.array(
            [[variants[n][e] for n in names] for e in train_ids], dtype=np.float64
        )
        eval_matrix = np.array(
            [[variants[n][e] for n in names] for e in eval_ids], dtype=np.float64
        )
        variances = train_matrix.var(axis=0, ddof=1)
        variances = np.where(variances <= 1e-12, np.nan, variances)
        inv = 1.0 / variances
        inv = np.where(np.isfinite(inv), inv, 0.0)
        if inv.sum() <= 0:
            continue
        weights = inv / inv.sum()
        effective_breadth = float(weights.sum() ** 2 / np.square(weights).sum())
        with np.errstate(invalid="ignore"):
            corr = np.corrcoef(train_matrix, rowvar=False)

        ensemble_eval = eval_matrix @ weights
        ensemble_events = [
            {"event_id": e, "realized_closed_trade_pnl": float(p)}
            for e, p in zip(eval_ids, ensemble_eval)
        ]
        family_key = f"{model_id}__{symbol}"
        gate_dir = out_dir / family_key
        gate_dir.mkdir(parents=True, exist_ok=True)
        from backtest_pipeline.src.hbt_only_gates import run_robustness_gate

        gate = run_robustness_gate(
            ensemble_events,
            out_dir=gate_dir,
            performance_matrix=[[float(v) for v in row] for row in eval_matrix],
        )
        receipts["families"][family_key] = {
            "variants": names,
            "weights": [float(w) for w in weights],
            "weights_fitted_on": {"events": len(train_ids), "through": split_date},
            "embargo_end": embargo_end,
            "effective_breadth": effective_breadth,
            "train_correlation": [
                [float(c) if np.isfinite(c) else None for c in row] for row in corr
            ],
            "eval_events": len(eval_ids),
            "ensemble_eval_mean": float(ensemble_eval.mean()),
            "best_single_eval_mean": float(eval_matrix.mean(axis=0).max()),
            "gate4_status": gate.get("status"),
            "gate4_psr": gate.get("psr"),
            "gate4_dsr": gate.get("dsr"),
            "gate4_report": str(gate_dir / "robustness_report.json"),
        }

    receipt_path = out_dir / "horizon_ensembles_receipt.json"
    receipt_path.write_text(
        json.dumps(receipts, indent=1, sort_keys=True), encoding="utf-8"
    )
    return receipts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-index", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--eval-fraction", type=float, default=0.25)
    parser.add_argument("--embargo-days", type=int, default=2)
    args = parser.parse_args(argv)
    receipts = build_family_ensembles(
        args.run_index,
        args.out_dir,
        eval_fraction=args.eval_fraction,
        embargo_days=args.embargo_days,
    )
    print(json.dumps({k: v["gate4_status"] for k, v in receipts["families"].items()}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
