"""Fixture tests for the horizon-ensemble builder."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "build_horizon_ensembles", repo / "scripts" / "build_horizon_ensembles.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_fixture_index(path: Path) -> None:
    # 1 family x 3 horizon variants x 12 dated events. Variant h15 is
    # low-variance (should earn the largest weight), h60 high-variance.
    rows = []
    base = {"h15": 10.0, "h30": 8.0, "h60": 6.0}
    noise = {"h15": 0.5, "h30": 2.0, "h60": 6.0}
    signs = [1, -1, 1, 1, -1, 1, -1, 1, 1, -1, 1, 1]
    for month in range(1, 13):
        event_id = f"CPI_2024_{month:02d}_10_TIGHT"
        for variant, holding in (("h15", 15), ("h30", 30), ("h60", 60)):
            pnl = base[variant] + signs[month - 1] * noise[variant]
            rows.append(
                {
                    "canonical_model_id": "SECOND_WAVE_CONTINUATION",
                    "symbol": "MES.v.0",
                    "event_id": event_id,
                    "strategy_params": {"holding_period_bars": holding, "max_round_trips": 1},
                    "realized_closed_trade_pnl": pnl,
                }
            )
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_ensemble_weights_and_receipt(tmp_path: Path) -> None:
    module = _load_module()
    index = tmp_path / "run_index.jsonl"
    _write_fixture_index(index)

    receipts = module.build_family_ensembles(
        index, tmp_path / "out", eval_fraction=0.25, embargo_days=0
    )

    (family,) = receipts["families"].values()
    weights = family["weights"]
    assert abs(sum(weights) - 1.0) < 1e-9
    # inverse variance: low-noise h15 dominates, high-noise h60 smallest
    by_name = dict(zip(family["variants"], weights))
    assert by_name["h15_rt1"] > by_name["h30_rt1"] > by_name["h60_rt1"]
    assert 1.0 < family["effective_breadth"] <= 3.0
    assert family["weights_fitted_on"]["events"] >= 4
    assert family["gate4_status"] in ("pass", "fail")
    assert Path(family["gate4_report"]).is_file()
    receipt_file = tmp_path / "out" / "horizon_ensembles_receipt.json"
    assert receipt_file.is_file()


def test_families_without_enough_variants_skipped(tmp_path: Path) -> None:
    module = _load_module()
    index = tmp_path / "run_index.jsonl"
    rows = [
        {
            "canonical_model_id": "LONE_MODEL",
            "symbol": "MES.v.0",
            "event_id": f"CPI_2024_{m:02d}_10_TIGHT",
            "strategy_params": {"holding_period_bars": 15, "max_round_trips": 1},
            "realized_closed_trade_pnl": 5.0,
        }
        for m in range(1, 13)
    ]
    index.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")

    receipts = module.build_family_ensembles(index, tmp_path / "out")

    assert receipts["families"] == {}
