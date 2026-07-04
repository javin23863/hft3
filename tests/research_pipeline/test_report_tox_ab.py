"""Tests for scripts/report_tox_ab.py (PR-1, zero-compute A/B)."""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(_REPO), str(_REPO / "packages"), str(_REPO / "apps")]


def _load():
    script = _REPO / "scripts" / "report_tox_ab.py"
    spec = importlib.util.spec_from_file_location("report_tox_ab", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _envelope(tmp_path: Path) -> Path:
    sets_ = []
    for arm, extra in (("", {}), ("_tox", {"toxicity_max_vpin": 0.8, "toxicity_block_regime": 2})):
        sets_.append({
            "canonical_model_id": "SECOND_WAVE_CONTINUATION",
            "parameter_family": "grid",
            "source_candidate_id": f"model_registry:SECOND_WAVE_CONTINUATION:grid:0{arm}",
            "strategy_params": {"signal_threshold": 0.15, "holding_period_bars": 5, **extra},
        })
    p = tmp_path / "envelope.json"
    p.write_text(json.dumps(sets_), encoding="utf-8")
    return p


def test_paired_diff_and_v1_conditional_label(tmp_path: Path) -> None:
    mod = _load()
    env = _envelope(tmp_path)
    hashes = mod._hash_sets(mod._load_sets(env))
    assert len(hashes) == 2
    (base_h,) = [h for h, v in hashes.items() if not v["is_tox"]]
    (tox_h,) = [h for h, v in hashes.items() if v["is_tox"]]

    runs = tmp_path / "runs.jsonl.gz"
    rows = []
    for h, pnl in ((base_h, -2.0), (base_h, -4.0), (tox_h, 1.0), (tox_h, 3.0)):
        rows.append({"parameter_hash": h, "net_pnl": pnl,
                     "realized_closed_trade_pnl": pnl, "fills_count": 1,
                     "orders_submitted": 1})
    with gzip.open(runs, "wt", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    out = tmp_path / "report.json"
    rc = mod.main(["--envelope", str(env), "--runs-jsonl", str(runs), "--out", str(out)])
    assert rc == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["verdict_scope"] == "expression-v1-conditional"
    assert report["runs_matched"] == 4 and report["runs_unmatched"] == 0
    m = report["models"]["SECOND_WAVE_CONTINUATION"]
    # tox mean (+2) minus base mean (-3) = +5 per run
    assert m["mean_d_net_per_run"] == pytest.approx(5.0)
    assert m["pairs"] == 1


def test_no_match_fails_closed(tmp_path: Path) -> None:
    mod = _load()
    env = _envelope(tmp_path)
    runs = tmp_path / "runs.jsonl.gz"
    with gzip.open(runs, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"parameter_hash": "deadbeef", "net_pnl": 1.0}) + "\n")
    with pytest.raises(SystemExit, match="no_runs_matched"):
        mod.main(["--envelope", str(env), "--runs-jsonl", str(runs),
                  "--out", str(tmp_path / "r.json")])
