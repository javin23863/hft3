#!/usr/bin/env python3
"""CHI404 paper latency sweep report — percentiles by stage and symbol."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from data_system.rithmic_trial.latency.percentile_stats import stats_by_key, stats_us
from data_system.rithmic_trial.schema.paper_latency_record_v1 import (
    PaperLatencyRecordV1,
    stage_deltas_us,
)

PAPER_ORDER_MIN_PAIRED = 1000


def load_records(path: Path) -> list[PaperLatencyRecordV1]:
    out: list[PaperLatencyRecordV1] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(PaperLatencyRecordV1.from_dict(json.loads(line)))
        except json.JSONDecodeError:
            continue
    return out


def build_report(records: list[PaperLatencyRecordV1], run_id: str) -> dict:
    submit_ack: list[float] = []
    tick_decision: list[float] = []
    decision_submit: list[float] = []
    cancel_submit_ack: list[float] = []
    replace_submit_ack: list[float] = []
    by_symbol_submit_ack: dict[str, list[float]] = {}

    for rec in records:
        d = stage_deltas_us(rec)
        sa = d.get("submit_to_ack_us")
        if sa is not None:
            submit_ack.append(sa)
            by_symbol_submit_ack.setdefault(rec.symbol or "unknown", []).append(sa)
        td = d.get("tick_to_feature_us")
        if td is not None:
            tick_decision.append(td)
        ds = d.get("risk_to_submit_us")
        if ds is not None:
            decision_submit.append(ds)
        if rec.cancel_replace_submit_mono_ns and rec.cancel_replace_ack_mono_ns:
            cancel_submit_ack.append(
                (rec.cancel_replace_ack_mono_ns - rec.cancel_replace_submit_mono_ns) / 1000.0
            )
        if rec.order_type == "replace" and sa is not None:
            replace_submit_ack.append(sa)

    paired = len(submit_ack)
    authoritative = paired >= PAPER_ORDER_MIN_PAIRED
    sa_stats = stats_us(submit_ack)
    p99_ms = (sa_stats.get("p99_us") or 0) / 1000.0 if sa_stats.get("p99_us") else None
    p999_ms = (sa_stats.get("p999_us") or 0) / 1000.0 if sa_stats.get("p999_us") else None

    if authoritative and p99_ms is not None:
        if p99_ms <= 2.0 and (p999_ms is None or p999_ms <= 2.0):
            operating_tier = "tier_2ms"
            backtest_bands = [0.5, 1.0, 2.0]
            promotion = "authoritative_tier_2ms"
        elif p99_ms <= 2.0:
            operating_tier = "tier_2ms_provisional_p999"
            backtest_bands = [0.5, 1.0, 2.0]
            promotion = "provisional_p999_exceeds_2ms"
        else:
            operating_tier = "downgrade_above_2ms_p99"
            backtest_bands = [2.0, 5.0, 10.0]
            promotion = "blocked_p99_exceeds_2ms"
    else:
        operating_tier = "tier_2ms_provisional"
        backtest_bands = [0.5, 1.0, 2.0]
        promotion = f"blocked_until_{PAPER_ORDER_MIN_PAIRED}_pairs"

    return {
        "run_id": run_id,
        "paired_submit_ack_count": paired,
        "authoritative": authoritative,
        "promotion_status": promotion,
        "operating_tier": operating_tier,
        "backtest_latency_bands_ms": backtest_bands,
        "rithmic_tcp_65000_note": "network_health_only — not execution latency",
        "stages": {
            "order_submit_to_ack_us": sa_stats,
            "tick_receive_to_decision_us": stats_us(tick_decision),
            "decision_to_submit_us": stats_us(decision_submit),
            "cancel_submit_to_cancel_ack_us": stats_us(cancel_submit_ack),
            "replace_submit_to_replace_ack_us": stats_us(replace_submit_ack),
        },
        "by_symbol": {
            sym: stats_us(vals) for sym, vals in sorted(by_symbol_submit_ack.items())
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    records = load_records(args.records.resolve())
    report = build_report(records, args.run_id)
    out = args.out or args.records.parent / "sweep_report.json"
    out.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    print(json.dumps(report, indent=2))
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
