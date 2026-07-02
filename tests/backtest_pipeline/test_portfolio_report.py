"""Fixture tests for scripts/build_portfolio_report.py (PR-L)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "scripts"), str(REPO / "packages")]

from build_portfolio_report import build_portfolio_report

_T0 = 1_700_000_000_000_000_000


def _write_run(
    root: Path,
    model_id: str,
    event_id: str,
    pnl: float,
    round_trips: list[dict],
) -> dict:
    run_dir = root / f"{model_id}__{event_id}"
    run_dir.mkdir(parents=True)
    stats = {
        "realized_closed_trade_pnl": pnl,
        "round_trips": round_trips,
    }
    stats_path = run_dir / "stats_summary.json"
    stats_path.write_text(json.dumps(stats), encoding="utf-8")
    return {
        "canonical_model_id": model_id,
        "event_id": event_id,
        "symbol": "MES",
        "contract": "MESU6",
        "realized_closed_trade_pnl": pnl,
        "artifact_dir": str(run_dir),
        "stats_summary_sha256": hashlib.sha256(stats_path.read_bytes()).hexdigest(),
    }


def _trip(side: str, qty: float, entry_ts: int, exit_ts: int) -> dict:
    return {
        "side": side,
        "closed_quantity": qty,
        "entry_ts_ns": entry_ts,
        "exit_ts_ns": exit_ts,
    }


def _build_fixture(tmp_path: Path) -> Path:
    rows = []
    events = [f"CPI_2024_01_{10 + i:02d}" for i in range(6)]
    for i, event_id in enumerate(events):
        t0 = _T0 + i * 3_600_000_000_000
        a_trips = [_trip("BUY", 2.0, t0, t0 + 60_000_000_000)]
        if i == 0:
            # One legacy trip without timestamps: counted, never guessed.
            a_trips.append({"side": "BUY", "closed_quantity": 1.0})
        rows.append(
            _write_run(tmp_path / "runs", "model_a", event_id, float(i + 1), a_trips)
        )
        rows.append(
            _write_run(
                tmp_path / "runs",
                "model_b",
                event_id,
                -0.5 * (i + 1),
                [_trip("SELL", 1.0, t0 + 10_000_000_000, t0 + 40_000_000_000)],
            )
        )
    index_path = tmp_path / "run_index.jsonl"
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )
    return index_path


def test_portfolio_report_netting_correlation_marginal_psr(tmp_path):
    index_path = _build_fixture(tmp_path)
    out_dir = tmp_path / "report"
    report = build_portfolio_report(index_path, out_dir)

    assert report["models"] == ["model_a", "model_b"]

    # Per event: A long 2 for 60s, B short 1 for the middle 30s.
    # gross = 2*10 + 3*30 + 2*20 = 150 contract-seconds
    # net   = 2*10 + 1*30 + 2*20 = 90  -> benefit = 60/150 = 0.4
    netting = report["netting"]
    assert abs(netting["netting_benefit_fraction"] - 0.4) < 1e-9
    assert netting["trips_used"] == 12
    assert netting["trips_missing_timestamps"] == 1

    # B is a perfect -0.5x of A on every shared event.
    corr = report["correlation_matrix"]
    assert abs(corr[0][1] - (-1.0)) < 1e-9
    assert abs(corr[0][0] - 1.0) < 1e-9

    # Equal-weight portfolio series 0.25*(i+1) has positive Sharpe;
    # removing either model changes PSR, so marginals are defined.
    assert report["portfolio_psr"] is not None
    assert set(report["marginal_psr"]) == {"model_a", "model_b"}
    assert all(v is not None for v in report["marginal_psr"].values())

    # Gate-4 must actually evaluate (performance matrix supplied), not
    # fail closed on a missing CSCV matrix.
    assert report["gate4_status"] in {"pass", "fail"}
    assert report["gate4_psr"] is not None
    assert report["sha_mismatch_rows"] == 0

    receipt = json.loads((out_dir / "portfolio_report.json").read_text("utf-8"))
    assert receipt["schema_version"] == "hft3_portfolio_report_v1"
    assert receipt["run_index_sha256"]


def test_portfolio_report_skips_and_counts_sha_mismatch(tmp_path):
    index_path = _build_fixture(tmp_path)
    rows = [
        json.loads(line)
        for line in index_path.read_text("utf-8").splitlines()
        if line.strip()
    ]
    # Tamper with one model_b stats file after indexing.
    tampered = Path(rows[1]["artifact_dir"]) / "stats_summary.json"
    stats = json.loads(tampered.read_text("utf-8"))
    stats["realized_closed_trade_pnl"] = 999.0
    tampered.write_text(json.dumps(stats), encoding="utf-8")

    report = build_portfolio_report(index_path, tmp_path / "report2")
    assert report["sha_mismatch_rows"] == 1
    # The tampered event drops out of model_b's series, others remain.
    assert report["model_event_counts"]["model_b"] == 5
